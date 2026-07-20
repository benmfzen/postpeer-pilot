"""Performance store + pullers.

Store: performance.jsonl in the config dir, append-only snapshots:
  {"ts": ..., "title": <caption text>, "views": int, "source": "tiktok"|"instagram"|"facebook"|"manual"}

Pullers (all optional, enabled via config):
  * tiktok  — yt-dlp scrape of the profile page (TikTok has no usable public API).
  * meta    — official Meta Graph API for Instagram + Facebook reels.
  * import_file — manual drop for anything else: CSV (title,views) or JSONL.

Posts are matched to snapshots later by caption-token overlap (see match()), so the
store needs no IDs — just the caption/title text a human would recognize the post by.
"""
import csv
import json
import re
import subprocess
import urllib.parse

from datetime import datetime, timezone
from pathlib import Path

from . import config, safe_read

STORE = config.HOME / "performance.jsonl"

_STOP = set("the a an and or of to in on for with your you this that it its is are be as at "
            "from how why what when who".split())


def tokens(s: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2 and t not in _STOP}


def _append(rows: list):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with STORE.open("a") as f:
        for r in rows:
            f.write(json.dumps({"ts": ts, **r}, ensure_ascii=False) + "\n")


def latest() -> list:
    """Latest snapshot per (source, title): [{'title', 'views', 'source'}]."""
    best = {}
    for ln in safe_read.read_lines_if_present(STORE):
        try:
            r = json.loads(ln)
            best[(r.get("source"), r["title"])] = r    # append-only: later line wins
        except (json.JSONDecodeError, KeyError):
            pass
    return list(best.values())


def match(caption: str, records: list, min_overlap: int = 4) -> int | None:
    """Views for a post caption within ONE source's records.

    Matching ladder (IDs beat text, exact text beats fuzzy text):
      1. exact title == caption
      2. token-overlap fuzzy match — but only when the winner is UNAMBIGUOUS:
         two different titles sharing the top overlap score -> None (better no
         data point than a wrong one; the planner just skips this post).
    """
    cap = caption.strip()
    exact = [r["views"] for r in records if r["title"].strip() == cap]
    if exact:
        return max(exact)
    ct = tokens(caption)
    scored = sorted(((len(ct & tokens(r["title"])), r["title"], r["views"]) for r in records),
                    reverse=True)
    if not scored or scored[0][0] < min_overlap:
        return None
    top = [s for s in scored if s[0] == scored[0][0]]
    if len({t[1] for t in top}) > 1:
        return None                        # ambiguous — refuse to guess
    return top[0][2]


def total_views(caption: str, records: list, sources: list | None = None,
                min_overlap: int = 4) -> int | None:
    """Cross-platform total for one post: match per source, sum what matched.
    `sources` restricts which platforms count (empty/None = all in the store).
    Summing per-source keeps weekday comparisons fair — every post is measured
    across the same set of platforms instead of whichever source matched first."""
    by_source: dict = {}
    for r in records:
        if sources and r.get("source") not in sources:
            continue
        by_source.setdefault(r.get("source"), []).append(r)
    hits = [v for recs in by_source.values()
            if (v := match(caption, recs, min_overlap)) is not None]
    return sum(hits) if hits else None


# ── pullers ──────────────────────────────────────────────────────────────────

def pull_tiktok(limit: int = 150) -> int:
    """View counts of the last `limit` videos on the configured profile (yt-dlp flat playlist)."""
    handle = config.load()["tiktok_handle"]
    if not handle:
        return 0
    p = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--playlist-end", str(limit),
         "--print", "%(view_count)s\t%(title)s", f"https://www.tiktok.com/@{handle}"],
        capture_output=True, text=True, timeout=300)
    rows = []
    for ln in p.stdout.splitlines():
        v, _, title = ln.partition("\t")
        if v.isdigit() and title.strip():
            rows.append({"title": title.strip(), "views": int(v), "source": "tiktok"})
    _append(rows)
    return len(rows)


def _graph(path: str, token: str, **params) -> dict:
    params.setdefault("access_token", token)
    url = f"https://graph.facebook.com/v25.0/{path}?{urllib.parse.urlencode(params)}"
    p = subprocess.run(["curl", "-sS", url], capture_output=True, text=True, timeout=60)
    return json.loads(p.stdout)


def pull_meta(limit: int = 50) -> int:
    """Instagram + Facebook reel views via the Meta Graph API (official)."""
    m = config.load()["meta"]
    if not m:
        return 0
    token = None
    env = Path(m.get("env", "")).expanduser()
    for ln in safe_read.read_lines_if_present(env):
            if ln.startswith("META_PAGE_TOKEN="):
                token = ln.split("=", 1)[1].strip()
    if not token:
        raise RuntimeError("META_PAGE_TOKEN not found (config.meta.env)")
    rows = []
    if m.get("ig_user_id"):
        d = _graph(f"{m['ig_user_id']}/media", token, limit=limit,
                   fields="caption,insights.metric(views)")
        for it in d.get("data", []):
            ins = {i["name"]: (i.get("values") or [{}])[0].get("value", 0)
                   for i in (it.get("insights") or {}).get("data", [])}
            if it.get("caption"):
                rows.append({"title": it["caption"], "views": int(ins.get("views", 0) or 0),
                             "source": "instagram"})
    if m.get("fb_page_id"):
        d = _graph(f"{m['fb_page_id']}/video_reels", token, limit=limit,
                   fields="description,video_insights.metric(fb_reels_total_plays)")
        for it in d.get("data", []):
            ins = {i["name"]: (i.get("values") or [{}])[0].get("value", 0)
                   for i in (it.get("video_insights") or {}).get("data", [])}
            if it.get("description"):
                rows.append({"title": it["description"],
                             "views": int(ins.get("fb_reels_total_plays", 0) or 0),
                             "source": "facebook"})
    _append(rows)
    return len(rows)


def import_file(path: str) -> int:
    """Manual drop: .csv with title,views columns, or .jsonl with {title, views} lines."""
    f = Path(path).expanduser()
    rows = []
    if f.suffix == ".csv":
        for r in csv.DictReader(f.open()):
            rows.append({"title": r["title"], "views": int(r["views"]), "source": "manual"})
    else:
        for ln in f.read_text().splitlines():
            if ln.strip():
                r = json.loads(ln)
                rows.append({"title": r["title"], "views": int(r["views"]), "source": "manual"})
    _append(rows)
    return len(rows)
