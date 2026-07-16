"""In-process fake Postpeer — powers offline demos, integration tests and agent evals.

Activate by setting POSTPEER_PILOT_FAKE=<path/to/state.json> before starting server.py
(or call install() directly). All api.* calls are served from the JSON state file, so:

  * nothing ever touches the network,
  * an external grader can assert side effects after a run by reading the file,
  * failures are injectable ({"fail_creates": N} fails the first N create_post calls).

State shape:
  {"scheduled": [post...], "published": [post...], "uploads": 0,
   "creates": 0, "fail_creates": 0}
"""
import json
from pathlib import Path

from . import api

_STATE_PATH: Path | None = None


def _load() -> dict:
    if _STATE_PATH.exists():
        return json.loads(_STATE_PATH.read_text())
    return {"scheduled": [], "published": [], "uploads": 0, "creates": 0, "fail_creates": 0}


def _save(s: dict):
    _STATE_PATH.write_text(json.dumps(s, indent=1, ensure_ascii=False))


def _list_posts(status: str = "scheduled") -> list:
    return list(_load().get(status, []))


def _upload_media(mp4: Path) -> str:
    s = _load()
    s["uploads"] += 1
    _save(s)
    return f"https://fake.postpeer.invalid/media/{mp4.name}"


def _create_post(content: str, video_url: str, when_local: str | None,
                 yt_title: str | None = None) -> dict:
    s = _load()
    s["creates"] += 1
    if when_local is None:
        # The real tool layer never does this (scheduling only); recorded loudly so a
        # grader can flag any code path that tries.
        s.setdefault("live_attempts", 0)
        s["live_attempts"] += 1
        _save(s)
        return {"success": False, "error": "live posting attempted (should never happen)"}
    if s["creates"] <= s.get("fail_creates", 0):
        _save(s)
        return {"success": False, "error": "injected failure"}
    pid = f"fake{s['creates']}"
    s["scheduled"].append({"postId": pid, "content": content,
                           "scheduledFor": api.scheduled_for(when_local),
                           "mediaUrl": video_url, "ytTitle": yt_title})
    _save(s)
    return {"success": True, "postId": pid}


def install(state_path: str):
    """Replace the api module's network functions with the fake, state-backed ones."""
    global _STATE_PATH
    _STATE_PATH = Path(state_path).expanduser()
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _STATE_PATH.exists():
        _save(_load())
    api.list_posts = _list_posts
    api.upload_media = _upload_media
    api.create_post = _create_post
