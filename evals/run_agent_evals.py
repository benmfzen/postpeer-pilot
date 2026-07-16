#!/usr/bin/env python3
"""Agent eval harness: Claude drives the REAL MCP server against the fake Postpeer.

For every case in cases.jsonl:
  1. build a fresh workspace (config home + fake state + video files),
  2. run `claude -p` with the postpeer-pilot MCP server attached (fake mode),
  3. grade OBSERVED side effects (fake state, ledger, plan.json) + the final answer.

No network, no real accounts — the only external dependency is the `claude` CLI.

    python3 evals/run_agent_evals.py                 # all cases, 1 trial
    python3 evals/run_agent_evals.py --trials 3
    python3 evals/run_agent_evals.py --case missing-caption --keep
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
from graders import GRADERS  # noqa: E402

RESULTS = ROOT / "evals" / "results"
MAX_TURNS = 12


def build_workspace(case: dict) -> Path:
    ws = Path(tempfile.mkdtemp(prefix=f"ppp-eval-{case['id']}-"))
    (ws / "config.json").write_text(json.dumps({
        "per_day_by_wd": {"0": 4, "1": 4, "2": 4, "3": 3, "4": 2, "5": 2, "6": 3},
        "slot_hours": ["08:00", "09:00", "10:00", "11:00"], "series_day_cap": 2}))
    setup = case.get("setup", {})
    for name in setup.get("videos", []):
        v = ws / f"{name}.mp4"
        v.write_bytes(b"eval")
        if setup.get("captions", True):
            v.with_suffix(".txt").write_text(f"Caption for {name} - an eval fixture video")
    state = {"scheduled": [], "published": [], "uploads": 0, "creates": 0, "fail_creates": 0}
    n_pre = setup.get("prescheduled", 0)
    for i in range(n_pre):
        state["scheduled"].append({"postId": f"pre{i}", "content": f"prescheduled {i}",
                                   "scheduledFor": f"2099-01-0{i + 1}T08:00:00.000Z"})
    state["prescheduled_count"] = n_pre
    (ws / "fake_state.json").write_text(json.dumps(state))
    return ws


def run_agent(case: dict, ws: Path, model: str) -> tuple:
    """-> (final_answer, tool_names, raw_events)"""
    mcp_config = {"mcpServers": {"postpeer-pilot": {
        "type": "stdio", "command": sys.executable, "args": [str(ROOT / "server.py")],
        "env": {"POSTPEER_PILOT_HOME": str(ws),
                "POSTPEER_PILOT_FAKE": str(ws / "fake_state.json")}}}}
    prompt = (f"{case['prompt']}\n\n(Files referenced are in {ws}. Use the postpeer-pilot "
              f"tools. When done, answer with what you did or why you could not.)")
    cmd = ["claude", "-p", prompt, "--model", model, "--max-turns", str(MAX_TURNS),
           "--mcp-config", json.dumps(mcp_config),
           "--allowedTools", "mcp__postpeer-pilot__*",
           "--disallowedTools", "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch",
           "--output-format", "stream-json", "--verbose"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(ws))
    answer, tools, events = "", [], []
    for ln in p.stdout.splitlines():
        try:
            ev = json.loads(ln)
        except json.JSONDecodeError:
            continue
        events.append(ev)
        if ev.get("type") == "assistant":
            for block in (ev.get("message") or {}).get("content", []):
                if block.get("type") == "tool_use":
                    tools.append(block.get("name", "?"))
        if ev.get("type") == "result":
            answer = ev.get("result") or ""
    if not answer and p.returncode != 0:
        answer = f"[claude -p failed: {p.stderr[:300]}]"
    return answer, tools, events


def grade(case: dict, ws: Path, answer: str, tools: list) -> list:
    out = []
    for g in case["graders"]:
        passed, note = GRADERS[g](ws, answer, tools)
        out.append({"grader": g, "passed": passed, "note": note})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--case", help="run a single case id")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--keep", action="store_true", help="keep workspaces for inspection")
    args = ap.parse_args()

    cases = [json.loads(ln) for ln in (ROOT / "evals" / "cases.jsonl").open() if ln.strip()]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rows, by_category = [], defaultdict(lambda: [0, 0])

    for case in cases:
        for trial in range(args.trials):
            ws = build_workspace(case)
            answer, tools, _ = run_agent(case, ws, args.model)
            checks = grade(case, ws, answer, tools)
            ok = all(c["passed"] for c in checks)
            by_category[case["category"]][0] += ok
            by_category[case["category"]][1] += 1
            rows.append({"case": case["id"], "trial": trial, "passed": ok,
                         "tools": tools, "checks": checks, "answer": answer[:500]})
            flag = "PASS" if ok else "FAIL"
            print(f"[{flag}] {case['id']} (trial {trial + 1}/{args.trials}) "
                  f"tools={tools} " + "; ".join(f"{c['grader']}:{'ok' if c['passed'] else 'X'}"
                                                for c in checks))
            if not args.keep:
                shutil.rmtree(ws, ignore_errors=True)
            else:
                print(f"       workspace kept: {ws}")

    total_ok = sum(r["passed"] for r in rows)
    summary = {"stamp": stamp, "model": args.model, "trials": args.trials,
               "total": f"{total_ok}/{len(rows)}",
               "by_category": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_category.items())},
               "runs": rows}
    out = RESULTS / f"{stamp}.json"
    out.write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print(f"\n{total_ok}/{len(rows)} passed · per category: "
          + " · ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(by_category.items())))
    print(f"results: {out}")


if __name__ == "__main__":
    main()
