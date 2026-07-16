"""Backtest harness: structural guarantees + the one behavioural claim that matters —
on data whose strong days genuinely shift, the damped planner adapts (and never breaks
the plan shape while doing so)."""
from postpeer_pilot import backtest


def test_backtest_on_shifting_channel(home, fake_api):
    published, records = backtest.demo_data(weeks=30, seed=42)
    initial = {0: 4, 1: 4, 2: 4, 3: 3, 4: 2, 5: 2, 6: 3}
    r = backtest.replay(published, records, initial)

    assert r["weeks_replayed"] > 10
    assert 0 <= r["plan_churn"] <= 1
    assert 0 <= r["false_adaptation_rate"] <= 1
    assert r["match_rate"] == 1.0                      # synthetic captions always match
    # the mid-history weekday shift must eventually be picked up...
    assert len(r["changes"]) >= 1
    # ...and every plan the replay ever adopted kept volume and shape
    for ch in r["changes"]:
        assert sorted(ch["new"].values()) == sorted(initial.values())
        assert sum(ch["new"].values()) == sum(initial.values())
    # scoring covered all strategies
    assert set(r["scores"]) == {"adaptive", "unchanged", "short_only", "random_expected"}


def test_backtest_refuses_thin_history(home, fake_api):
    published, records = backtest.demo_data(weeks=6)
    r = backtest.replay(published, records, {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1})
    assert "error" in r
