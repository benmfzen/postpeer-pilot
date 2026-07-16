"""Thin Postpeer API client (api.postpeer.dev/v1) — no dependencies beyond curl.

HTTP goes through the curl binary (ships with macOS, Linux and Windows 10+) instead of
urllib: python.org installs often lack SSL root certs (CERTIFICATE_VERIFY_FAILED), and
curl streams multi-hundred-MB video uploads without loading them into memory.

The quirks that bite, captured once:

  * Auth header is `x-access-key: <KEY>` — NOT `Authorization: Bearer`.
  * `scheduledFor` MUST be RFC3339 with milliseconds + Z ("2026-06-27T09:00:00.000Z").
    A plain "…T09:00:00" is rejected with "must match format date-time".
    Together with the `timezone` field, the HH:MM inside the Z-string is treated
    as the LOCAL slot time (house convention — do not convert to UTC yourself).
  * GET /posts caps `limit` HARD at 100. limit >= 101 returns success:false with an
    EMPTY list (not an error!), so always page with offset until `total` is reached.
  * YouTube needs its title as platformSpecificData {"title": ...} and rejects any
    additional property there.
"""
import json
import subprocess
from pathlib import Path

from . import config

BASE = "https://api.postpeer.dev/v1"


def _req(method: str, path: str, body: dict | None = None) -> dict:
    cmd = ["curl", "-sS", "--fail-with-body", "-X", method, f"{BASE}{path}",
           "-H", f"x-access-key: {config.api_key()}", "-H", "Content-Type: application/json"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Postpeer {method} {path}: {p.stdout[:300] or p.stderr[:300]}")


def list_posts(status: str = "scheduled") -> list:
    """All posts with `status` in {scheduled, pending, draft, published} — paginated."""
    out, offset, page_size = [], 0, 100
    while True:
        d = _req("GET", f"/posts?status={status}&limit={page_size}&offset={offset}")
        if not d.get("success"):
            break
        page = d.get("posts", [])
        out.extend(page)
        offset += len(page)
        if not page or len(page) < page_size or offset >= (d.get("total") or 0):
            break
    return out


def upload_media(mp4: Path) -> str:
    """Upload via Postpeer's presigned-S3 flow -> public URL. No external hosting needed."""
    d = _req("POST", "/media/upload", {"filename": mp4.name, "mimeType": "video/mp4"})
    d = d.get("data", d)
    subprocess.run(["curl", "-sS", "--fail", "-X", "PUT", d["uploadUrl"],
                    "--upload-file", str(mp4), "-H", "Content-Type: video/mp4"],
                   check=True, capture_output=True, text=True, timeout=1800)
    return d["publicUrl"]


def scheduled_for(local_iso: str) -> str:
    """'2026-06-28T09:00' -> '2026-06-28T09:00:00.000Z' (house convention, see module doc)."""
    s = local_iso.strip()
    if len(s) == 16:
        s += ":00"
    return s + ".000Z" if not s.endswith("Z") else s


def create_post(content: str, video_url: str, when_local: str | None,
                yt_title: str | None = None) -> dict:
    """Create a post on all configured accounts. when_local=None means post NOW (live)."""
    platforms = []
    for a in config.accounts():
        p = {"platform": a["platform"], "accountId": a["accountId"]}
        if a["platform"] == "youtube" and yt_title:
            p["platformSpecificData"] = {"title": yt_title[:100]}
        platforms.append(p)
    body = {"content": content,
            "mediaItems": [{"url": video_url, "type": "video"}],
            "platforms": platforms,
            "timezone": config.load()["timezone"]}
    if when_local:
        body["scheduledFor"] = scheduled_for(when_local)
    return _req("POST", "/posts", body)
