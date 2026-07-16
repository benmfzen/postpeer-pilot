"""The post plan: how many posts on which weekday, at which local slot times.

The plan is data-driven but sticky: plan.json (written by planner.apply) overrides
the config defaults; nothing here changes on its own.
"""
import json
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import api, config

PLAN_FILE = config.HOME / "plan.json"
LEDGER = config.HOME / "scheduled.jsonl"


def active() -> dict:
    """{'per_day_by_wd': {int: int}, 'slot_hours': [...], 'series_day_cap': int, 'source': str}."""
    cfg = config.load()
    plan = {"per_day_by_wd": {int(k): int(v) for k, v in cfg["per_day_by_wd"].items()},
            "slot_hours": list(cfg["slot_hours"]),
            "series_day_cap": int(cfg["series_day_cap"]), "source": "config defaults"}
    if PLAN_FILE.exists():
        p = json.loads(PLAN_FILE.read_text())
        plan.update({"per_day_by_wd": {int(k): int(v) for k, v in p["per_day_by_wd"].items()},
                     "slot_hours": list(p.get("slot_hours", plan["slot_hours"])),
                     "source": "plan.json"})
    return plan


def slots_for_day(d: date, plan: dict | None = None) -> list:
    plan = plan or active()
    n = plan["per_day_by_wd"].get(d.weekday(), 0)
    return [f"{d.isoformat()}T{hh}" for hh in plan["slot_hours"][:n]]


def occupied() -> dict:
    """{'YYYY-MM-DD': {'HH:MM', ...}} from the live Postpeer schedule."""
    occ = defaultdict(set)
    for p in api.list_posts("scheduled"):
        sf = str(p.get("scheduledFor") or "")
        if len(sf) >= 16:
            occ[sf[:10]].add(sf[11:16])
    return occ


def _series_by_day(exclude_slots: set | None = None) -> dict:
    """{'YYYY-MM-DD': [series, ...]} from the local ledger (only posts this tool scheduled).
    `exclude_slots` drops entries the caller already counts itself (taken_extra) — a slot
    handed out AND ledger-recorded in the same run must not count twice toward the cap."""
    out = defaultdict(list)
    exclude_slots = exclude_slots or set()
    if LEDGER.exists():
        for ln in LEDGER.read_text().splitlines():
            try:
                r = json.loads(ln)
                if r.get("series") and r["when"] not in exclude_slots:
                    out[r["when"][:10]].append(r["series"])
            except (json.JSONDecodeError, KeyError):
                pass
    return out


def record(when: str, video: str, series: str | None, post_id: str = "",
           caption: str = ""):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"when": when, "video": video, "series": series,
                            "post_id": post_id, "caption": caption},
                           ensure_ascii=False) + "\n")


def ledger_entries() -> list:
    if not LEDGER.exists():
        return []
    out = []
    for ln in LEDGER.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out


def ledger_by_post_id() -> dict:
    """{post_id: entry} for everything this tool scheduled — the stable-ID side of
    performance matching: a published post whose id is in here needs no fuzzy text
    match to know its canonical caption."""
    out = {}
    if LEDGER.exists():
        for ln in LEDGER.read_text().splitlines():
            try:
                r = json.loads(ln)
                if r.get("post_id"):
                    out[r["post_id"]] = r
            except json.JSONDecodeError:
                pass
    return out


def free_slots(count: int = 1, start: date | None = None, series: str | None = None,
               taken_extra: dict | None = None) -> list:
    """The next `count` FREE plan slots ('YYYY-MM-DDThh:mm', local): the day's plan slots
    minus live-occupied ones (Postpeer) minus `taken_extra` ({slot: series} handed out
    earlier in the same run). With `series`, at most `series_day_cap` posts of the same
    series land on one day."""
    plan = active()
    occ = occupied()
    cap = plan["series_day_cap"]
    taken_extra = taken_extra or {}
    series_days = _series_by_day(exclude_slots=set(taken_extra)) if series and cap else {}
    extra = defaultdict(int)
    for s, sr in taken_extra.items():
        if series and sr == series:
            extra[s[:10]] += 1
    d = start or date.today()
    now = datetime.now()
    out = []
    while len(out) < count:
        used = (series_days.get(d.isoformat(), []).count(series) + extra[d.isoformat()]) \
            if series and cap else 0
        for s in slots_for_day(d, plan):
            if len(out) >= count:
                break
            if datetime.fromisoformat(s) <= now:
                continue
            if s[11:16] in occ.get(s[:10], set()) or s in taken_extra:
                continue
            if series and cap and used >= cap:
                break
            out.append(s)
            used += 1
        d += timedelta(days=1)
        if (d - (start or date.today())).days > 365:
            raise RuntimeError("free_slots: no free slot within a year — check plan/occupancy.")
    return out
