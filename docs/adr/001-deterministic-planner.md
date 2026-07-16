# ADR-001: Deterministic optimization, not an LLM planner

**Status:** accepted · **Date:** 2026-07-16

## Context

postpeer-pilot decides (a) *when* a video is scheduled and (b) *whether the weekly
post plan should change*. Both decisions could plausibly be delegated to an LLM
("here are the analytics, pick the slots"). This ADR records why they are not.

## Decision

The core decisions are deterministic Python. Claude (or any MCP client) sits **above**
the tool layer as the flexible, natural-language orchestration surface — it decides
*that* something should be scheduled or reviewed, never *what the data says*.

## Rationale

1. **The inputs are structured.** View counts, timestamps, weekday buckets. There is
   no unstructured signal here that a language model would unlock.
2. **The objective is small and explicit.** Keep the weekly volume, re-rank days by
   robust performance, change nothing without stable evidence. That fits in a page of
   auditable code; an LLM would re-derive it probabilistically on every call.
3. **Auditability beats flexibility at the action boundary.** Every plan change can be
   explained from the stored stats ("Sun median 5.4k > Wed 2.9k in both windows").
   "The model felt Sunday was stronger" is not an explanation a creator can act on.
4. **Failure modes differ in kind.** A deterministic planner fails loudly (not
   applyable, with reasons). An LLM planner fails plausibly — it would produce a
   confident, well-formatted, occasionally wrong plan. For a system with real side
   effects, plausible-but-wrong is the expensive failure mode.
5. **Reproducibility enables evaluation.** The backtest harness
   (`python3 -m postpeer_pilot.backtest`) replays history and yields identical
   results on identical inputs. Plan churn, false-adaptation rate and uplift are only
   meaningful because the decision function is a pure function of its inputs.

## Where an LLM *does* belong in this system

- As the **operator interface**: "schedule these three, keep the FD series capped" —
  intent parsing, tool sequencing, error triage. This is what MCP is for.
- Optionally, **upstream of the data**: caption writing, content selection,
  performance *commentary*. Those produce suggestions a human reviews, not side
  effects.
- Any future generative step should come with its own eval gate before it is allowed
  to influence scheduling.

## Consequences

- The planner can be backtested, property-tested (see `tests/test_invariants.py`) and
  explained line by line. Reviewers can verify the damping rules instead of trusting
  prompt wording.
- The system is deliberately less "impressive" in demos — it will refuse to adapt on
  thin data rather than produce an answer. That refusal is the feature.
- If the objective ever grows genuinely fuzzy (e.g. trading off audience segments
  against series fatigue), that would be the moment to revisit this ADR — with an
  eval framework in place first.
