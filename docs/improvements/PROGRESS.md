# Implementation Progress

Live tracker. Update after each spec completion.

## Legend

- ✅ Done (merged or in PR-ready branch)
- 🔧 In progress
- ⏳ Next up
- ⬜ Not started

## Phase 0 — Foundation

| Spec | Title | Status | Branch | PR target | Notes |
|------|-------|--------|--------|-----------|-------|
| 0.1 | Recurring API parity audit | ✅ | `spec/0.1-parity-audit` | TBD | `scripts/parity_audit.py` (heuristic enum diff, injectable fetcher, exit-code taxonomy) + quarterly fork-guarded workflow that opens an issue on drift + `make audit` + generated `docs/feature-parity.md`. 17 test cases. Commit `4c59d51`. |
| 0.2 | `from __future__ import annotations` consistency | ✅ | `spec/0.2-future-annotations` | TBD | 48 files migrated, ruff FA enabled, 61 tests added. Commit `bf0bea9`. |
| 0.3 | Backoff jitter | ⏳ | — | — | Touch `_core.py` retry path |
| 0.4 | httpx/httpcore log redaction | ⬜ | — | — | Extend `_logging.py` filter scope |
| 0.5 | Test cassettes via Git LFS | ⬜ | — | — | 28MB → LFS, update CONTRIBUTING.md |

## Phase 1 — Performance & UX Critical Wins

| Spec | Title | Status | Notes |
|------|-------|--------|-------|
| 1.1 | Chat streaming (`ask_stream`) | ⬜ | Endpoint already named `GenerateFreeFormStreamed` |
| 1.2 | Batch downloads concurrent + streaming | ⬜ | Replace sequential loop in `_download_urls_batch` |
| 1.3 | HTTP client reuse across modules | ⬜ | 9 ad-hoc `httpx.AsyncClient` instances |
| 1.4 | Async iterators for pagination | ⬜ | New `iter()` methods on notebooks/artifacts/sources |

## Phase 2 — Concurrency & API Completeness

| Spec | Title | Status |
|------|-------|--------|
| 2.1 | `ConcurrencyLimits` + semaphore throttling | ⬜ |
| 2.2 | `add_many` batch source helpers | ⬜ |
| 2.3 | `research.wait_for_completion` | ⬜ |
| 2.4 | `chat.delete_history` (requires RPC discovery via DevTools) | ⬜ |
| 2.5 | `artifacts.load_audio` (load existing) | ⬜ |

## Phase 3 — MCP Server

| Spec | Title | Status |
|------|-------|--------|
| 3.1 | Native MCP server (stdio + Streamable HTTP) | ⬜ |

## Phase 4 — Architectural Refactors

| Spec | Title | Status |
|------|-------|--------|
| 4.1 | Split `auth.py` (3,205 lines → `auth/` package) | ⬜ |
| 4.2 | Split `_artifacts.py` (2,625 lines → `artifacts/` package) | ⬜ |
| 4.3 | Split `paths.py` | ⬜ |
| 4.4 | Split `cli/session.py` | ⬜ |
| 4.5 | `RPCParamsBuilder` abstraction | ⬜ |
| 4.6 | Structural validation (msgspec/TypedDict) | ⬜ |
| 4.7 | Shape adapters registry for decoder | ⬜ |

## Phase 5 — Observability & Extensibility

| Spec | Title | Status |
|------|-------|--------|
| 5.1 | OpenTelemetry hooks (optional `[telemetry]` extra) | ⬜ |
| 5.2 | Request/response middleware | ⬜ |
| 5.3 | SKILL.md slim + sub-docs | ⬜ |

## Phase 6 — API Parity Gaps + Advanced

| Spec | Title | Status | Notes |
|------|-------|--------|-------|
| 6.1 | Notes-to-source conversion | ⬜ | Requires DevTools network capture |
| 6.2 | Featured Notebooks discovery | ⬜ | Requires DevTools network capture |
| 6.3 | Interactive Audio Mode (exploratory) | ⬜ | Spike first; WebRTC transport |
| 6.4 | Living documents auto-refresh check | ⬜ | Audit existing `refresh_source` |
| 6.5 | Notebook profiles / avatars / banners | ⬜ | Feature in rollout Q2 2026 |

## Workflow notes for Claude Code

1. Read `docs/improvements/spec.md` § for the spec being implemented.
2. Read this PROGRESS.md to see prior decisions.
3. Branch off `main`: `git checkout -b spec/X.Y-short-name`.
4. Write tests asserting acceptance criteria FIRST when feasible.
5. Implement.
6. Validate locally: `uv run ruff check . && uv run pytest tests/unit -q` (skip e2e unless touched).
7. Update this PROGRESS.md (status + branch + commit hash).
8. Commit with `feat: <summary> (Spec X.Y)`.
9. Push to origin (the fork): `git push -u origin spec/X.Y-short-name`.
10. Stop and ask the human about PR target before opening a PR.
