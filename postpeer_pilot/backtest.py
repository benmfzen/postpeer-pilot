"""Historical backtest for the damped planner.

Replays history week by week: at each Monday, propose() sees ONLY the posts published
before that date (no lookahead into its own input), and the plan evolves exactly as the
live system would have evolved it. The realized performance of the following weeks then
scores every strategy.

Strategies compared:
  adaptive        the damped planner (what this repo ships)
  unchanged       the initial plan, never touched
  short_only      re-rank every week on the recent window alone — no damping
  random_expected analytic expectation of allocating the same weekly volume randomly

Metrics:
  plan_churn             plan changes per replayed week (lower = calmer)
  false_adaptation_rate  share of changes reverted to the previous plan within
                         `revert_weeks` (a high value means the damping is too loose)
  match_rate             share of published posts with usable view data
  scores                 sum over weeks of  posts_on_day x realized median views of
                         that weekday in the following `lookahead_weeks` — i.e. how
                         much attention the allocation captured

Caveat (by design, documented): views are FINAL counts, not the counts as they stood
in the replayed week — early-window snapshots are not available retroactively. This
biases all strategies equally, so the comparison stays fair, but absolute numbers are
proxies. Run `python3 -m postpeer_pilot.backtest --demo` for a self-contained example.
"""
import argparse
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from . import config, perf, planner

LOOKAHEAD_WEEKS = 4
REVERT_WEEKS = 4


def _mondays(published: list) -> list:
    days = sorted({date.fromisoformat(p["when"][:10]) for p in published})
    cfg = config.load()
    first = days[0] + timedelta(weeks=int(cfg["window_weeks_long"]))
    last = days[-1] - timedelta(weeks=LOOKAHEAD_WEEKS)
    out, d = [], first - timedelta(days=first.weekday())
    while d <= last:
        out.append(d)
        d += timedelta(weeks=1)
    return out


def _realized(as_of: date, published: list, records: list, sources) -> dict:
    """Median views per weekday over [as_of, as_of + LOOKAHEAD_WEEKS) — the future the
    replayed decision was trying to allocate posts into."""
    buckets = defaultdict(list)
    hi = as_of + timedelta(weeks=LOOKAHEAD_WEEKS)
    for p in published:
        d = date.fromisoformat(p["when"][:10])
        if as_of <= d < hi:
            v = perf.total_views(p["content"], records, sources=sources)
            if v is not None:
                buckets[d.weekday()].append(v)
    if not buckets:
        return {}
    fallback = median([x for vs in buckets.values() for x in vs])
    return {wd: median(buckets.get(wd, [fallback])) for wd in range(7)}


def _score(plan_counts: dict, realized: dict) -> float:
    return sum(plan_counts.get(wd, 0) * realized.get(wd, 0) for wd in range(7))


def replay(published: list, records: list, initial_plan: dict) -> dict:
    sources = config.load().get("planner_sources") or None
    weeks = _mondays(published)
    if not weeks:
        return {"error": "not enough history for a single replay week "
                         f"(need > long window + {LOOKAHEAD_WEEKS} weeks)"}
    cur = dict(initial_plan)
    short_cur = dict(initial_plan)
    shape = sorted(initial_plan.values(), reverse=True)
    changes, plan_history = [], [(weeks[0], dict(cur))]
    scores = {"adaptive": 0.0, "unchanged": 0.0, "short_only": 0.0, "random_expected": 0.0}
    matched = total = 0

    for wk in weeks:
        past = [p for p in published if p["when"][:10] < wk.isoformat()]
        p = planner.propose(as_of=wk, published=past, records=records, current=cur)
        if p["applyable"]:
            changes.append({"week": wk.isoformat(), "old": dict(cur), "new": p["proposal"]})
            cur = dict(p["proposal"])
            plan_history.append((wk, dict(cur)))
        # undamped baseline: recent window alone, whenever it is computable at all
        recent = planner.day_stats(wk - timedelta(weeks=int(config.load()["window_weeks_short"])),
                                   wk, past, records, sources)
        stats = {k: {"med": v["med"], "n": v["n"]} for k, v in recent.items()}
        undamped = planner._plan_from(stats, shape)
        if undamped:
            short_cur = undamped

        realized = _realized(wk, published, records, sources)
        if realized:
            scores["adaptive"] += _score(cur, realized)
            scores["unchanged"] += _score(initial_plan, realized)
            scores["short_only"] += _score(short_cur, realized)
            weekly_volume = sum(initial_plan.values())
            scores["random_expected"] += weekly_volume * (sum(realized.values()) / 7)
        for post in [p for p in published if p["when"][:10] == wk.isoformat()]:
            total += 1
            matched += perf.total_views(post["content"], records, sources=sources) is not None

    false_adaptations = 0
    for i, ch in enumerate(changes):
        wk = date.fromisoformat(ch["week"])
        for later in changes[i + 1:]:
            if (date.fromisoformat(later["week"]) - wk).days <= REVERT_WEEKS * 7 \
                    and later["new"] == ch["old"]:
                false_adaptations += 1
                break

    base = scores["unchanged"] or 1
    return {"weeks_replayed": len(weeks), "changes": changes,
            "plan_churn": round(len(changes) / len(weeks), 3),
            "false_adaptation_rate": round(false_adaptations / len(changes), 3) if changes else 0.0,
            "match_rate": round(matched / total, 3) if total else None,
            "scores": {k: round(v) for k, v in scores.items()},
            "uplift_vs_unchanged": {k: round(v / base - 1, 4)
                                    for k, v in scores.items() if k != "unchanged"}}


# ── demo data: a channel whose strong days genuinely shift halfway through ──────────

def demo_data(weeks: int = 30, seed: int = 42) -> tuple:
    rng = random.Random(seed)
    strength_a = {0: 900, 1: 1000, 2: 850, 3: 500, 4: 300, 5: 350, 6: 600}
    strength_b = {0: 300, 1: 1000, 2: 500, 3: 550, 4: 320, 5: 900, 6: 950}  # Sat/Sun rise
    published, records = [], []
    monday = date.today() - timedelta(weeks=weeks)
    monday -= timedelta(days=monday.weekday())
    i = 0
    for w in range(weeks):
        strength = strength_a if w < weeks // 2 else strength_b
        for wd in range(7):
            for _ in range(2):                                   # two posts a day
                d = monday + timedelta(weeks=w, days=wd)
                views = max(10, int(rng.gauss(strength[wd], strength[wd] * 0.35)))
                if rng.random() < 0.02:
                    views *= 20                                  # the occasional viral hit
                cap = f"demo post {i} with its own caption {i}"
                published.append({"when": f"{d.isoformat()}T09:00:00.000Z",
                                  "content": cap, "post_id": f"d{i}"})
                records.append({"title": cap, "views": views, "source": "manual"})
                i += 1
    return published, records


def _load_input(path: str) -> tuple:
    """JSONL with {"when": ISO-date/datetime, "views": int} per line — no matching needed."""
    published, records = [], []
    for i, ln in enumerate(open(path)):
        if not ln.strip():
            continue
        r = json.loads(ln)
        when = r["when"] if "T" in r["when"] else f'{r["when"]}T09:00:00.000Z'
        cap = f"input row {i} caption {i}"
        published.append({"when": when, "content": cap, "post_id": f"i{i}"})
        records.append({"title": cap, "views": int(r["views"]), "source": "manual"})
    return published, records


def main():
    ap = argparse.ArgumentParser(description="Backtest the damped planner against history.")
    ap.add_argument("--input", help="JSONL with {when, views} per post (skips matching)")
    ap.add_argument("--demo", action="store_true", help="run on deterministic synthetic data")
    args = ap.parse_args()
    if args.demo:
        published, records = demo_data()
        # a shaped plan (strong Mon-Wed), so the replay has something to re-rank —
        # the demo channel's strength shifts to the weekend halfway through
        initial = {0: 4, 1: 4, 2: 4, 3: 3, 4: 2, 5: 2, 6: 3}
    else:
        if args.input:
            published, records = _load_input(args.input)
        else:
            published, records = planner.fetch_published(), perf.latest()
        from . import plan
        initial = plan.active()["per_day_by_wd"]
    print(json.dumps(replay(published, records, initial), indent=1))


if __name__ == "__main__":
    main()
