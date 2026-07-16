"""Configuration for postpeer-pilot.

Everything lives in one config directory (default ~/.config/postpeer-pilot,
override with env POSTPEER_PILOT_HOME):

  config.json       tunables (timezone, default plan, damping, perf sources)
  accounts.json     [{"platform": "tiktok", "accountId": "..."}, ...]
  .env              POSTPEER_API_KEY=... (or set it as a real env var)
  plan.json         the ACTIVE post plan, written by the planner (falls back
                    to config.json defaults when absent)
  performance.jsonl append-only view snapshots (written by perf pulls)
  scheduled.jsonl   local ledger of what this tool scheduled (series caps)
"""
import json
import os
from pathlib import Path

HOME = Path(os.environ.get("POSTPEER_PILOT_HOME", Path.home() / ".config" / "postpeer-pilot"))

DEFAULTS = {
    "timezone": "Europe/Berlin",
    # Post plan defaults, used until the planner has written plan.json.
    # weekday keys: 0=Monday ... 6=Sunday. slot_hours are local times; a day
    # with per_day 2 uses the first 2 slot_hours.
    "per_day_by_wd": {"0": 1, "1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1},
    "slot_hours": ["09:00", "11:00", "13:00", "15:00"],
    # max posts of the same `series` per day (0 = no cap)
    "series_day_cap": 2,
    # planner damping: only change the plan on a big, stable delta.
    # windows are DISJOINT: recent = last `short` weeks, prior = the
    # (`long` - `short`) weeks before that; both must agree independently.
    "window_weeks_long": 8,
    "window_weeks_short": 4,
    "min_samples_per_weekday": 3,
    "fresh_cutoff_days": 7,
    # which performance sources the planner ranks by ([] = all in the store);
    # per-post views are summed across these sources (cross-platform total)
    "planner_sources": [],
    # performance sources
    "tiktok_handle": "",          # enables the yt-dlp puller when set
    "meta": {},                   # {"env": "~/.config/meta/.env", "ig_user_id": "...", "fb_page_id": "..."}
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    f = HOME / "config.json"
    if f.exists():
        cfg.update(json.loads(f.read_text()))
    return cfg


def api_key() -> str:
    if os.environ.get("POSTPEER_API_KEY"):
        return os.environ["POSTPEER_API_KEY"]
    env = HOME / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("POSTPEER_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(f"POSTPEER_API_KEY not set (env var or {env})")


def accounts() -> list:
    f = HOME / "accounts.json"
    if not f.exists():
        raise RuntimeError(
            f"{f} missing. Create it like: "
            '[{"platform":"tiktok","accountId":"..."},{"platform":"youtube","accountId":"..."}] '
            "(account ids: GET /v1/connect/integrations)")
    return json.loads(f.read_text())
