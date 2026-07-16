"""Shared fixtures: isolated config home + a fake Postpeer API.

The fake replaces the network layer at the module boundary (postpeer_pilot.api
attributes), so every invariant test runs against real plan/scheduler/planner code
with zero network access.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from postpeer_pilot import api, config, plan, planner, scheduler  # noqa: E402


class FakeAPI:
    """In-memory Postpeer: uploads count, created posts land in the scheduled list."""

    def __init__(self, fail_creates: int = 0):
        self.scheduled = []
        self.uploads = 0
        self.creates = 0
        self.fail_creates = fail_creates          # fail the first N create_post calls

    def list_posts(self, status="scheduled"):
        return list(self.scheduled) if status == "scheduled" else []

    def upload_media(self, mp4):
        self.uploads += 1
        return f"https://fake.example/{mp4.name}"

    def create_post(self, content, video_url, when_local, yt_title=None):
        self.creates += 1
        assert when_local is not None, "INVARIANT VIOLATED: live post attempted"
        if self.creates <= self.fail_creates:
            return {"success": False, "error": "injected failure"}
        pid = f"p{self.creates}"
        self.scheduled.append({"postId": pid, "content": content,
                               "scheduledFor": api.scheduled_for(when_local)})
        return {"success": True, "postId": pid}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated config dir with a known plan; patches every module-level HOME path."""
    (tmp_path / "config.json").write_text(json.dumps({
        "timezone": "Europe/Berlin",
        "per_day_by_wd": {"0": 4, "1": 4, "2": 4, "3": 3, "4": 2, "5": 2, "6": 3},
        "slot_hours": ["08:00", "09:00", "10:00", "11:00"],
        "series_day_cap": 2,
    }))
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setattr(plan, "PLAN_FILE", tmp_path / "plan.json")
    monkeypatch.setattr(plan, "LEDGER", tmp_path / "scheduled.jsonl")
    return tmp_path


@pytest.fixture
def fake_api(monkeypatch):
    fake = FakeAPI()

    def install(target):
        for name in ("list_posts", "upload_media", "create_post"):
            monkeypatch.setattr(target, name, getattr(fake, name), raising=False)
    install(api)                      # plan.py and planner.py call through the module
    return fake


@pytest.fixture
def video(tmp_path_factory):
    d = tmp_path_factory.mktemp("videos")

    def make(name="clip"):
        v = d / f"{name}.mp4"
        v.write_bytes(b"fake")
        v.with_suffix(".txt").write_text(f"Caption for {name}")
        return v
    return make
