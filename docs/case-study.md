# Case study: a real channel's publishing pipeline

postpeer-pilot was not designed on a whiteboard — it was extracted from the daily
pipeline of a real short-form video channel (four platforms: TikTok, Instagram,
Facebook, YouTube; anonymized here). This page records the problem, the decisions,
and what actually happened in operation.

## The starting point

The channel produces 2–4 videos per day through an automated render pipeline.
Publishing was the bottleneck and the risk zone:

- **Manual slot arithmetic.** Every batch meant reading the existing schedule,
  remembering the posting plan (which weekdays take how many posts), spotting
  collisions, and typing exact RFC3339 timestamps into an API call. 10–15 minutes
  per batch, every day, with a person in the loop who just wanted to say
  *"these three can go out"*.
- **A plan based on a one-off analysis.** A 107-video analysis had produced a
  clear result: mornings win on every weekday, Tuesday is the strongest day,
  Friday/Saturday are dead, and flooding (8 posts/day) crashes per-video reach.
  That produced a fixed weekly plan — Mon–Wed 4, Thu/Sun 3, Fri/Sat 2, morning
  slots only. But the plan was a snapshot: nothing would ever update it as the
  channel changed.
- **Series risk.** A 25-part series was ready to ship. Naively scheduled, it would
  have filled entire weeks and buried every other format — the flooding failure
  mode again, in a new shape.

## Requirements as they emerged

1. Drop finished videos into the pipeline; slot selection must be automatic and
   plan-aware (not "next morning hour", but "next slot the plan allows that isn't
   taken").
2. The plan should follow real performance — but a single viral video must never
   rewrite it. The operator's phrasing: *"adapt only on a bigger delta, say the
   last 4–8 weeks."* That sentence became the damping design.
3. Never post live. Everything scheduled, everything reversible.
4. Cap same-series posts per day (the 25-part series became `series_day_cap`).
5. Refuse rather than guess: thin data, ambiguous matches, missing captions are
   all reasons to stop, not to improvise.

## What happened in operation

Numbers from the live channel (as of 2026-07-16):

- **85 posts published** via the API pipeline, **49 scheduled ahead** — roughly
  two weeks of runway maintained continuously.
- **117 tracked publishing events** in the channel's ledger since the pipeline
  went live (~4.5 weeks), across all four platforms.
- **Zero accidental live posts.** The tool has no live path; the number is boring
  by construction, which is the point.
- The 25-part series shipped over ~10 days at max 2/day next to the regular
  formats, instead of flooding the schedule.
- **The planner's first real review proposed a change and refused to apply it** —
  the channel's history was younger than the long window, so the recent and prior
  windows would have been the same posts. The refusal reason was printed, the plan
  stayed, and the review is simply re-run as history accumulates. This is the
  damping working as designed: the interesting output was the *documented
  non-action*.
- Slot planning time went from 10–15 minutes of manual schedule-reading per batch
  to a one-line request ("schedule these three").

## What operation taught us (fed back into the design)

- **The API's edges bite silently.** `limit=101` returning an empty *success*
  response cost a debugging session — the latest scheduled posts just vanished
  from view. That's why pagination is mandatory in `api.py` and documented in the
  README.
- **Text matching is a liability.** Reconciling published posts with performance
  data by caption similarity mostly works — until two posts in the same series
  differ by one word. That produced the ID-first ledger design; fuzzy matching
  survives only as a fallback for pre-tool posts, and ambiguity now returns
  "no data" instead of a guess.
- **Every safety rule here was a real incident or a near-miss first.** The series
  cap exists because a 25-part series nearly flooded the plan. The freshness
  cutoff exists because a two-day-old post looks like a flop before its views
  mature. The double-count guard on the series cap was found by the demo script
  in this repo, then pinned by a test.

## Next iteration

- Feed per-platform totals into the planner as platforms diverge (the config
  already supports `planner_sources`).
- Revisit the damping thresholds once the channel has ≥ 16 weeks of history —
  the backtest harness exists precisely so that change is a measurement, not an
  opinion.
