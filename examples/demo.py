#!/usr/bin/env python3
"""End-to-end demo on the built-in fake Postpeer — safe to run anywhere, no keys.

    python3 examples/demo.py

The story it tells (the interesting part is the controlled NON-action):
  1. three finished videos, queue is empty -> status
  2. dry run: slot assignment preview, zero side effects
  3. a 5-part series gets scheduled -> the per-day cap spreads it out
  4. plan_review on a young channel -> REFUSES to change the plan (and says why)
  5. plan_review on 16 weeks of stable data -> the same code applies the change
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

home = Path(tempfile.mkdtemp(prefix="postpeer-pilot-demo-"))
os.environ["POSTPEER_PILOT_HOME"] = str(home)
(home / "config.json").write_text(json.dumps({
    "per_day_by_wd": {"0": 4, "1": 4, "2": 4, "3": 3, "4": 2, "5": 2, "6": 3},
    "slot_hours": ["08:00", "09:00", "10:00", "11:00"], "series_day_cap": 2}))

from postpeer_pilot import fake_api, perf, plan, planner, scheduler  # noqa: E402

fake_api.install(str(home / "fake_state.json"))


def say(title):
    import time
    time.sleep(float(os.environ.get("DEMO_PAUSE", 0)))    # readable pacing for the GIF
    print(f"\n{'─' * 62}\n{title}\n{'─' * 62}")


videos = []
for i in range(1, 6):
    v = home / f"episode-{i:02d}.mp4"
    v.write_bytes(b"demo")
    v.with_suffix(".txt").write_text(f"Episode {i:02d} — the demo series, part {i}")
    videos.append(v)
start = date.today() + timedelta(days=1)

say("1 · queue_status — empty channel, active plan")
print(f"plan: {plan.active()['per_day_by_wd']}  (source: {plan.active()['source']})")
print(f"next free slots: {plan.free_slots(4, start=start)}")

say("2 · dry run — preview only, zero side effects")
for r in scheduler.schedule(videos[:3], series="demo-series", dry_run=True, start=start):
    print(f"  [dry-run] {r['video']} -> {r['slot']}")
print(f"uploads that happened: {json.loads((home / 'fake_state.json').read_text())['uploads']}")

say("3 · schedule the 5-part series — the per-day cap (2) spreads it")
for r in scheduler.schedule(videos, series="demo-series", start=start):
    print(f"  {'OK ' if r['ok'] else 'ERR'} {r['video']} -> {r['slot']}")

say("4 · plan_review on a YOUNG channel — controlled non-action")
p = planner.propose()
print(f"applyable: {p['applyable']}")
for reason in p["reasons"]:
    print(f"  refused: {reason}")
print(f"plan.json written: {plan.PLAN_FILE.exists()}")

say("5 · same code, 16 weeks of stable history — now it acts")
strength = {0: 300, 1: 900, 2: 500, 3: 550, 4: 320, 5: 700, 6: 1000}   # Sun/Tue strong
published, rows = [], []
monday = date.today() - timedelta(weeks=16)
monday -= timedelta(days=monday.weekday())
for w in range(16):
    for wd in range(7):
        d = monday + timedelta(weeks=w, days=wd)
        cap = f"history clip {w}-{wd} caption {w * 7 + wd}"
        published.append({"postId": f"h{w}{wd}", "content": cap,
                          "scheduledFor": f"{d.isoformat()}T09:00:00.000Z"})
        rows.append({"title": cap, "views": strength[wd] + w, "source": "manual"})
state = json.loads((home / "fake_state.json").read_text())
state["published"] = published
(home / "fake_state.json").write_text(json.dumps(state))
perf._append(rows)

r = planner.apply()
print(f"applyable: {r['applyable']}  ->  applied: {r['applied']}")
print(f"old plan (Mon..Sun): {[r['current'][wd] for wd in range(7)]}")
print(f"new plan (Mon..Sun): {[r['proposal'][wd] for wd in range(7)]}")
print("weekly volume unchanged:",
      sum(r["current"].values()) == sum(r["proposal"].values()))
print(f"per-weekday medians (recent window): "
      f"{ {k: v['med'] for k, v in r['stats_recent'].items()} }")
print(f"\n(demo workspace: {home})")
