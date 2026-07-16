# postpeer-pilot

An MCP server that turns a [Postpeer](https://postpeer.dev) account into a
**performance-driven publishing autopilot** for short-form video (TikTok,
Instagram, Facebook, YouTube).

You drop a video into the pipeline — it lands on the next free slot of a post
plan that is derived from your channel's real performance and only changes when
the data says so **consistently**.

```
video.mp4 ──► schedule_video ──► next free plan slot ──► Postpeer ──► 4 platforms
                                      ▲
             plan.json  ◄── plan_review (damped) ◄── performance_pull
```

## The four tools

| Tool | What it does |
|---|---|
| `queue_status` | Scheduled posts per day, the next free plan slots, the active plan |
| `schedule_video` | Upload + schedule video(s) onto the next free plan slot(s) |
| `performance_pull` | Refresh view counts (TikTok via yt-dlp, IG/FB via Meta Graph API, or a manual CSV/JSONL drop) |
| `plan_review` | Re-rank the plan against real 4/8-week performance; apply only on a stable delta |

## The ideas worth stealing

**A post plan, not a queue.** The plan says *how many* posts go out on *which
weekday* at *which local times* (e.g. Mon–Wed 4, Thu 3, Fri/Sat 2 — mornings
only). Scheduling means: find the next slot the plan allows that isn't already
taken on Postpeer. Strong days get volume, dead days don't burn good content.

**Damped plan adaptation.** One viral Sunday must not rewrite the plan. A
change is only applyable when:

- a long window (default 8 weeks) and a short window (default 4 weeks)
  **independently produce the same new plan**,
- every weekday has enough samples (default ≥ 3 posts),
- there is actually more history than the short window (otherwise the
  stability check is vacuous),
- posts younger than 7 days are ignored (their views are still growing).

The weekly volume and its shape are preserved: a `[4,4,4,3,3,2,2]` plan stays a
`[4,4,4,3,3,2,2]` plan — the counts just get re-assigned to weekdays by
performance rank. `plan_review` without `apply` is always a safe, read-only
report.

**Series cap.** Posts tagged with the same `series` are capped per day
(default 2), so a 25-part series doesn't flood a single week.

**Scheduling only, never live.** A badly timed scheduled post can be deleted;
a live post cannot. Going live is deliberately not exposed.

## Setup

Requires Python 3.11+ (stdlib only). Optional: `yt-dlp` on the PATH for the
TikTok puller.

```bash
mkdir -p ~/.config/postpeer-pilot
cp examples/config.example.json    ~/.config/postpeer-pilot/config.json
cp examples/accounts.example.json  ~/.config/postpeer-pilot/accounts.json
echo 'POSTPEER_API_KEY=pk_...'   > ~/.config/postpeer-pilot/.env
chmod 600 ~/.config/postpeer-pilot/.env
```

Account IDs come from `GET /v1/connect/integrations` after connecting your
channels in Postpeer.

Register with Claude Code:

```bash
claude mcp add --scope user postpeer-pilot -- python3 /path/to/postpeer-pilot/server.py
```

Then, in any session: *"schedule these three videos"* → Claude calls
`schedule_video` with the file paths; captions come from `<video>.txt` sidecar
files next to the mp4s.

## Postpeer API quirks (captured in `api.py` so you don't relearn them)

- Auth header is `x-access-key`, **not** `Authorization: Bearer`.
- `scheduledFor` must be RFC3339 with milliseconds + `Z`; combined with the
  `timezone` field, the HH:MM inside the string is treated as local time.
- `GET /posts` hard-caps `limit` at 100 — and `limit=101` returns
  `success:false` with an **empty list**, not an error. Always paginate with
  `offset`.
- YouTube titles go in `platformSpecificData: {"title": ...}` and the object
  rejects any additional property.

## Files

```
server.py                 MCP server (stdio, newline-delimited JSON-RPC, no SDK)
postpeer_pilot/
  api.py                  Postpeer client (upload, posts, pagination)
  plan.py                 plan model + free-slot search + series cap
  planner.py              damped 4/8-week plan review
  perf.py                 performance store + pullers (tiktok / meta / manual)
  scheduler.py            video -> next free slot -> Postpeer
  config.py               config dir, defaults, key/account loading
~/.config/postpeer-pilot/
  config.json  accounts.json  .env  plan.json  performance.jsonl  scheduled.jsonl
```

## Non-goals

No content generation, no analytics dashboard, no live posting. This is the
thin, reliable layer between "video is ready" and "video is scheduled right".

---

*Not affiliated with Postpeer. Built for a real channel's daily pipeline;
extracted and generalized. MIT.*
