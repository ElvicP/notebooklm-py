# notebooklm-py Improvement Plan

This directory tracks a phased improvement plan for `notebooklm-py`, derived
from a deep codebase audit + feature-parity check against the NotebookLM web UI.

## Documents

- **`spec.md`** — Full functional specification (30 improvements across 6 phases).
  Each improvement spec includes: Context · Design · API · Tests · Acceptance Criteria.
  *(To be placed here from the Claude chat artifact.)*
- **`PROGRESS.md`** — Live status: what's done, what's in flight, what's next.

## Phases at a glance

| Phase | Theme | Specs |
|-------|-------|-------|
| 0 | Foundation (recurring audit + quick hardening) | 0.1 – 0.5 |
| 1 | Performance & UX critical wins (chat streaming, batch downloads) | 1.1 – 1.4 |
| 2 | Concurrency & API completeness (semaphores, add_many, delete_history) | 2.1 – 2.5 |
| 3 | Native MCP server | 3.1 |
| 4 | Architectural refactors (split auth.py, _artifacts.py, RPCParamsBuilder) | 4.1 – 4.7 |
| 5 | Observability & extensibility (OpenTelemetry, middleware) | 5.1 – 5.3 |
| 6 | API parity gaps (notes-to-source, featured notebooks, Interactive Audio) | 6.1 – 6.5 |

## Branching convention

One branch per spec: `spec/<phase>.<num>-<short-name>`.

Example: `spec/0.2-future-annotations`, `spec/1.1-chat-streaming`.

## PR target

Decision recorded in PROGRESS.md per spec:
- **upstream**: PR to `teng-lin/notebooklm-py`
- **fork-only**: keep on `ElvicP/notebooklm-py` for TIBAI internal use
- **hybrid**: ship to fork now, propose upstream when stable
