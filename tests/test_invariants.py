"""Safety & reliability invariants.

These are the promises the README makes; each test breaks the build if code drifts:

  I1  no tool can publish immediately (scheduling only)
  I2  a dry run causes zero write side effects
  I3  plan_review never changes the weekly volume or its shape
  I4  no plan is ever written without sufficient data
  I5  a failed post creation does not occupy its slot
  I6  a series never exceeds its per-day cap
  I7  a second scheduler run cannot double-book a slot taken by the first
  I8  ambiguous performance matches are refused, not guessed
"""
from datetime import date, timedelta

from postpeer_pilot import perf, plan, planner, scheduler

START = date.today() + timedelta(days=30)      # far future: no past-slot skipping


# I1 — enforced inside FakeAPI.create_post (asserts when_local is not None);
# exercised by every scheduling test below. Plus: the MCP surface offers no live path.
def test_no_live_surface_in_mcp_tools():
    import server
    schedule_tool = next(t for t in server.TOOLS if t["name"] == "schedule_video")
    assert "live" not in schedule_tool["inputSchema"]["properties"]
    assert "live" not in schedule_tool["description"].lower().replace("never posts live", "")


def test_i2_dry_run_has_no_write_side_effects(home, fake_api, video):
    res = scheduler.schedule([video()], dry_run=True, start=START)
    assert res[0]["ok"] and res[0]["dry_run"]
    assert fake_api.uploads == 0 and fake_api.creates == 0
    assert not plan.LEDGER.exists()


def _history(weeks: int, day_views: dict) -> tuple:
    """Synthetic published posts + perf records: `weeks` weeks, one post per weekday,
    views per weekday from day_views (older half gets slightly different absolute
    numbers but the same ranking)."""
    published, records = [], []
    monday = START - timedelta(weeks=weeks)      # history runs right up to as_of;
    monday -= timedelta(days=monday.weekday())   # the fresh-cutoff trims the last week
    i = 0
    for w in range(weeks):
        for wd in range(7):
            d = monday + timedelta(weeks=w, days=wd)
            cap = f"post number {i} unique caption words {i}"
            published.append({"when": f"{d.isoformat()}T09:00:00.000Z",
                              "content": cap, "post_id": f"h{i}"})
            records.append({"title": cap, "views": day_views[wd] + w, "source": "manual"})
            i += 1
    return published, records


def test_i3_apply_preserves_weekly_shape(home, fake_api):
    current = {0: 4, 1: 4, 2: 4, 3: 3, 4: 2, 5: 2, 6: 3}
    # Sunday strongest now, Monday weakest — a full re-ranking vs. current.
    day_views = {0: 100, 1: 900, 2: 800, 3: 700, 4: 600, 5: 500, 6: 1000}
    published, records = _history(16, day_views)
    p = planner.propose(as_of=START, published=published, records=records, current=current)
    assert p["applyable"], p["reasons"]
    assert sorted(p["proposal"].values()) == sorted(current.values())   # shape kept
    assert sum(p["proposal"].values()) == sum(current.values())         # volume kept
    assert p["proposal"][6] == 4 and p["proposal"][0] == 2              # re-ranked


def test_i4_no_plan_written_without_data(home, fake_api, monkeypatch):
    monkeypatch.setattr(planner, "fetch_published", lambda: [])
    monkeypatch.setattr(perf, "latest", lambda: [])
    r = planner.apply()
    assert r["applied"] is False
    assert not plan.PLAN_FILE.exists()


def test_i4b_young_channel_not_applyable(home, fake_api):
    # 4 weeks of history: recent window full, prior (disjoint) window empty.
    published, records = _history(4, {wd: 100 + wd for wd in range(7)})
    p = planner.propose(as_of=START, published=published, records=records,
                        current={0: 4, 1: 4, 2: 4, 3: 3, 4: 2, 5: 2, 6: 3})
    assert not p["applyable"]
    assert any("prior window" in r for r in p["reasons"])


def test_i5_failed_post_frees_its_slot(home, fake_api, video):
    fake_api.fail_creates = 1
    res = scheduler.schedule([video("a"), video("b")], start=START)
    assert res[0]["ok"] is False and res[1]["ok"] is True
    assert res[1]["slot"] == res[0]["slot"]        # the freed slot got reused
    assert "orphaned_upload" in res[0].get("response", res[0])


def test_i6_series_cap_never_exceeded(home, fake_api, video):
    vids = [video(f"s{i}") for i in range(5)]
    res = scheduler.schedule(vids, series="demo-series", start=START)
    per_day = {}
    for r in res:
        assert r["ok"]
        per_day[r["slot"][:10]] = per_day.get(r["slot"][:10], 0) + 1
    assert max(per_day.values()) <= 2              # series_day_cap from config
    # ...and the cap is FILLED, not undershot: 5 videos at 2/day need exactly 3 days
    # (guards against double-counting a slot in ledger + taken_extra)
    assert len(per_day) == 3
    assert sorted(per_day.values(), reverse=True) == [2, 2, 1]


def test_i7_second_run_sees_first_runs_slots(home, fake_api, video):
    r1 = scheduler.schedule([video("x")], start=START)
    r2 = scheduler.schedule([video("y")], start=START)
    assert r1[0]["ok"] and r2[0]["ok"]
    assert r1[0]["slot"] != r2[0]["slot"]          # occupancy re-read from the API


def test_i8_ambiguous_match_refused():
    records = [{"title": "orks points update video part one", "views": 100, "source": "m"},
               {"title": "orks points update video part two", "views": 900, "source": "m"}]
    assert perf.match("orks points update video", records) is None
    # but an exact title always wins
    records.append({"title": "orks points update video", "views": 55, "source": "m"})
    assert perf.match("orks points update video", records) == 55
