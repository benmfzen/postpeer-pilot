"""Damped, performance-driven plan review.

Idea: the WEEKLY SHAPE of the plan (how many posts on which weekday) should follow the
channel's real performance — but only move on a big, stable delta, never on one viral
outlier. Concretely:

  * Data = published Postpeer posts (when they went out) x latest views per post
    (matched by caption-token overlap against the performance store).
  * Posts younger than `fresh_cutoff_days` don't count (views still growing).
  * The weekly post volume and its distribution shape are KEPT: the current plan's
    per-day counts are re-assigned to weekdays by performance rank. A plan
    [4,4,4,3,3,2,2] stays a [4,4,4,3,3,2,2] — just possibly on different days.
  * A change is only applyable when the long window (default 8 weeks) and the short
    window (default 4 weeks) independently produce the SAME new plan, every weekday
    has >= min_samples posts, and there is actually more history than the short
    window (otherwise the stability check is vacuous).

`propose()` is always safe to call (read-only). `apply()` writes plan.json only when
the damping criteria hold — or when explicitly forced.
"""
import json
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import api, config, perf, plan

WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _published() -> list:
    out = []
    for p in api.list_posts("published"):
        sf = str(p.get("scheduledFor") or p.get("publishedAt") or p.get("createdAt") or "")
        if len(sf) >= 16 and p.get("content"):
            out.append({"when": sf, "content": p["content"]})
    return out


def day_stats(weeks: int, published: list, records: list) -> dict:
    cfg = config.load()
    lo = date.today() - timedelta(weeks=weeks)
    hi = date.today() - timedelta(days=int(cfg["fresh_cutoff_days"]))
    buckets = defaultdict(list)
    for p in published:
        views = perf.match(p["content"], records)
        if views is None:
            continue
        try:
            d = date.fromisoformat(p["when"][:10])
        except ValueError:
            continue
        if lo <= d <= hi:
            buckets[d.weekday()].append(views)
    return {wd: {"avg": round(sum(v) / len(v)), "n": len(v)} for wd, v in buckets.items()}


def _plan_from(stats: dict, counts_shape: list) -> dict | None:
    if len(stats) < 7:
        return None
    ranked = sorted(stats, key=lambda wd: stats[wd]["avg"], reverse=True)
    return {wd: counts_shape[i] for i, wd in enumerate(ranked)}


def propose() -> dict:
    cfg = config.load()
    current = plan.active()["per_day_by_wd"]
    shape = sorted(current.values(), reverse=True)
    published, records = _published(), perf.latest()
    s_long = day_stats(int(cfg["window_weeks_long"]), published, records)
    s_short = day_stats(int(cfg["window_weeks_short"]), published, records)
    p_long = _plan_from(s_long, shape)
    p_short = _plan_from(s_short, shape)
    min_n = int(cfg["min_samples_per_weekday"])
    reasons = []
    if p_long is None:
        reasons.append(f"long window: not all weekdays present ({len(s_long)}/7)")
    else:
        thin = [WD[wd] for wd in range(7) if s_long.get(wd, {}).get("n", 0) < min_n]
        if thin:
            reasons.append(f"long window: fewer than {min_n} posts on {', '.join(thin)}")
    if p_short is None:
        reasons.append(f"short window: not all weekdays present ({len(s_short)}/7)")
    if p_long and p_short and p_long != p_short:
        reasons.append("short and long window disagree — delta not stable enough")
    n_long = sum(v["n"] for v in s_long.values())
    n_short = sum(v["n"] for v in s_short.values())
    if p_long and p_short and n_long <= n_short:
        reasons.append("long window contains the same posts as the short one "
                       "(history shorter than the long window) — stability check vacuous")
    if p_long and p_long == current:
        reasons.append("proposal equals the active plan — nothing to do")
    return {"current": current, "proposal": p_long, "proposal_short_window": p_short,
            "stats_long": {WD[k]: v for k, v in sorted(s_long.items())},
            "stats_short": {WD[k]: v for k, v in sorted(s_short.items())},
            "applyable": not reasons, "reasons": reasons}


def apply(force: bool = False) -> dict:
    p = propose()
    if p["proposal"] is None or (not force and not p["applyable"]):
        return {**p, "applied": False}
    cfg = config.load()
    plan.PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
    plan.PLAN_FILE.write_text(json.dumps({
        "per_day_by_wd": {str(k): v for k, v in p["proposal"].items()},
        "slot_hours": plan.active()["slot_hours"],
        "basis": {"window_weeks": cfg["window_weeks_long"],
                  "updated": datetime.now().isoformat(timespec="seconds"),
                  "stats": p["stats_long"],
                  "forced": bool(force and not p["applyable"])},
    }, indent=1))
    return {**p, "applied": True, "plan_file": str(plan.PLAN_FILE)}
