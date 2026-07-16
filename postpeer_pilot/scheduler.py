"""Drop a video into the pipeline -> it lands on the next free plan slot.

Caption resolution for <video>.mp4, in order:
  1. explicit `caption` argument
  2. sidecar file <video>.txt (same folder, same stem)
YouTube title (optional): <video>.title.txt or the `title` argument.

Scheduling only — going live immediately is deliberately NOT offered here; a wrongly
timed scheduled post can be deleted, a live post cannot.
"""
import json
import subprocess
from datetime import date
from pathlib import Path

from . import api, config, plan


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


def _fire_hook(payload: dict) -> dict | None:
    """Run the operator's on_scheduled hook (config) with the schedule result as JSON
    on stdin. Fire-and-report: a failing hook is surfaced in the result but NEVER
    undoes or fails the schedule itself — the post is already placed."""
    cmd = config.load().get("on_scheduled", "")
    if not cmd:
        return None
    try:
        p = subprocess.run(cmd, shell=True, input=json.dumps(payload, ensure_ascii=False),
                           capture_output=True, text=True, timeout=120)
        return {"ok": p.returncode == 0,
                **({"stderr": p.stderr[-300:]} if p.returncode != 0 else {})}
    except Exception as e:
        return {"ok": False, "stderr": f"{type(e).__name__}: {e}"}


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
        result = {"ok": ok, "video": video.name, "slot": slot, "response": r}
        if ok:
            pid = str(r.get("postId") or r.get("id") or "")
            plan.record(slot, video.name, series, pid, caption=cap)
            hook = _fire_hook({"video": str(video), "slot": slot, "post_id": pid,
                               "series": series, "caption": cap, "media_url": url})
            if hook is not None:
                result["hook"] = hook
        else:
            taken.pop(slot, None)          # post failed -> slot is still free
            result["response"] = {**r, "orphaned_upload": url}
        results.append(result)
    return results
