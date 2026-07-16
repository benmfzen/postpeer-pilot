"""Damped, performance-driven plan review.

Idea: the WEEKLY SHAPE of the plan (how many posts on which weekday) should follow the
channel's real performance — but only move on a big, stable delta, never on one viral
outlier. Concretely:

  * Data = published Postpeer posts (when they went out) x views per post. Posts this
    tool scheduled are joined by their stable Postpeer id (local ledger); text matching
    against the performance store is exact-first, fuzzy as fallback, and refuses
    ambiguous matches.
  * Metric = MEDIAN views per weekday (robust against the viral outlier by
    construction; a mean would let one hit drag its weekday up for weeks).
  * Posts younger than `fresh_cutoff_days` don't count (views still growing).
  * Two DISJOINT windows must independently produce the same new plan:
      recent window:  the last `window_weeks_short` weeks
      prior window:   the `window_weeks_long - window_weeks_short` weeks before that
    Disjoint means the check cannot be satisfied by the same posts appearing in both
    windows — a channel younger than the long window is simply not applyable yet.
  * Every weekday needs >= `min_samples_per_weekday` posts in each window.
  * The weekly post volume and its distribution shape are KEPT: the current plan's
    per-day counts are re-assigned to weekdays by performance rank. A plan
    [4,4,4,3,3,2,2] stays a [4,4,4,3,3,2,2] — just possibly on different days.

This is a deliberately conservative heuristic, not a statistical proof — see
docs/adr/001-deterministic-planner.md for why the core stays deterministic at all.

`propose()` is always safe to call (read-only). `apply()` writes plan.json only when
the damping criteria hold — or when explicitly forced.
"""
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median

from . import api, config, perf, plan

WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def fetch_published() -> list:
    """[{when, content, post_id}] — captions of ledger-known posts come from the ledger
    (stable-ID join), everyone else keeps the Postpeer content text."""
    ledger = plan.ledger_by_post_id()
    out = []
    for p in api.list_posts("published"):
        sf = str(p.get("scheduledFor") or p.get("publishedAt") or p.get("createdAt") or "")
        pid = str(p.get("postId") or p.get("id") or "")
        content = (ledger.get(pid) or {}).get("caption") or p.get("content") or ""
        if len(sf) >= 16 and content:
            out.append({"when": sf, "content": content, "post_id": pid})
    return out


def day_stats(lo: date, hi: date, published: list, records: list,
              sources: list | None = None) -> dict:
    """{weekday: {'med': int, 'n': int}} for posts with lo <= post-date < hi."""
    buckets = defaultdict(list)
    for p in published:
        try:
            d = date.fromisoformat(p["when"][:10])
        except ValueError:
            continue
        if not (lo <= d < hi):
            continue
        views = perf.total_views(p["content"], records, sources=sources)
        if views is not None:
            buckets[d.weekday()].append(views)
    return {wd: {"med": round(median(v)), "n": len(v)} for wd, v in buckets.items()}


def _plan_from(stats: dict, counts_shape: list) -> dict | None:
    if len(stats) < 7:
        return None
    ranked = sorted(stats, key=lambda wd: stats[wd]["med"], reverse=True)
    return {wd: counts_shape[i] for i, wd in enumerate(ranked)}


def _window_reasons(name: str, stats: dict, p: dict | None, min_n: int) -> list:
    if p is None:
        return [f"{name} window: not all weekdays present ({len(stats)}/7)"]
    thin = [WD[wd] for wd in range(7) if stats.get(wd, {}).get("n", 0) < min_n]
    return [f"{name} window: fewer than {min_n} posts on {', '.join(thin)}"] if thin else []


def propose(as_of: date | None = None, published: list | None = None,
            records: list | None = None, current: dict | None = None) -> dict:
    """Read-only plan review. `as_of`/`published`/`records`/`current` are injectable so
    backtests and tests can replay history; live callers pass nothing."""
    cfg = config.load()
    as_of = as_of or date.today()
    current = current or plan.active()["per_day_by_wd"]
    shape = sorted(current.values(), reverse=True)
    published = fetch_published() if published is None else published
    records = perf.latest() if records is None else records
    sources = cfg.get("planner_sources") or None

    short_w, long_w = int(cfg["window_weeks_short"]), int(cfg["window_weeks_long"])
    fresh = timedelta(days=int(cfg["fresh_cutoff_days"]))
    split = as_of - timedelta(weeks=short_w)
    s_recent = day_stats(split, as_of - fresh, published, records, sources)
    s_prior = day_stats(as_of - timedelta(weeks=long_w), split, published, records, sources)
    p_recent = _plan_from(s_recent, shape)
    p_prior = _plan_from(s_prior, shape)

    min_n = int(cfg["min_samples_per_weekday"])
    reasons = (_window_reasons("recent", s_recent, p_recent, min_n)
               + _window_reasons("prior", s_prior, p_prior, min_n))
    if p_recent and p_prior and p_recent != p_prior:
        reasons.append("recent and prior window disagree — delta not stable enough")
    if p_recent and p_recent == current:
        reasons.append("proposal equals the active plan — nothing to do")
    return {"current": current, "proposal": p_recent, "proposal_prior_window": p_prior,
            "stats_recent": {WD[k]: v for k, v in sorted(s_recent.items())},
            "stats_prior": {WD[k]: v for k, v in sorted(s_prior.items())},
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
        "basis": {"windows_weeks": [cfg["window_weeks_short"], cfg["window_weeks_long"]],
                  "updated": datetime.now().isoformat(timespec="seconds"),
                  "stats_recent": p["stats_recent"], "stats_prior": p["stats_prior"],
                  "forced": bool(force and not p["applyable"])},
    }, indent=1))
    return {**p, "applied": True, "plan_file": str(plan.PLAN_FILE)}
