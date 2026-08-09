# ADR-002: Scale-out design — from single-operator to multi-team

**Status:** proposed (not implemented) · **Date:** 2026-07-16

## Context

postpeer-pilot is built, tested, and running as a **single-operator tool**: one
channel, one config directory, one process at a time. That scope is deliberate — see
the README's "Design notes & known limits" and [ADR-001](001-deterministic-planner.md).
But it's worth recording, precisely, what would have to change for a shared,
multi-team, or multi-tenant deployment — so the boundary between "works today" and
"would need work" is an explicit decision, not something a reader has to infer from
absence.

This ADR documents the decision **not to build** the items below yet, and what each
one would concretely require if the day comes. Nothing here is implemented.

## The six gaps, and what closing each one actually requires

### 1. Distributed locking around slot assignment

**Today:** `plan.free_slots()` re-reads live Postpeer occupancy on every call (tested,
invariant I7), so a *second, later* scheduler run never double-books a slot the first
run already has on Postpeer. But two runs that read occupancy **at the same instant**
— before either has written anything back — can both see the same free slot. This is
explicitly called out in the README as acceptable for one operator and unsafe for
concurrent ones.

**What closing it requires:** a lock scoped to `(account_id, date)`, held for the
read-check-write window around slot assignment. Not a generic distributed lock
service — the contention unit is narrow (one account, one day), so something like a
row in a shared table with a conditional write (`UPDATE ... WHERE slot IS NULL`) or a
Postgres advisory lock keyed on `hash(account_id, date)` is enough. A general-purpose
lock manager (Redis/Zookeeper) would be solving a bigger problem than this one has.

### 2. Atomic `plan.json` writes and versioning

**Today:** `planner.apply()` calls `PLAN_FILE.write_text(...)` directly
(`postpeer_pilot/planner.py:124`) — a torn read during a crash mid-write falls back to
config defaults rather than corrupting state, which is safe but silent: there's no
record of what the plan *was* before the write, only what it is now.

**What closing it requires:** write-to-temp-then-`os.replace()` for atomicity (cheap,
should probably happen regardless of scale), plus a `plan_history.jsonl` — one
append-only line per applied plan (proposal, stats, whether forced, timestamp)
instead of overwriting in place. That turns "what changed and why" from "not
recorded" into "queryable," which matters the moment more than one person is asking
"why did Tuesday's volume change last week."

### 3. A durable job queue instead of direct, synchronous API calls

**Today:** `schedule_video` uploads and schedules inline, in the calling process; a
crash mid-batch leaves whatever was already scheduled recorded in the ledger
(idempotent re-runs pick up where it left off — tested in
`tests/test_reliability.py`), but there's no persistent, inspectable "N videos are
queued to be scheduled" state between the request and completion.

**What closing it requires:** a real queue (even SQLite-backed would do — this
doesn't need Kafka) so that `schedule_video` enqueues and returns immediately, a
worker processes it with the existing retry policy, and `queue_status` can report
in-flight work, not just completed slots. This is the change most tied to *why* you'd
scale: a single MCP call blocking on N sequential uploads is fine for a handful of
videos and wrong for a content team's daily batch.

### 4. Idempotency at the API boundary — but not the way that phrase usually means

The generic version of this advice is "add idempotency keys to your API calls." That
doesn't apply cleanly here: **Postpeer's API has no idempotency-key mechanism to send
one to** (see the README's "Postpeer API quirks" — there's no reservation primitive,
full stop). Today's idempotency is built entirely in the layer above the API: the
local ledger records what this tool has scheduled, keyed by post id once one exists,
and a re-run skips ledger entries in future slots (`allow_duplicate` opts out).

**What scaling it requires:** the same pattern, made crash-safe and shared — a
ledger entry written *before* the upload call, in a `pending` state, so a second
worker (or a retry after a crash between "media uploaded" and "post created") can see
the in-flight attempt and either wait or resume from the orphaned upload
(`orphaned_upload` already surfaces the reusable URL) instead of re-uploading. The
idempotency key, in other words, needs to be **ours**, generated before the first
network call, not Postpeer's.

### 5. A structured, queryable audit log

**Today:** `scheduled.jsonl` is an audit trail for *scheduling* decisions only —
what was scheduled, when, under what series. Plan changes live inside `plan.json`'s
`basis` field (only the most recent one, per gap 2). There's no unified log of "who
(which operator, which agent session) asked for what, and what did the tool layer
actually do" across both tools.

**What closing it requires:** one append-only, structured log (JSONL is enough,
doesn't need a database) written by the tool layer itself — not the MCP client — for
every call: `{ts, tool, args, actor, result, refused_reason?}`. `actor` matters more
here than in most audit logs: the whole point of this tool is that an agent can act,
so the log needs to distinguish "operator ran this from the CLI" from "Claude called
this via MCP" from (eventually) "operator B's agent called this."

### 6. Tenant isolation

**Today:** one `POSTPEER_PILOT_HOME` (default `~/.config/postpeer-pilot`), one
`accounts.json`, one `plan.json`, one ledger. Nothing in the code path takes a tenant
identifier — `config.HOME` is a module-level constant.

**What closing it requires:** the actual work here is smaller than it sounds, because
the data model already partitions cleanly by account. Threading a `tenant_id`
through `config.HOME`-equivalent lookups (or moving from files to a
tenant-keyed table) turns one directory into N; the planner, gates, and invariants
don't change at all, because they were already written per-account. The real design
question isn't storage, it's **auth**: who can call `schedule_video` for tenant A,
and how does an MCP server (currently one process, one identity) authenticate a
caller as acting on behalf of a specific tenant. That's the part worth designing
before writing any tenant-partitioning code.

## Human approval for plan changes, at scale

`planner.apply(force=True)` already exists as an escape hatch for a human who wants
to override the damping guard on thin data — but `server.py` never passes it
(`return planner.apply(force=False) if args.get("apply") else planner.propose()`),
so **no MCP tool call can reach it today**. That's not an accident to fix; it's a
property worth keeping deliberately. At scale, the right shape isn't to expose
`force` as a tool parameter an agent can flip — it's a **separate, explicitly-named
tool** (e.g. `plan_force_apply`) that a human calls directly, outside agent-driven
flows, logged with `forced: true` and a required reason string. The override should
stay reachable by a human and unreachable as a side effect of an agent being asked
to "just make it work."

## Decision

None of the six gaps above are implemented. This ADR is the artifact instead of the
code: a single-operator tool that is honest about exactly what "single-operator"
excludes, and what each exclusion would concretely cost to remove, so that decision
is made once, on purpose, rather than discovered piecemeal under load.

## Consequences

- A reader (or a reviewer) can evaluate "is this production-ready for a team" without
  guessing — the answer is in this file, itemized, not implied by the absence of a
  section.
- If any of these become real requirements, the corresponding invariant tests
  (`tests/test_invariants.py`) are the right place to encode the *new* guarantees
  (e.g. "two concurrent scheduler runs never both win the same slot") before writing
  the implementation — the same discipline ADR-001 already applies to the planner.
- Building all six pre-emptively would be over-engineering for the tool's actual
  current usage (one channel, one operator, 85+ posts published without a single
  double-book). The trigger for revisiting this ADR is a second concurrent operator
  or tenant, not a hypothetical one.
