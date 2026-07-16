#!/usr/bin/env python3
"""postpeer-pilot MCP server (stdio).

Turns a Postpeer account into a performance-driven publishing autopilot:

  queue_status      scheduled posts, next free plan slots, active plan
  schedule_video    drop video(s) onto the next free slot(s) of the plan
  performance_pull  refresh view counts (TikTok / Meta Graph / manual file)
  plan_review       check the plan against real 4/8-week performance, optionally apply

Hand-rolled MCP (newline-delimited JSON-RPC over stdio) — zero dependencies.
Register:  claude mcp add --scope user postpeer-pilot -- python3 /path/to/server.py
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from postpeer_pilot import api, perf, plan, planner, scheduler  # noqa: E402

if os.environ.get("POSTPEER_PILOT_FAKE"):      # offline mode: demos, integration, evals
    from postpeer_pilot import fake_api
    fake_api.install(os.environ["POSTPEER_PILOT_FAKE"])

TOOLS = [
    {"name": "queue_status",
     "description": "Publishing overview: scheduled Postpeer posts per day, the next free "
                    "plan slots, and the active post plan (per-weekday counts, slot hours).",
     "inputSchema": {"type": "object", "properties": {
         "free_slots": {"type": "integer", "description": "how many free slots to show (default 8)"}},
         "additionalProperties": False}},
    {"name": "schedule_video",
     "description": "Schedule video file(s) onto the next FREE slot(s) of the post plan: "
                    "uploads to Postpeer, schedules on all configured accounts, respects the "
                    "per-day series cap. Caption comes from a <video>.txt sidecar or the "
                    "captions argument. Never posts live — scheduling only. dry_run previews "
                    "the slot assignment.",
     "inputSchema": {"type": "object", "properties": {
         "videos": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                    "description": "paths to .mp4 files; several fill consecutive free slots"},
         "captions": {"type": "array", "items": {"type": "string"},
                      "description": "captions, same order as videos (else sidecar files)"},
         "titles": {"type": "array", "items": {"type": "string"},
                    "description": "YouTube titles, same order as videos (optional)"},
         "series": {"type": "string", "description": "series tag for the per-day cap (optional)"},
         "dry_run": {"type": "boolean", "description": "preview only (default false)"},
         "start": {"type": "string", "description": "earliest day YYYY-MM-DD (default today)"}},
         "required": ["videos"], "additionalProperties": False}},
    {"name": "performance_pull",
     "description": "Refresh the performance store: TikTok views (yt-dlp), Instagram/Facebook "
                    "views (Meta Graph API), and/or a manual CSV/JSONL drop. Which sources run "
                    "depends on the config; the store feeds plan_review.",
     "inputSchema": {"type": "object", "properties": {
         "tiktok": {"type": "boolean", "description": "pull TikTok (default true if configured)"},
         "meta": {"type": "boolean", "description": "pull IG/FB (default true if configured)"},
         "import_file": {"type": "string", "description": "path to a manual CSV/JSONL drop (optional)"}},
         "additionalProperties": False}},
    {"name": "plan_review",
     "description": "Review the post plan against real performance of the short and long window "
                    "(default 4 and 8 weeks). Shows avg views per weekday and a re-ranked plan "
                    "proposal. Damped: only applyable when both windows agree, every weekday has "
                    "enough samples, and history exceeds the short window. apply=true writes plan.json.",
     "inputSchema": {"type": "object", "properties": {
         "apply": {"type": "boolean", "description": "apply when damping criteria hold (default false)"}},
         "additionalProperties": False}},
]


def t_queue(args: dict):
    n = int(args.get("free_slots") or 8)
    posts = api.list_posts("scheduled")
    by_day: dict = {}
    for p in posts:
        sf = str(p.get("scheduledFor") or "")
        if len(sf) >= 16:
            by_day.setdefault(sf[:10], []).append(sf[11:16])
    return {"scheduled_total": len(posts),
            "scheduled_by_day": {d: sorted(v) for d, v in sorted(by_day.items())},
            "next_free_slots": plan.free_slots(n),
            "plan": plan.active()}


def t_schedule(args: dict):
    start = date.fromisoformat(args["start"]) if args.get("start") else None
    return {"results": scheduler.schedule(
        args["videos"], captions=args.get("captions"), titles=args.get("titles"),
        series=args.get("series"), dry_run=bool(args.get("dry_run")), start=start)}


def t_perf(args: dict):
    out = {}
    if args.get("tiktok", True):
        try:
            out["tiktok_rows"] = perf.pull_tiktok()
        except Exception as e:
            out["tiktok_error"] = f"{type(e).__name__}: {e}"
    if args.get("meta", True):
        try:
            out["meta_rows"] = perf.pull_meta()
        except Exception as e:
            out["meta_error"] = f"{type(e).__name__}: {e}"
    if args.get("import_file"):
        out["imported_rows"] = perf.import_file(args["import_file"])
    return out


def t_plan(args: dict):
    return planner.apply(force=False) if args.get("apply") else planner.propose()


HANDLERS = {"queue_status": t_queue, "schedule_video": t_schedule,
            "performance_pull": t_perf, "plan_review": t_plan}


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error:
        msg["error"] = {"code": -32000, "message": str(error)}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, id_ = m.get("method"), m.get("id")
        if method == "initialize":
            reply(id_, {"protocolVersion": m["params"].get("protocolVersion", "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "postpeer-pilot", "version": "0.1.0"}})
        elif method == "tools/list":
            reply(id_, {"tools": TOOLS})
        elif method == "tools/call":
            name = m["params"]["name"]
            args = m["params"].get("arguments") or {}
            try:
                res = HANDLERS[name](args)
                reply(id_, {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}]})
            except Exception as e:
                reply(id_, {"content": [{"type": "text", "text": f"ERROR: {type(e).__name__}: {e}"}],
                            "isError": True})
        elif method == "ping":
            reply(id_, {})
        elif id_ is not None:
            reply(id_, error=f"unknown method {method}")


if __name__ == "__main__":
    main()
