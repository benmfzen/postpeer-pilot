"""Unreadable must never look like absent.

Regression for the failure found on 2026-07-20: `queue_status` reported an empty
review folder while nine finished videos sat in it. The guard was
`if PATH.exists()` — and `exists()` returns False when the OS denies access, so a
permission problem was indistinguishable from "nothing there". Anything that
schedules against such a reading books work it cannot see.
"""
import json
import os
import stat

import pytest

from postpeer_pilot import config, plan, safe_read


def test_absent_file_reads_as_none(tmp_path):
    assert safe_read.read_text_if_present(tmp_path / "nope.json") is None
    assert safe_read.read_lines_if_present(tmp_path / "nope.jsonl") == []


def test_present_file_reads_through(tmp_path):
    f = tmp_path / "there.txt"
    f.write_text("a\nb\n")
    assert safe_read.read_text_if_present(f) == "a\nb\n"
    assert safe_read.read_lines_if_present(f) == ["a", "b"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_unreadable_file_raises_instead_of_defaulting(tmp_path):
    f = tmp_path / "locked.json"
    f.write_text("{}")
    f.chmod(0)
    try:
        with pytest.raises(safe_read.Unreadable):
            safe_read.read_text_if_present(f)
    finally:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_unreadable_plan_does_not_silently_fall_back(tmp_path, monkeypatch):
    """The important one: an unreadable plan must NOT masquerade as 'no plan',
    because the scheduler would then quietly book against built-in defaults."""
    pf = tmp_path / "plan.json"
    pf.write_text(json.dumps({"per_day_by_wd": {str(i): 1 for i in range(7)}}))
    pf.chmod(0)
    monkeypatch.setattr(plan, "PLAN_FILE", pf)
    try:
        with pytest.raises(safe_read.Unreadable):
            plan.active()
    finally:
        pf.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_unreadable_config_does_not_silently_fall_back(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    f.write_text("{}")
    f.chmod(0)
    monkeypatch.setattr(config, "HOME", tmp_path)
    try:
        with pytest.raises(safe_read.Unreadable):
            config.load()
    finally:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)
