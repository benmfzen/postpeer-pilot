"""on_scheduled hook: fires with the right payload, never breaks scheduling, silent
on dry runs and failures-to-schedule."""
import json
from datetime import date, timedelta

from postpeer_pilot import scheduler

START = date.today() + timedelta(days=30)


def _set_hook(home, cmd):
    cfg = json.loads((home / "config.json").read_text())
    cfg["on_scheduled"] = cmd
    (home / "config.json").write_text(json.dumps(cfg))


def test_hook_receives_payload(home, fake_api, video):
    out = home / "hook_payload.json"
    _set_hook(home, f"cat > {out}")
    r = scheduler.schedule([video("hooked")], series="s1", start=START)
    assert r[0]["ok"] and r[0]["hook"]["ok"]
    payload = json.loads(out.read_text())
    assert payload["slot"] == r[0]["slot"]
    assert payload["post_id"] and payload["series"] == "s1"
    assert "Caption for hooked" in payload["caption"]


def test_failing_hook_does_not_fail_schedule(home, fake_api, video):
    _set_hook(home, "exit 7")
    r = scheduler.schedule([video("h2")], start=START)
    assert r[0]["ok"] is True                      # schedule stands
    assert r[0]["hook"]["ok"] is False             # failure surfaced, not swallowed
    assert fake_api.creates == 1


def test_hook_silent_on_dry_run_and_failure(home, fake_api, video):
    out = home / "should_not_exist"
    _set_hook(home, f"touch {out}")
    scheduler.schedule([video("h3")], dry_run=True, start=START)
    assert not out.exists()                        # dry run: no hook
    fake_api.fail_creates = 99
    r = scheduler.schedule([video("h4")], start=START)
    assert r[0]["ok"] is False and not out.exists()  # failed schedule: no hook
