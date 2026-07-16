"""Drop a video into the pipeline -> it lands on the next free plan slot.

Caption resolution for <video>.mp4, in order:
  1. explicit `caption` argument
  2. sidecar file <video>.txt (same folder, same stem)
YouTube title (optional): <video>.title.txt or the `title` argument.

Scheduling only — going live immediately is deliberately NOT offered here; a wrongly
timed scheduled post can be deleted, a live post cannot.
"""
from datetime import date
from pathlib import Path

from . import api, plan


def _caption(video: Path, caption: str | None) -> str:
    if caption:
        return caption
    sidecar = video.with_suffix(".txt")
    if sidecar.exists() and sidecar.read_text().strip():
        return sidecar.read_text().strip()
    raise RuntimeError(f"no caption: pass one explicitly or create {sidecar.name}")


def _title(video: Path, title: str | None) -> str | None:
    if title:
        return title
    sidecar = video.with_suffix(".title.txt")
    return sidecar.read_text().strip() if sidecar.exists() else None


def _already_scheduled(video: Path) -> str | None:
    """Idempotency guard: has THIS file already been scheduled into a future slot?
    Retrying a batch after a partial failure must not double-post the successes."""
    today = date.today().isoformat()
    for e in plan.ledger_entries():
        if e.get("video") == video.name and e.get("when", "")[:10] >= today:
            return e["when"]
    return None


def schedule(videos: list, captions: list | None = None, titles: list | None = None,
             series: str | None = None, dry_run: bool = False,
             start: date | None = None, allow_duplicate: bool = False) -> list:
    """Schedule each video on the next free plan slot. Slots handed out earlier in the
    same run are respected, as is the per-day series cap. Re-running a batch skips
    videos that already sit in a future slot (see _already_scheduled)."""
    taken: dict = {}
    results = []
    for i, v in enumerate(videos):
        video = Path(v).expanduser()
        if not video.is_file():
            results.append({"ok": False, "video": str(v), "error": "file not found"})
            continue
        if not allow_duplicate and not dry_run and (dup := _already_scheduled(video)):
            results.append({"ok": False, "video": video.name, "skipped": True,
                            "error": f"already scheduled for {dup} — "
                                     "pass allow_duplicate to post it again"})
            continue
        cap = _caption(video, captions[i] if captions else None)
        slot = plan.free_slots(1, start=start, series=series, taken_extra=taken)[0]
        taken[slot] = series
        if dry_run:
            results.append({"ok": True, "dry_run": True, "video": video.name, "slot": slot})
            continue
        url = api.upload_media(video)
        try:
            r = api.create_post(cap, url, slot,
                                yt_title=_title(video, titles[i] if titles else None))
        except Exception as e:
            # Upload succeeded but the post didn't: surface the orphaned media URL so it
            # can be reused (pass it to a retry) instead of silently rotting in storage.
            taken.pop(slot, None)
            results.append({"ok": False, "video": video.name, "slot": slot,
                            "orphaned_upload": url, "error": f"{type(e).__name__}: {e}"})
            continue
        ok = bool(r.get("id") or r.get("success") or r.get("postId"))
        if ok:
            plan.record(slot, video.name, series,
                        str(r.get("postId") or r.get("id") or ""), caption=cap)
        else:
            taken.pop(slot, None)          # post failed -> slot is still free
            r = {**r, "orphaned_upload": url}
        results.append({"ok": ok, "video": video.name, "slot": slot, "response": r})
    return results
