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
import time
from pathlib import Path

from . import config

BASE = "https://api.postpeer.dev/v1"
RETRIES = 3
BACKOFF_S = 0.8            # 0.8, 1.6, 3.2 — small tool, polite retries


class ApiError(RuntimeError):
    """Permanent API failure (4xx other than 429) — retrying would not help."""


def _run(cmd: list, timeout: int) -> tuple:
    """(curl_exit_code, stdout) — separated for tests to monkeypatch."""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout if p.returncode == 0 or p.stdout else p.stderr


def _req(method: str, path: str, body: dict | None = None) -> dict:
    """Request with retry/backoff. Retryable: network errors, 429, 5xx.
    Permanent: other 4xx -> ApiError immediately (no pointless retries)."""
    cmd = ["curl", "-sS", "-X", method, f"{BASE}{path}",
           "-H", f"x-access-key: {config.api_key()}", "-H", "Content-Type: application/json",
           "-w", "\n%{http_code}"]
    if body is not None:
        cmd += ["-d", json.dumps(body)]
    last = ""
    for attempt in range(RETRIES):
        code, out = _run(cmd, timeout=120)
        raw, _, status_s = out.rpartition("\n")
        status = int(status_s) if status_s.strip().isdigit() else 0
        if code == 0 and 200 <= status < 300:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                raise RuntimeError(f"Postpeer {method} {path}: non-JSON body {raw[:200]!r}")
        if code == 0 and 400 <= status < 500 and status != 429:
            raise ApiError(f"Postpeer {method} {path} -> {status}: {raw[:300]}")
        last = f"status={status or 'network'} {raw[:200]!r}"
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF_S * (2 ** attempt))
    raise RuntimeError(f"Postpeer {method} {path}: still failing after {RETRIES} attempts ({last})")


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
    """Upload via Postpeer's presigned-S3 flow -> public URL. No external hosting needed.
    The PUT retries on network hiccups (S3 PUTs are idempotent — same bytes, same key)."""
    d = _req("POST", "/media/upload", {"filename": mp4.name, "mimeType": "video/mp4"})
    d = d.get("data", d)
    for attempt in range(RETRIES):
        try:
            subprocess.run(["curl", "-sS", "--fail", "-X", "PUT", d["uploadUrl"],
                            "--upload-file", str(mp4), "-H", "Content-Type: video/mp4"],
                           check=True, capture_output=True, text=True, timeout=1800)
            return d["publicUrl"]
        except subprocess.CalledProcessError as e:
            if attempt == RETRIES - 1:
                raise RuntimeError(f"media upload failed after {RETRIES} attempts: "
                                   f"{e.stderr[:200]}") from e
            time.sleep(BACKOFF_S * (2 ** attempt))
    raise AssertionError("unreachable")


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
