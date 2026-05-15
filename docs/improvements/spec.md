# notebooklm-py — Functional Improvement Specification

> **Status:** Draft v1.0 · 2026-05-15
> **Audience:** Maintainers of `teng-lin/notebooklm-py` · Claude Code agents implementing each phase · Senior reviewers
> **Source-of-truth base version:** notebooklm-py v0.4.1 (PyPI) / `main` snapshot at clone time
> **Reference web UI baseline:** NotebookLM as of May 2026 (post Cinematic Video Overviews / Gemini 3 / Deep Research)

---

## 0. Cover

### 0.1 Purpose

This document specifies a phased improvement plan for `notebooklm-py`, derived from a deep codebase audit (29,113 lines of source analyzed across 59 Python files) and a feature-parity check against the current NotebookLM web UI. Each improvement is documented as a self-contained spec ready for direct execution by an implementation agent (e.g. Claude Code).

### 0.2 Why these improvements

The audit identified three concern classes:

1. **Performance/UX gaps** — capabilities the underlying API supports but the library doesn't expose (chat streaming, batch concurrency).
2. **Architectural debt** — large multi-responsibility modules (`auth.py` 3,205 lines, `_artifacts.py` 2,625 lines) that will become risky as Google's protocol drifts.
3. **API parity drift** — features added to NotebookLM web UI in 2025-2026 not yet exposed (Interactive Audio Mode, Notes-to-Source, Featured Notebooks discovery).

### 0.3 Phasing principles

- **Phase 0** is foundational — establishes the audit cadence that prevents the next parity drift.
- **Phases 1-2** ship user-visible wins quickly and unblock real workloads.
- **Phase 3** is strategic positioning (MCP server).
- **Phase 4** is the architectural rework — risky but it pays for itself in the next 5 Google protocol changes.
- **Phases 5-6** close out polish + edge-case features.

### 0.4 Reading guide

Each improvement spec contains five fixed sections:

| Section | Purpose |
|---|---|
| **Context** | Current state, observed problem, evidence (file:line references) |
| **Design** | Approach chosen, alternatives rejected, key invariants |
| **API** | Public signatures, types, error model, examples |
| **Tests** | Required coverage (unit/integration/e2e), regression guardrails |
| **Acceptance Criteria** | Concrete pass/fail conditions before the PR merges |

---

## 1. Executive Summary

### 1.1 Improvements at a glance

| # | Improvement | Phase | Effort | Risk | Breaking? |
|---|---|---|---|---|---|
| 0.1 | Recurring API parity audit | 0 | S | Low | No |
| 0.2 | `from __future__` consistency | 0 | XS | None | No |
| 0.3 | Backoff jitter | 0 | XS | Low | No |
| 0.4 | httpx/httpcore log redaction | 0 | S | Low | No |
| 0.5 | Cassettes → Git LFS | 0 | S | Low | No |
| 1.1 | Chat streaming (`ask_stream`) | 1 | M | Medium | No (additive) |
| 1.2 | Batch downloads concurrent + streaming | 1 | M | Low | No |
| 1.3 | HTTP client reuse across modules | 1 | M | Medium | No |
| 1.4 | Async iterators for pagination | 1 | S | Low | No (additive) |
| 2.1 | `ConcurrencyLimits` + semaphore throttling | 2 | M | Low | No (additive) |
| 2.2 | `add_many` batch source helpers | 2 | M | Low | No (additive) |
| 2.3 | `research.wait_for_completion` | 2 | S | Low | No (additive) |
| 2.4 | `chat.delete_history` | 2 | S | Medium | No (additive) |
| 2.5 | `artifacts.load_audio` (load existing) | 2 | S | Low | No (additive) |
| 3.1 | Native MCP server | 3 | L | Medium | No (new module) |
| 4.1 | Split `auth.py` into `auth/` package | 4 | L | High | No (compat shim) |
| 4.2 | Split `_artifacts.py` by domain | 4 | L | Medium | No (compat shim) |
| 4.3 | Split `paths.py` | 4 | M | Low | No |
| 4.4 | Split `cli/session.py` | 4 | M | Low | No |
| 4.5 | `RPCParamsBuilder` abstraction | 4 | L | High | No (internal) |
| 4.6 | Structural validation (msgspec/TypedDict) | 4 | L | Medium | No (internal) |
| 4.7 | Shape adapters registry for decoder | 4 | M | Medium | No (internal) |
| 5.1 | OpenTelemetry hooks | 5 | M | Low | No (optional extra) |
| 5.2 | Request/response middleware | 5 | M | Low | No (additive) |
| 5.3 | SKILL.md slim + sub-docs | 5 | S | None | No |
| 6.1 | Notes-to-Source conversion | 6 | M | Medium | No (additive) |
| 6.2 | Featured Notebooks discovery | 6 | S | Low | No (additive) |
| 6.3 | Interactive Audio Mode (exploratory) | 6 | XL | High | No (additive) |
| 6.4 | Living-documents auto-refresh check | 6 | S | Low | No |
| 6.5 | Notebook profiles/avatars/banners | 6 | M | Low | No (additive) |

**Effort scale:** XS = <1 day · S = 1-2 days · M = 3-5 days · L = 1-2 weeks · XL = >2 weeks
**Total estimated effort:** ~14-18 weeks of focused work (single developer) or ~6-8 weeks parallelizable across 2-3 contributors.

### 1.2 Dependency graph

```
Phase 0 ──┐
          │
          ├──→ Phase 1 ──┐
          │              │
          │              ├──→ Phase 3 (MCP server consumes Phase 1 streaming + Phase 2 batch)
          │              │
          │              └──→ Phase 2 ──┘
          │
          ├──→ Phase 4 (independent, can run parallel after Phase 0)
          │
          └──→ Phase 5 (independent)

Phase 6: items 6.1, 6.2, 6.4, 6.5 independent; 6.3 (Interactive Audio) requires research spike first.
```

### 1.3 Risk matrix

| Risk | Mitigation |
|---|---|
| Google changes RPC method IDs mid-phase | Phase 0.1 establishes the parity audit; Phase 4.7 (shape adapters) makes drift easier to handle |
| Breaking change to public API in Phase 4 splits | Compat shims at each old module path; `__getattr__` deprecation warnings |
| MCP server (Phase 3) duplicates effort with `claude-world/notebooklm-skill` | Coordinate with that maintainer; offer co-maintenance or upstream merge |
| Interactive Audio (6.3) requires WebRTC which is out of `httpx` scope | Mark as exploratory; ship only if proof-of-concept validates feasibility |
| Refactor invalidates 28MB of recorded cassettes | Phase 0.5 (Git LFS) reduces re-record cost; each Phase 4 PR re-records affected cassettes only |

---

## 2. Phase 0 — Foundation (Recurring Process + Quick Hardening)

### Spec 0.1 — Recurring API Parity Audit

#### Context

This audit identified that the library has good coverage of NotebookLM web UI features (~92%), but no recurring process detects when Google ships new features. The Cinematic Video Overview (March 2026) was picked up promptly because the maintainer was paying attention — but that's brittle.

#### Design

Introduce a quarterly automated audit driven by:
1. A `scripts/parity_audit.py` that scrapes a curated list of NotebookLM feature URLs (Google blog posts, support pages) and compares discovered features against the library's `RPCMethod` enum + `ArtifactType` enum.
2. A new GitHub Actions workflow `.github/workflows/parity-audit.yml` that runs on the 1st of each quarter and opens an issue if drift is detected.
3. A canonical `docs/feature-parity.md` doc that the audit updates automatically.

The audit is heuristic — it can't perfectly detect new RPC method IDs (those require network capture). What it CAN detect is the existence of a new Studio output type, a new source type, or a new chat configuration option. False positives are acceptable.

#### API

New script:
```bash
python scripts/parity_audit.py --output docs/feature-parity.md --diff-only
```

New CI surface: `parity-audit.yml` (quarterly cron + manual dispatch).

#### Tests

- Unit: parsing of the feature pages (mock HTTP responses).
- Integration: run the audit against a frozen baseline and assert no false negatives on features known to exist.

#### Acceptance Criteria

- [ ] Audit script runs end-to-end on `make audit` locally.
- [ ] Workflow opens a GitHub issue when a new feature is detected.
- [ ] `docs/feature-parity.md` is generated automatically and committed.
- [ ] The audit covers at minimum: Studio output types, source types, chat configuration options, sharing options.

---

### Spec 0.2 — `from __future__ import annotations` Consistency

#### Context

`from __future__ import annotations` appears in only 10 of 59 source files (17%). Inconsistency means some files evaluate `list[Notebook]` lazily as strings (no runtime cost) and others evaluate eagerly. On Python 3.10 (the minimum supported), this matters for startup time and for places where type hints are introspected.

#### Design

Add the import to all 49 remaining `.py` files in `src/notebooklm/`. Configure `ruff` to enforce it.

#### API

No public API change.

#### Tests

- A new `tests/unit/test_future_annotations.py` that walks `src/notebooklm/` and asserts every file starts with `from __future__ import annotations` (skipping `__init__.py` files if they have side-effect imports that must run early).

#### Acceptance Criteria

- [ ] All 49 files updated.
- [ ] `ruff` rule `FA100` enabled with `select = [..., "FA"]` in `pyproject.toml`.
- [ ] Mypy passes unchanged.
- [ ] Test asserts the invariant.

---

### Spec 0.3 — Backoff Jitter

#### Context

`_core.py:_perform_authed_post` uses `min(2 ** attempt, 30)` for server-error backoff. Pure exponential. Under concurrent load (e.g. an agent pool retrying after a Google blip), all clients retry in lockstep → thundering herd.

#### Design

Add jitter: `delay = min(2 ** attempt, 30) + random.uniform(0, base * 0.1)`. Use `random.Random` seeded per-process (not the global RNG) to keep test determinism via `Random(seed)` injection.

#### API

Internal change only. The new optional kwarg `jitter_rng: random.Random | None = None` on `ClientCore.__init__` is for tests; production callers use the default.

#### Tests

- Unit test with a deterministic `jitter_rng` confirming the delay distribution.
- Regression: existing retry tests pass with the new jitter (re-record cassettes if timing-sensitive).

#### Acceptance Criteria

- [ ] All retry paths (server error, network error, rate limit) carry jitter.
- [ ] Test confirms delay variance.
- [ ] No existing tests broken.

---

### Spec 0.4 — Logging Redaction for httpx/httpcore

#### Context

`_logging.py` installs a `RedactingFilter` on the package logger that scrubs CSRF tokens, OAuth credentials, and session cookies. Excellent — except `httpx` and `httpcore` have their own loggers that the user can enable independently (`logging.getLogger("httpx").setLevel(logging.DEBUG)`), and those loggers emit full URLs including auth query params and cookie headers.

#### Design

In `configure_logging()`:
1. Install the same `RedactingFilter` on `logging.getLogger("httpx")` and `logging.getLogger("httpcore")`.
2. Document the behavior in `docs/troubleshooting.md`.
3. Add an opt-out env var `NOTEBOOKLM_REDACT_HTTPX=0` for advanced debugging (off by default = redaction always on).

#### API

No public API change. New env var documented.

#### Tests

- Unit test: enable httpx DEBUG, emit a log message with a sensitive URL, assert the rendered output is redacted.
- Test the opt-out env var.

#### Acceptance Criteria

- [ ] httpx and httpcore loggers redact by default.
- [ ] Opt-out via `NOTEBOOKLM_REDACT_HTTPX=0` works.
- [ ] Documented in troubleshooting.md and SECURITY.md.

---

### Spec 0.5 — Test Cassettes via Git LFS

#### Context

`tests/cassettes/` is 28MB. Every `git clone` pulls them, slowing remote contributors and bloating Docker build contexts. Git history accumulates them. They are binary-ish (YAML with base64 blobs); they don't merge well; they don't compress well in git's delta format.

#### Design

1. Configure Git LFS for `tests/cassettes/*.yaml`.
2. Update `CONTRIBUTING.md` with `git lfs install` step.
3. Pre-existing cassettes are migrated via `git lfs migrate import --include="tests/cassettes/*.yaml" --everything`.
4. Add a `make cassettes-fetch` target as a safety net for contributors who skip LFS setup.

#### API

No public API. New developer workflow step.

#### Tests

- CI runs with `git lfs pull` step.
- `make cassettes-fetch` works offline (skips if cassettes already present).

#### Acceptance Criteria

- [ ] Cassettes moved to LFS.
- [ ] `git clone --no-checkout && git lfs pull` works for new contributors.
- [ ] CONTRIBUTING.md updated.
- [ ] CI green.

---

## 3. Phase 1 — Performance & UX Critical Wins

### Spec 1.1 — Chat Streaming (`chat.ask_stream`)

#### Context

The NotebookLM endpoint is literally named `GenerateFreeFormStreamed` (`rpc/types.py:207`), but `ChatAPI.ask` buffers the entire response before returning. Users wait 30-60 seconds for the full answer before seeing anything. Every modern LLM client (OpenAI, Anthropic, Google Gemini) streams; not streaming is the most visible UX gap in the library.

Evidence:
- `_chat.py:136` — `response = await self._core.query_post(...)` returns the full `httpx.Response`.
- `_core.py:859` — `response = await client.post(url, content=body)` — non-streaming.

#### Design

Introduce `chat.ask_stream()` as a sibling of `chat.ask()`, returning an async iterator of `ChatChunk` deltas. The existing `ask()` is preserved unchanged (it can be reimplemented as a thin wrapper that joins the chunks, but only as a follow-up PR to keep this change minimal).

The endpoint emits chunked responses in Google's batchexecute streaming format: lines prefixed by a count, then a JSON array. The decoder needs to parse incrementally. Reuse `parse_chunked_response` from `rpc/decoder.py` but adapt it to an async generator pattern.

**Cancellation contract:** if the consumer of the async iterator breaks early, the underlying HTTP stream MUST be closed via the existing `client.stream()` context manager. No best-effort cleanup needed — `httpx` handles this.

#### API

New types:
```python
@dataclass(frozen=True)
class ChatChunk:
    """A single delta in a streaming chat response."""
    delta: str                          # New text since the last chunk
    accumulated: str                    # Full text so far (convenience)
    references: list[ChatReference]     # Citations seen so far
    is_final: bool                      # True for the last chunk only
    conversation_id: str | None         # Set once the server returns it
```

New method:
```python
async def ask_stream(
    self,
    notebook_id: str,
    question: str,
    source_ids: list[str] | None = None,
    conversation_id: str | None = None,
) -> AsyncIterator[ChatChunk]:
    """Stream a chat response as it is generated.

    Yields ``ChatChunk`` objects progressively. The final chunk carries
    ``is_final=True`` and the complete reference list.

    Example:
        async for chunk in client.chat.ask_stream(nb_id, "Summarize"):
            print(chunk.delta, end="", flush=True)
            if chunk.is_final:
                refs = chunk.references
    """
```

#### Tests

- Unit: cassette-based test recording a streamed chat session and asserting the chunks decode correctly.
- Unit: cancellation test — break out of the `async for` loop early, assert the underlying stream is closed (mock `httpx` and assert `aclose` was called).
- Unit: token-count test — sum of `delta` lengths equals length of final `accumulated`.
- Integration: smoke test against live NotebookLM (e2e marker) confirming streaming works end-to-end.

#### Acceptance Criteria

- [ ] `ChatChunk` dataclass added to public types.
- [ ] `chat.ask_stream()` returns an `AsyncIterator[ChatChunk]`.
- [ ] First chunk arrives within 2s of the request (vs ~30s for `ask()`).
- [ ] Cancellation closes the stream cleanly.
- [ ] CLI gains `notebooklm ask --stream` flag (Phase 1.1.b).
- [ ] Documented in `docs/python-api.md` with full example.

---

### Spec 1.2 — Batch Downloads Concurrent + Streaming

#### Context

`_artifacts.py:_download_urls_batch` (`L2195`) iterates URLs sequentially with `for url, path in urls_and_paths:` and uses `response.content` (full in-memory load) + `output_file.write_bytes`. Compare to `_download_url` (`L2287`) which streams correctly per file.

Impact on `--all` flag (download all artifacts of a notebook): 5 audio files × 50MB each = 250MB held in RAM at once, plus 5× sequential wall-clock.

#### Design

Replace `_download_urls_batch` body:
1. Each URL handled by an inner coroutine that uses `client.stream()` (mirroring `_download_url`).
2. Wrap the coroutines in `asyncio.gather(...)` with a `Semaphore(max_concurrency)` to throttle (default `max_concurrency=4` — Google's CDN can handle parallel downloads fine but more isn't worth it).
3. Reuse one `httpx.AsyncClient` for all URLs (TCP connection pool benefit).
4. `DownloadResult.failed` carries the per-URL exception; `succeeded` carries paths.

The existing security validations (HTTPS-only, trusted domain allowlist, HTML-content-type guard) MUST be preserved on each coroutine.

**Backward compatibility:** the signature `_download_urls_batch(urls_and_paths)` is preserved. New optional kwarg `max_concurrency: int = 4`.

#### API

```python
async def _download_urls_batch(
    self,
    urls_and_paths: list[tuple[str, str]],
    *,
    max_concurrency: int = 4,
) -> DownloadResult:
    """Download many files concurrently, each via streaming.

    Args:
        urls_and_paths: List of (url, output_path) tuples.
        max_concurrency: Maximum simultaneous downloads. Default 4.
    """
```

#### Tests

- Unit: mock `httpx` to record call ordering; assert N concurrent calls when `max_concurrency=N`.
- Unit: 10 URLs with `max_concurrency=2` — verify timing (with patched sleep) that no more than 2 run in parallel.
- Unit: one URL fails → `failed` has the entry, `succeeded` has the others.
- Unit: streaming — assert that no URL response is fully loaded into memory (mock `aiter_bytes` and verify per-chunk dispatch).
- Regression: existing `download_*` tests pass unchanged.

#### Acceptance Criteria

- [ ] Wall-clock time on 10-file batch decreases by ≥3x with `max_concurrency=4`.
- [ ] Peak memory during batch download is bounded by `chunk_size × max_concurrency` (64KB × 4 = 256KB), not by total payload size.
- [ ] All security validations preserved.
- [ ] Documented in changelog as performance fix, not a breaking change.

---

### Spec 1.3 — HTTP Client Reuse Across Modules

#### Context

The codebase instantiates 9 ad-hoc `httpx.AsyncClient` outside `_core.py`:
- `_sources.py` × 3 (upload, finalize, drive)
- `_artifacts.py` × 2 (single-file download, batch download)
- `auth.py` × 3 (token fetch, cookie rotation, account probe)
- Plus 1 in `_core.py` (legitimate — main pool)

Each ad-hoc instance = fresh TCP+TLS+DNS, no pooling benefit, no shared retry/timeout config.

The justification in some cases (e.g. Scotty upload requires the live cookie jar) is real but solvable by exposing a `_core.get_http_client_with_cookies(cookies=jar)` factory.

#### Design

1. Add `ClientCore.get_http_client_for_upload(cookies: httpx.Cookies, *, timeout: httpx.Timeout) -> httpx.AsyncClient` as a factory that returns a configured client. Document that the caller owns the lifecycle (must `async with`).
2. For auth probes that run BEFORE the main client is open: keep ad-hoc but extract them to a single `auth/probe.py` module after Phase 4.1.
3. For artifact downloads: reuse the main `_core._http_client` cookie jar by passing it as `cookies=self._core.get_http_client().cookies` to a per-download `httpx.AsyncClient` (this is what the upload path already does, but now centralized).

#### API

```python
# New on ClientCore
def get_upload_client(
    self,
    *,
    cookies: httpx.Cookies | None = None,
    timeout: httpx.Timeout | None = None,
) -> httpx.AsyncClient:
    """Return a fresh httpx client configured for upload/download flows.

    Reuses the cookie jar from the main client by default. The caller
    owns the lifecycle (must close via async context manager).
    """
```

#### Tests

- Unit: mock `httpx.AsyncClient` constructor; count instantiations across a download batch.
- Regression: all auth flows (initial login, refresh, keepalive) still pass.

#### Acceptance Criteria

- [ ] Number of ad-hoc `httpx.AsyncClient(...)` outside `_core.py` and `auth/probe.py` is ≤1 per file (the legitimate Scotty/CDN cases).
- [ ] All ad-hoc instances reuse the core's cookie jar.
- [ ] No regression in upload/download tests.

---

### Spec 1.4 — Async Iterators for Pagination

#### Context

`client.notebooks.list()` returns a fully materialized `list[Notebook]`. For users with hundreds of notebooks, that's wasteful (memory + initial latency). The underlying RPC supports pagination via `pageToken`, but the library always fetches all pages eagerly.

Same pattern in `artifacts.list()` and `sources.list()`.

#### Design

Add `iter()` variants alongside `list()`:
- `client.notebooks.iter() -> AsyncIterator[Notebook]`
- `client.artifacts.iter(notebook_id, kind: ArtifactType | None = None) -> AsyncIterator[Artifact]`
- `client.sources.iter(notebook_id) -> AsyncIterator[Source]`

Internally, `iter()` fetches one page at a time and yields items lazily. `list()` keeps its current semantics (it can be reimplemented in terms of `iter()` later but doesn't have to be in this PR).

The new `artifacts.iter(kind=...)` supports server-side filtering when possible — eliminating today's pattern of "list all then client-side filter" (`list_audio`, `list_video`, etc. become wrappers).

#### API

```python
async def iter(
    self,
    notebook_id: str,
    *,
    kind: ArtifactType | None = None,
    page_size: int = 50,
) -> AsyncIterator[Artifact]:
    """Iterate artifacts lazily, fetching pages on demand.

    Example:
        async for art in client.artifacts.iter(nb_id, kind=ArtifactType.AUDIO):
            if art.status == "completed":
                await client.artifacts.download_audio(nb_id, f"{art.id}.mp3")
    """
```

#### Tests

- Unit: mock 3 pages of results; assert exactly 3 RPC calls made when iterating.
- Unit: early break from `async for` — assert no further pages fetched.
- Unit: `iter(kind=AUDIO)` filters server-side when supported, client-side as fallback.

#### Acceptance Criteria

- [ ] `iter()` available on `notebooks`, `artifacts`, `sources`.
- [ ] Memory profile flat regardless of total item count.
- [ ] Backward-compat: `list()` returns same shape as today.

---

## 4. Phase 2 — Concurrency & API Completeness

### Spec 2.1 — `ConcurrencyLimits` + Semaphore Throttling

#### Context

The library has zero `asyncio.Semaphore` usage. Users who `asyncio.gather` over many operations rely entirely on `httpx`'s connection pool (`max_connections=100`) for throttling. Result: under heavy fan-out, the lib opens up to 100 simultaneous RPC calls and Google promptly returns 429s. The lib then retries those (with retry budget), so the user pays double.

#### Design

Introduce a `ConcurrencyLimits` dataclass parallel to the existing `ConnectionLimits`:

```python
@dataclass(frozen=True)
class ConcurrencyLimits:
    """Application-level concurrency throttle.

    Limits the number of in-flight requests per logical operation type.
    Independent of httpx connection pool (which throttles TCP-level).
    """
    rpc_calls: int = 8           # Concurrent batchexecute RPCs
    downloads: int = 4           # Concurrent artifact downloads
    uploads: int = 2             # Concurrent file uploads
```

`ClientCore` owns one `asyncio.Semaphore` per limit. Acquire-release wrappers in each module:

```python
# In ArtifactsAPI._download_url:
async with self._core.acquire_download_slot():
    # ... existing download body ...
```

Default values chosen conservatively: Google's actual rate limits are not public, but empirically 8 concurrent RPCs is safe and 4 concurrent downloads doesn't trip CDN throttling.

#### API

```python
NotebookLMClient(
    auth,
    concurrency=ConcurrencyLimits(rpc_calls=16, downloads=8),
)
```

#### Tests

- Unit: with `ConcurrencyLimits(rpc_calls=2)`, gather 10 RPCs and assert exactly 2 are in-flight at any moment (via barrier mocks).
- Integration: verify 429 rate from Google decreases substantially.

#### Acceptance Criteria

- [ ] `ConcurrencyLimits` exported from public API.
- [ ] Semaphores integrated into `rpc_call`, `_download_url`, `_upload_file_streaming`.
- [ ] Default values documented as "safe for typical agent workloads".
- [ ] No regression in single-call latency.

---

### Spec 2.2 — `add_many` Batch Source Helpers

#### Context

Adding 50 URLs to a notebook today requires the user to write:
```python
results = await asyncio.gather(*[
    client.sources.add_url(nb_id, url) for url in urls
])
```
Without throttling (see 2.1). And there's no equivalent for mixed source types (some URLs, some PDFs, some YouTube).

#### Design

```python
SourceSpec = Union[
    UrlSourceSpec,      # {"type": "url", "value": str}
    FileSourceSpec,     # {"type": "file", "path": Path}
    TextSourceSpec,     # {"type": "text", "value": str, "title": str}
    YouTubeSourceSpec,  # {"type": "youtube", "value": str}
    DriveSourceSpec,    # {"type": "drive", "value": str, "mime_type": DriveMimeType}
]

@dataclass
class BatchAddResult:
    succeeded: list[Source]
    failed: list[tuple[SourceSpec, Exception]]
```

The method runs each addition through the appropriate `add_*` and aggregates results. Throttled via the `uploads` and `rpc_calls` semaphores from 2.1.

#### API

```python
async def add_many(
    self,
    notebook_id: str,
    sources: list[SourceSpec],
    *,
    max_concurrency: int | None = None,  # Defaults to ConcurrencyLimits
    wait_for_ready: bool = False,
    fail_fast: bool = False,
) -> BatchAddResult:
    """Add multiple sources concurrently.

    If ``fail_fast=True``, the first failure cancels remaining additions.
    Otherwise all are attempted and failures returned in ``failed``.
    """
```

#### Tests

- Unit: 10 mixed sources, 2 simulated failures → succeeded.len=8, failed.len=2.
- Unit: `fail_fast=True` with failure at index 3 → cancels remaining.
- Unit: respects semaphore limit.

#### Acceptance Criteria

- [ ] `add_many` accepts heterogeneous source specs.
- [ ] Partial success returned cleanly.
- [ ] CLI gains `notebooklm source add-many --from-file urls.txt`.

---

### Spec 2.3 — `research.wait_for_completion`

#### Context

`artifacts.wait_for_completion()` is excellent — exponential backoff, not-found handling, transient error retry, timeout clamping. `research.poll()` returns status but offers no `wait_for`. Agents/pipelines re-implement the polling loop, inconsistently.

#### Design

Extract the polling-loop kernel from `artifacts.wait_for_completion` into a private `_polling.py` module:

```python
async def wait_for(
    *,
    poll_fn: Callable[[], Awaitable[T]],
    is_done: Callable[[T], bool],
    is_failed: Callable[[T], bool],
    initial_interval: float = 2.0,
    max_interval: float = 10.0,
    timeout: float = 300.0,
    transient_errors: tuple[type[Exception], ...] = (NetworkError, RPCTimeoutError, ServerError),
) -> T:
    ...
```

Both `artifacts.wait_for_completion` and the new `research.wait_for_completion` become thin wrappers.

#### API

```python
async def wait_for_completion(
    self,
    notebook_id: str,
    *,
    timeout: float = 600.0,  # Research can take longer than artifacts
    initial_interval: float = 5.0,
) -> dict[str, Any]:
    """Wait for the latest research task in this notebook to complete."""
```

#### Tests

- Unit: research that completes after 3 polls.
- Unit: research that times out.
- Unit: transient error retry.
- Regression: existing `artifacts.wait_for_completion` tests still pass.

#### Acceptance Criteria

- [ ] `research.wait_for_completion` symmetric with `artifacts.wait_for_completion`.
- [ ] Polling kernel shared via `_polling.py`.
- [ ] No regression.

---

### Spec 2.4 — `chat.delete_history`

#### Context

NotebookLM web UI (Dec 2025) added "Delete chat history" with privacy implications. Library exposes `get_history()` but not `delete_history()`. Agents that log queries (e.g. via Phase 5.1 OpenTelemetry) need a way to purge sensitive conversations.

#### Design

Discovery: the web UI's "Delete chat history" likely maps to a new batchexecute RPC. Step 1 is network capture (see `docs/rpc-development.md` for the existing process). Once identified, add to `RPCMethod` enum and implement the call.

#### API

```python
async def delete_history(
    self,
    notebook_id: str,
    *,
    conversation_id: str | None = None,
) -> bool:
    """Delete chat history for a notebook.

    Args:
        notebook_id: The notebook ID.
        conversation_id: If specified, delete only that conversation.
            If None, delete all conversation history for the notebook.

    Returns:
        True if deletion confirmed.
    """
```

#### Tests

- Unit: cassette-based.
- Integration (e2e): create notebook, ask question, delete, verify `get_history()` returns empty.

#### Acceptance Criteria

- [ ] RPC method ID discovered and added to `RPCMethod`.
- [ ] `chat.delete_history` implemented.
- [ ] CLI gains `notebooklm chat clear-history`.
- [ ] Documented in privacy section.

---

### Spec 2.5 — `artifacts.load_audio` (Load Existing)

#### Context

NotebookLM web UI has a "Load" button that brings a previously-generated Audio Overview back into the active player without regenerating. The library can `list_audio()` to find it but offers no convenience wrapper to fetch metadata + URL for the most recent one.

#### Design

Thin convenience wrapper:

```python
async def load_audio(
    self,
    notebook_id: str,
    *,
    artifact_id: str | None = None,
) -> Artifact | None:
    """Load an existing Audio Overview without regenerating.

    Args:
        notebook_id: The notebook ID.
        artifact_id: Specific audio to load. If None, returns the most
            recently created one.

    Returns:
        The Artifact with status=completed and url populated, or None
        if no audio exists.
    """
```

Internally: `list_audio()` → filter by `is_complete` → sort by `created_at` desc → return first.

Analogous helpers for `load_video`, `load_slide_deck`, etc.

#### API

See above. Add `load_*` for each artifact type that supports persistence.

#### Tests

- Unit: notebook with 3 audio overviews → `load_audio()` returns the latest.
- Unit: notebook with no audio → returns None.
- Unit: `artifact_id` specified → returns that exact one.

#### Acceptance Criteria

- [ ] `load_*` family added for all stable artifact types.
- [ ] CLI: `notebooklm download audio --load-latest`.

---

## 5. Phase 3 — Native MCP Server

### Spec 3.1 — Native MCP Server

#### Context

The Model Context Protocol (MCP) ecosystem is the de-facto standard for exposing tools to LLM agents (Claude Desktop, Cursor, Gemini CLI, etc.). Today there's an external wrapper (`claude-world/notebooklm-skill`) that adds an MCP server on top of notebooklm-py, but it exposes only 13 of ~50 available operations. A native MCP server in this repo:
1. Provides full surface area (every public API method becomes a tool).
2. Eliminates the external dependency for the most common consumption pattern.
3. Lets the maintainer evolve the tool schema in lockstep with the underlying library.

#### Design

New module: `src/notebooklm/mcp/`. Entry point: `python -m notebooklm.mcp` and console script `notebooklm-mcp`.

**Transports:** stdio (Claude Desktop, Cursor) + Streamable HTTP (web/cloud agents). The library `fastmcp` provides both.

**Authentication:** the MCP server uses the same `storage_state.json` as the rest of the library. No re-auth needed. Path resolution via `notebooklm.paths`.

**Tool surface:** auto-generated from the public API via a registry pattern:

```python
@mcp_tool(
    name="notebooklm_create_notebook",
    description="Create a new NotebookLM notebook with optional initial sources.",
)
async def create_notebook(title: str, sources: list[str] | None = None) -> dict:
    ...
```

A tool decorator registers the function with name, description, JSON schema (derived from type hints), and an async dispatcher.

**Tool naming:** `notebooklm_<resource>_<verb>` convention. ~40 tools total:
- `notebooklm_create_notebook`, `notebooklm_list_notebooks`, `notebooklm_delete_notebook`, ...
- `notebooklm_add_source_url`, `notebooklm_add_source_file`, ...
- `notebooklm_generate_audio`, `notebooklm_generate_cinematic_video`, ...
- `notebooklm_ask`, `notebooklm_ask_stream`, `notebooklm_get_history`, ...
- `notebooklm_research_start`, `notebooklm_research_wait`, ...
- `notebooklm_share_notebook`, `notebooklm_get_share_status`, ...

**Error mapping:** library exceptions become structured MCP error responses with `code` (machine-readable) + `message` (human-readable).

**Long-running operations:** `generate_*` returns a `task_id`. Consumers poll via `notebooklm_poll_status`. Streaming chat uses the MCP streaming protocol (server-sent events).

#### API

CLI:
```bash
notebooklm-mcp --transport stdio
notebooklm-mcp --transport http --port 8080
notebooklm-mcp --transport http --port 8080 --auth-token <bearer>
```

Configuration in `~/.config/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "notebooklm": {
      "command": "notebooklm-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

New dependency: `fastmcp >= 2.0` (optional extra `[mcp]`).

#### Tests

- Unit: each tool decorator generates the correct JSON schema.
- Unit: tool dispatcher correctly maps args → underlying API call.
- Integration: stdio transport — send `list_tools`, get full list; call a tool, get expected response.
- Integration: HTTP transport — bearer-token auth, error responses.
- E2E: actual Claude Desktop session calling `notebooklm_create_notebook` and `notebooklm_ask`.

#### Acceptance Criteria

- [ ] `notebooklm-mcp` console script available after `pip install "notebooklm-py[mcp]"`.
- [ ] Both stdio and HTTP transports work.
- [ ] All ~40 tools auto-registered from the public API.
- [ ] Schema generation tested for every tool.
- [ ] Documented in new `docs/mcp.md` with Claude Desktop / Cursor / Gemini CLI integration snippets.
- [ ] Co-coordination with `claude-world/notebooklm-skill` maintainer (issue opened, migration path documented).

---

## 6. Phase 4 — Architectural Refactors

### Spec 4.1 — Split `auth.py` into `auth/` Package

#### Context

`auth.py` is 3,205 lines / 70 top-level definitions mixing five distinct concerns:
- `AuthTokens` dataclass + lifecycle (lines ~370-540)
- Cookie jar serialization, snapshot/delta saves, file-locking (lines ~70-110, ~1611-2316)
- Account/profile management, email extraction, authuser routing (lines ~1005-1289)
- CSRF/session-ID HTML extraction from NotebookLM homepage (lines ~854-1004)
- Browser cookie scraping (rookiepy + Firefox containers) (lines ~700-754)

This is the file most likely to need surgery when Google changes anything auth-related, and it's the hardest to navigate.

#### Design

Split into a package:

```
src/notebooklm/auth/
├── __init__.py          # Re-exports for backward compatibility
├── tokens.py            # AuthTokens dataclass, fetch_tokens, from_storage
├── cookies.py           # Cookie jar serialization, snapshot/delta, file-locking
├── accounts.py          # Account/profile management, authuser routing
├── extraction.py        # CSRF/session-ID extraction, extract_wiz_field
├── browser.py           # Browser cookie scraping (rookiepy + Firefox)
└── refresh.py           # _run_refresh_cmd, _rotate_cookies, _probe_authuser
```

**Backward compatibility:** `src/notebooklm/auth/__init__.py` re-exports all current public symbols. The old `from notebooklm.auth import AuthTokens` keeps working. Old internal callers using `from notebooklm.auth import _is_google_domain` get a `DeprecationWarning` via `__getattr__` indicating the new internal path.

**Migration approach:** start with `tokens.py` (smallest, most public), then `accounts.py`, then `extraction.py`, then `cookies.py` (most coupled), then `browser.py` and `refresh.py`. Each in its own commit. Each commit re-runs the full test suite.

#### API

No public API change. Internal imports updated.

#### Tests

- All existing `tests/unit/test_auth*.py` tests pass unchanged.
- New `tests/unit/test_auth_layout.py` asserting the new module structure exists and the public re-exports are complete.
- Boundary tests: no submodule imports from another submodule beyond what the layered design allows (use `import_linter` or simple AST checks).

#### Acceptance Criteria

- [ ] Six submodules in `src/notebooklm/auth/`.
- [ ] All existing imports `from notebooklm.auth import X` still work.
- [ ] Tests green throughout the migration (each PR is a green checkpoint).
- [ ] Layer-boundary lint rule added.

---

### Spec 4.2 — Split `_artifacts.py` by Domain

#### Context

`_artifacts.py` is 2,625 lines / 7 classes containing generate + list + poll + wait + download + export + revise + parse_data_table all in one. The mind-map decoupling (T6.F, December 2025) already established the pattern.

#### Design

Split into a package:

```
src/notebooklm/artifacts/
├── __init__.py          # ArtifactsAPI facade class
├── _shared.py           # DownloadResult, _parse_generation_result, common helpers
├── audio.py             # generate_audio, download_audio, list_audio
├── video.py             # generate_video, generate_cinematic_video, download_video
├── slides.py            # generate_slide_deck, revise_slide, download_slide_deck
├── reports.py           # generate_report, generate_study_guide, export_report
├── data_table.py        # generate_data_table, export_data_table, _parse_data_table
├── interactive.py       # generate_quiz, generate_flashcards, _format_interactive_content
├── mind_map.py          # (Already exists as _mind_map.py — moved here)
├── infographic.py       # generate_infographic, download_infographic
└── downloads.py         # _download_url, _download_urls_batch (post Phase 1.2)
```

The `ArtifactsAPI` facade in `__init__.py` aggregates the module functions and exposes them as methods. Each submodule defines pure functions that take `ClientCore` as first arg.

This mirrors the `_mind_map.py` extraction pattern.

#### API

No public API change. `client.artifacts.generate_audio(...)` still works.

#### Tests

- Existing artifact tests pass.
- New `tests/unit/test_artifacts_layout.py` for the boundary.

#### Acceptance Criteria

- [ ] Nine submodules + facade.
- [ ] Public surface unchanged.
- [ ] No submodule >800 lines.
- [ ] Tests green.

---

### Spec 4.3 — Split `paths.py`

#### Context

`paths.py` (402 lines) handles storage path resolution, profile resolution, context file management, and legacy migration in one module. Less critical than `auth.py` but same pattern.

#### Design

```
src/notebooklm/paths/
├── __init__.py          # Re-exports
├── storage.py           # get_storage_path, _account_context_path
├── profiles.py          # resolve_profile, list_profiles
└── context.py           # get_context_path, set/get_context_value
```

#### Tests

- Existing tests pass.

#### Acceptance Criteria

- [ ] Three submodules.
- [ ] Backward compat shim.
- [ ] Tests green.

---

### Spec 4.4 — Split `cli/session.py`

#### Context

`cli/session.py` is 2,319 lines covering login, auth (logout/inspect/check/refresh), use, status, and clear. Each command is roughly 100-500 lines.

#### Design

```
src/notebooklm/cli/session/
├── __init__.py          # register_session_commands(cli)
├── login.py             # login command + helpers
├── auth.py              # auth_group + auth_logout + auth_inspect + auth_check + auth_refresh
├── use.py               # use_notebook command
├── status.py            # status command
└── clear.py             # clear_cmd
```

#### Tests

- Existing CLI tests pass unchanged.

#### Acceptance Criteria

- [ ] Five submodules + entry point.
- [ ] No submodule >800 lines.
- [ ] CLI behavior unchanged.

---

### Spec 4.5 — `RPCParamsBuilder` Abstraction

#### Context

Every `generate_*` method in `_artifacts.py` hand-constructs nested lists with magic numbers:

```python
params = [
    [2],
    notebook_id,
    [None, None, ArtifactTypeCode.AUDIO.value, source_ids_triple,
     None, None, [None, [instructions, length_code, None,
     source_ids_double, language, None, format_code]]],
]
```

When Google reorders a field, every method needs to be touched. Drift detection is impossible — the schema is implicit in the code.

#### Design

Introduce a builder pattern with a centralized schema:

```python
# src/notebooklm/rpc/builders.py

class CreateArtifactBuilder:
    """Builds CREATE_ARTIFACT (R7cb6c) RPC params with versioned schema."""

    SCHEMA_VERSION = 3  # Increment when Google reorders fields

    def __init__(self, notebook_id: str, artifact_type: ArtifactTypeCode):
        self._notebook_id = notebook_id
        self._artifact_type = artifact_type
        self._fields: dict[str, Any] = {}

    def sources(self, source_ids: list[str]) -> "CreateArtifactBuilder":
        self._fields["source_ids"] = source_ids
        return self

    def language(self, lang: str) -> "CreateArtifactBuilder":
        self._fields["language"] = lang
        return self

    def audio_format(self, fmt: AudioFormat) -> "CreateArtifactBuilder":
        self._fields["audio_format"] = fmt.value
        return self

    # ... etc

    def build(self) -> list[Any]:
        """Render to the positional list-of-lists for batchexecute."""
        if self._artifact_type == ArtifactTypeCode.AUDIO:
            return self._build_audio_schema()
        elif self._artifact_type == ArtifactTypeCode.VIDEO:
            return self._build_video_schema()
        # ...
```

Each `_build_*_schema()` is the ONE place that knows the positional layout. When Google reorders, you change one method.

#### API

Internal change. Public methods like `generate_audio` keep their signature; their body changes from hand-built lists to:

```python
async def generate_audio(self, notebook_id, source_ids=None, ...):
    builder = CreateArtifactBuilder(notebook_id, ArtifactTypeCode.AUDIO)
    if source_ids is None:
        source_ids = await self._core.get_source_ids(notebook_id)
    builder.sources(source_ids)
    if language: builder.language(language)
    if audio_format: builder.audio_format(audio_format)
    if instructions: builder.instructions(instructions)
    if audio_length: builder.audio_length(audio_length)
    return await self._call_generate(notebook_id, builder.build())
```

#### Tests

- Unit: every existing `generate_*` test passes (cassettes confirm wire-format identity).
- Unit: builder validates required fields, rejects invalid combinations (e.g., `style_prompt` with `CINEMATIC` format).
- Unit: schema version test — if the schema layout changes, the test forces a version bump.

#### Acceptance Criteria

- [ ] Builders for every RPC method that takes complex params (≥3 nested fields).
- [ ] No hand-built nested lists outside builders.
- [ ] Schema version constant per builder.
- [ ] Cassette tests unchanged (wire format identical).

---

### Spec 4.6 — Structural Validation (msgspec / TypedDict)

#### Context

`grep -rn "list\[Any\]\|dict\[str, Any\]" src/notebooklm/` returns 108 occurrences. The decoder navigates `raw_data[0][0][0][0][4][2]` with `try/except IndexError`. When Google's response shape drifts, the error surfaces as a `KeyError` or `IndexError` at a random call site, never at the parsing boundary where it could be reported coherently.

#### Design

Adopt `msgspec` for structural validation. It's fast (no overhead), supports `Struct` (faster than dataclass), and validates on decode.

```python
# src/notebooklm/rpc/schemas.py
import msgspec

class GenerateAudioResponseSchema(msgspec.Struct):
    """Schema for CREATE_ARTIFACT response when artifact_type=AUDIO."""
    task_id: str
    status_code: int
    # Use msgspec's tagged union for variant payloads
    payload: AudioPayload | VideoPayload | ReportPayload
```

In the decoder:
```python
result = msgspec.json.decode(raw_response, type=GenerateAudioResponseSchema)
# If shape drifted, msgspec raises ValidationError at the boundary.
```

**Migration strategy:** add schemas for the top 10 most-frequently-called RPCs first (artifact generation, notebook list, source list, chat ask). The remaining can stay `Any`-typed until their response shape proves troublesome.

#### API

`msgspec` becomes an optional dependency in a new `[validation]` extra. Without it, the library falls back to the current `list[Any]` navigation. With it, drift surfaces as `RPCError(code="SCHEMA_DRIFT", details=...)`.

#### Tests

- Unit: drift simulation — feed a response with a swapped field, assert `SCHEMA_DRIFT` raised at boundary, not deeper.
- Regression: cassette tests with schemas enabled match cassette tests without.

#### Acceptance Criteria

- [ ] Top 10 RPC responses have msgspec schemas.
- [ ] New `[validation]` extra.
- [ ] Drift detection produces structured error.
- [ ] No performance regression (msgspec is ~5x faster than json+dataclass).

---

### Spec 4.7 — Shape Adapters Registry for Decoder Versioning

#### Context

`_research.py:poll` literally maintains three parallel response shapes in code:
```python
# Fast research: [url, title, desc, type, ...]
# Deep research (legacy): [None, title, None, type, ..., [report_markdown]]
# Deep research (current): [None, [title, report_markdown], None, type, ...]
```
discriminated by `isinstance()` checks. Adding a 4th shape risks breaking the existing ones.

#### Design

Introduce a shape-adapter registry per RPC method:

```python
class ResearchSourceAdapter(Protocol):
    name: str
    priority: int

    def matches(self, raw: list[Any]) -> bool: ...
    def parse(self, raw: list[Any]) -> ParsedSource: ...

# Registration:
@register_adapter(rpc=RPCMethod.POLL_RESEARCH, name="fast_research", priority=10)
class FastResearchAdapter:
    def matches(self, raw): return isinstance(raw[0], str) and raw[0].startswith("http")
    def parse(self, raw): ...

@register_adapter(rpc=RPCMethod.POLL_RESEARCH, name="deep_research_v2", priority=20)
class DeepResearchV2Adapter:
    def matches(self, raw): return raw[0] is None and isinstance(raw[1], list)
    def parse(self, raw): ...
```

Decoder tries adapters in descending priority order; first match wins. New shapes register a new adapter without modifying old ones.

#### API

Internal abstraction. No public change.

#### Tests

- Unit: register two adapters, test priority ordering.
- Unit: every existing research shape has an adapter, all tests pass.

#### Acceptance Criteria

- [ ] Adapter registry implemented.
- [ ] `_research.py:poll` migrated.
- [ ] Pattern available for other multi-shape decoders.

---

## 7. Phase 5 — Observability & Extensibility

### Spec 5.1 — OpenTelemetry Hooks

#### Context

Library has zero observability hooks beyond Python `logging`. For users putting this in production (long-running agents, CI pipelines, FastAPI services), spans per RPC call + metrics (latency, error rate, retry count) are table-stakes.

#### Design

Optional dependency `opentelemetry-api` in a new `[telemetry]` extra. When installed, the library auto-instruments each RPC call with a span:

```python
with tracer.start_as_current_span("notebooklm.rpc", attributes={
    "rpc.method": method.name,
    "rpc.notebook_id": notebook_id,
    "rpc.retry_count": retry_count,
}) as span:
    try:
        result = await self._perform_authed_post(...)
        span.set_attribute("rpc.success", True)
        return result
    except RPCError as exc:
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR))
        raise
```

Metrics:
- `notebooklm.rpc.duration` (histogram, ms)
- `notebooklm.rpc.errors` (counter, by method + error type)
- `notebooklm.rpc.retries` (counter)
- `notebooklm.artifact.generation_duration` (histogram, by artifact type)

#### API

Zero config required — if `opentelemetry-api` is importable and a tracer provider is configured, spans flow. Otherwise, no-op.

#### Tests

- Unit: with `opentelemetry-test-utils`, assert a span is created per RPC call.
- Unit: span attributes correct.
- Unit: without `opentelemetry`, library still works (no import errors).

#### Acceptance Criteria

- [ ] `pip install "notebooklm-py[telemetry]"` enables observability.
- [ ] Spans created for RPC calls and artifact generations.
- [ ] Documented in `docs/observability.md`.

---

### Spec 5.2 — Request/Response Middleware

#### Context

To inject custom logging, audit trails, request signing, or custom retry logic, today you'd have to monkey-patch `ClientCore._perform_authed_post`. Most modern HTTP libraries (`httpx` included via `event_hooks`) expose a middleware pattern.

#### Design

```python
@dataclass
class RequestContext:
    method: RPCMethod | str
    notebook_id: str | None
    url: str
    body: bytes
    headers: dict[str, str]
    attempt: int

@dataclass
class ResponseContext:
    request: RequestContext
    response: httpx.Response
    duration_ms: float

Middleware = Callable[[RequestContext], Awaitable[RequestContext]] | \
             Callable[[ResponseContext], Awaitable[ResponseContext]]

# Usage:
async def audit_middleware(ctx: RequestContext) -> RequestContext:
    audit_log.info(f"{ctx.method} on {ctx.notebook_id}")
    return ctx

client.add_request_middleware(audit_middleware)
```

Middlewares run in registration order. They MAY modify the context (e.g. add a header).

#### API

```python
client.add_request_middleware(fn)
client.add_response_middleware(fn)
client.clear_middleware()  # For tests
```

#### Tests

- Unit: register a middleware, make a request, assert middleware called with expected context.
- Unit: middleware can modify headers; modifications visible to the request.
- Unit: exception in middleware is propagated cleanly.

#### Acceptance Criteria

- [ ] Middleware API public.
- [ ] Request and response phases distinct.
- [ ] At least 2 documented examples (audit log, request signing).

---

### Spec 5.3 — SKILL.md Slim + Sub-docs

#### Context

`SKILL.md` is 626 lines / 32KB. Skills loaded into agent context consume tokens; a more efficient pattern is a short skill front-matter with detail deferred to companion docs the agent fetches as needed.

#### Design

Restructure `SKILL.md` to ~150 lines (~8KB). Move details to:
- `skill/installation.md`
- `skill/authentication.md`
- `skill/cli-quickstart.md`
- `skill/python-quickstart.md`
- `skill/mcp-integration.md`
- `skill/troubleshooting.md`

The slimmed SKILL.md lists what each sub-doc covers; the agent fetches them on-demand via the existing skill discovery mechanism.

#### API

No code change.

#### Tests

- Linting: `SKILL.md` ≤ 200 lines.
- Link integrity: all referenced sub-docs exist.

#### Acceptance Criteria

- [ ] SKILL.md ≤ 200 lines.
- [ ] Six sub-docs created.
- [ ] Agents can fetch sub-docs via the skill mechanism.

---

## 8. Phase 6 — API Parity Gaps + Advanced Features

### Spec 6.1 — Notes-to-Source Conversion

#### Context

NotebookLM web UI (post-Studio-redesign) lets users convert a note into a source, feeding it back into the notebook's RAG corpus. Library exposes notes CRUD but not the conversion.

#### Design

Step 1: discover the RPC method ID via network capture (see `docs/rpc-development.md`).

Step 2: implement:

```python
async def convert_to_source(
    self,
    notebook_id: str,
    note_id: str,
    *,
    title: str | None = None,
) -> Source:
    """Convert a note into a source within the same notebook.

    The note's content is added as a new source. The original note
    is preserved unless ``delete_note=True``.
    """
```

#### Tests

- Unit: cassette-based.
- E2E: create note → convert → assert new source exists with note content.

#### Acceptance Criteria

- [ ] RPC method discovered.
- [ ] `notes.convert_to_source` implemented.
- [ ] CLI: `notebooklm note convert <note_id>`.

---

### Spec 6.2 — Featured Notebooks Discovery

#### Context

NotebookLM web UI has "Featured Notebooks" (curated public notebooks from The Economist, The Atlantic, etc.) plus a discoverability layer for user-published public notebooks (>140k as of July 2025).

#### Design

Step 1: discover RPC method IDs for listing featured + searching public notebooks.

Step 2: implement:

```python
async def list_featured(
    self,
    *,
    category: str | None = None,
    limit: int = 50,
) -> list[Notebook]:
    """List curated featured notebooks from partner publishers."""

async def search_public(
    self,
    query: str,
    *,
    limit: int = 50,
) -> list[Notebook]:
    """Search publicly-shared notebooks by title or description."""
```

These return `Notebook` objects with limited fields (read-only).

#### Tests

- Unit: cassettes for featured + search.
- Integration: list featured, smoke-test that ≥5 notebooks return.

#### Acceptance Criteria

- [ ] Both methods implemented.
- [ ] CLI: `notebooklm list-featured` and `notebooklm search-public "<query>"`.
- [ ] Documented limitations (read-only, can't add sources).

---

### Spec 6.3 — Interactive Audio Mode (Exploratory)

#### Context

NotebookLM "Interactive Audio Mode" lets users press Join during Audio Overview playback to ask the AI hosts voice questions and get real-time spoken responses. This uses a different transport (likely WebRTC or a dedicated voice-streaming RPC), not the batchexecute path the rest of the library uses.

**This is high-risk:** reverse-engineering WebRTC signaling is a significant undertaking, and Google may detect/block automated voice clients.

#### Design (Exploratory)

**Phase 6.3.a — Spike (3-5 days):**
- Capture web UI network traffic during a full Interactive Audio session.
- Document the signaling flow, audio codec, and session lifecycle.
- Assess feasibility: can a Python client realistically participate without browser automation?

**Phase 6.3.b — Implementation (only if spike succeeds):**
- New module `notebooklm.audio.interactive` (segregated; large dependency footprint likely: `aiortc`, `pyaudio` or `sounddevice`).
- New optional extra `[interactive_audio]`.
- API surface: `client.audio.start_interactive_session(notebook_id, audio_artifact_id)` → returns a session handle for sending audio + receiving audio streams.

#### API (Tentative)

```python
async with client.audio.start_interactive_session(
    notebook_id, audio_artifact_id
) as session:
    await session.play()                          # Start playback
    async with session.join() as conversation:    # Press Join
        await conversation.send_audio(audio_bytes)
        async for response_chunk in conversation.receive():
            play_audio(response_chunk)
```

#### Tests

- Spike phase: documented findings, decision recorded.
- If implemented: integration tests via captured fixtures (no live e2e — too brittle).

#### Acceptance Criteria

- [ ] Spike completes with go/no-go recommendation documented.
- [ ] If go: minimal viable session works end-to-end.
- [ ] If no-go: documented in `docs/known-limitations.md`.

---

### Spec 6.4 — Living Documents Auto-Refresh Check

#### Context

NotebookLM treats Google Docs/Slides/Sheets sources as "living documents" — they auto-fetch the latest version. The library has `refresh_source()` and `check_freshness()` but it's unclear if they handle the auto-refresh transparently or require manual invocation.

#### Design

1. Audit current behavior with test notebooks containing Google Docs sources.
2. Document the actual behavior in `docs/sources.md`.
3. If manual refresh required: add a convenience method `client.sources.refresh_all_stale(notebook_id)` that detects and refreshes all stale Google Workspace sources.

#### API

```python
async def refresh_all_stale(
    self,
    notebook_id: str,
    *,
    source_types: list[SourceType] | None = None,
) -> list[Source]:
    """Refresh all stale Google Workspace sources in a notebook."""
```

#### Tests

- Integration: notebook with one Doc; modify the Doc; call refresh_all_stale; verify content updated.

#### Acceptance Criteria

- [ ] Behavior documented.
- [ ] Convenience method added.
- [ ] CLI: `notebooklm source refresh-stale`.

---

### Spec 6.5 — Notebook Profiles / Avatars / Banners

#### Context

NotebookLM is rolling out (Q2 2026) creator profiles for public notebooks: avatar, custom name, custom description, banner image. The library's `Notebook` dataclass doesn't model these.

#### Design

Extend the `Notebook` dataclass:

```python
@dataclass(frozen=True)
class NotebookProfile:
    creator_avatar_url: str | None = None
    creator_name: str | None = None
    custom_description: str | None = None
    banner_image_url: str | None = None

@dataclass(frozen=True)
class Notebook:
    # ... existing fields ...
    profile: NotebookProfile | None = None
```

New API:

```python
async def update_profile(
    self,
    notebook_id: str,
    *,
    creator_avatar_url: str | None = None,
    creator_name: str | None = None,
    custom_description: str | None = None,
    banner_image_url: str | None = None,
) -> NotebookProfile:
    """Update the public-facing profile metadata for a shared notebook."""
```

#### Tests

- Unit: cassettes.
- Integration: create public notebook, set profile, verify visible.

#### Acceptance Criteria

- [ ] `NotebookProfile` dataclass.
- [ ] `notebooks.update_profile` method.
- [ ] CLI: `notebooklm notebook set-profile`.

---

## 9. Cross-Cutting Concerns

### 9.1 Migration & Backward Compatibility Policy

Every refactor in Phase 4 MUST:
1. Preserve public API contracts (functions, classes, exception types).
2. Provide compat shims with `DeprecationWarning` for any moved internals that external code may have imported.
3. Keep `__all__` lists synced with the new structure.
4. Update `docs/stability.md` to reflect new internal paths.

### 9.2 Versioning

- Phase 0, 1, 2, 5 ship in 0.x.y minor bumps (additive, non-breaking).
- Phase 3 (MCP) ships in 0.x.0 minor.
- Phase 4 (refactors) target 1.0.0 when complete (signals "API stable").
- Phase 6.3 (Interactive Audio) may ship in 1.x as exploratory; mark as `experimental` in docs.

### 9.3 Release Cadence

- Each spec is ≤1 PR (or a small series with feature flags).
- Phase 0 + Phase 1 ship together as v0.5.0.
- Phase 2 → v0.6.0.
- Phase 3 (MCP) → v0.7.0.
- Phase 4 (refactor) → v1.0.0.
- Phase 5 → v1.1.0.
- Phase 6 → rolling.

### 9.4 Documentation

Every PR MUST update:
- `CHANGELOG.md` (Keep-a-Changelog format).
- Relevant `docs/*.md` files.
- Type stubs / docstrings.

### 9.5 Testing Strategy

- All new public APIs require unit tests with ≥90% coverage (matches existing project floor).
- All new RPC integrations require cassette-based integration tests.
- Phase 4 refactors require full regression run; cassettes re-recorded where touched.
- Phase 3 (MCP) requires manual e2e validation in Claude Desktop, Cursor, Gemini CLI.

---

## 10. Appendix

### 10.1 Improvement → Phase Quick Reference

| Improvement Topic | Spec ID |
|---|---|
| Recurring API parity audit | 0.1 |
| `from __future__` annotations consistency | 0.2 |
| Backoff jitter | 0.3 |
| httpx/httpcore log redaction | 0.4 |
| Cassettes via Git LFS | 0.5 |
| Chat streaming | 1.1 |
| Concurrent + streaming batch downloads | 1.2 |
| HTTP client reuse | 1.3 |
| Async iterators (pagination) | 1.4 |
| Concurrency limits (semaphore) | 2.1 |
| Batch source operations (`add_many`) | 2.2 |
| `research.wait_for_completion` | 2.3 |
| `chat.delete_history` | 2.4 |
| `artifacts.load_audio` (existing) | 2.5 |
| Native MCP server | 3.1 |
| Split `auth.py` | 4.1 |
| Split `_artifacts.py` | 4.2 |
| Split `paths.py` | 4.3 |
| Split `cli/session.py` | 4.4 |
| `RPCParamsBuilder` | 4.5 |
| Structural validation (msgspec) | 4.6 |
| Shape adapters registry | 4.7 |
| OpenTelemetry hooks | 5.1 |
| Request/response middleware | 5.2 |
| SKILL.md slim + sub-docs | 5.3 |
| Notes-to-source conversion | 6.1 |
| Featured notebooks discovery | 6.2 |
| Interactive Audio Mode | 6.3 |
| Living documents auto-refresh | 6.4 |
| Notebook profiles/avatars | 6.5 |

### 10.2 Open Questions

These require decisions before or during implementation:

1. **MCP server distribution:** ship as `notebooklm-py[mcp]` extra or as a separate `notebooklm-mcp` package? (Recommendation: extra — keeps single source of truth.)
2. **`claude-world/notebooklm-skill` coordination:** offer to upstream their MCP server? Coexist? (Recommendation: open an issue early in Phase 3 proposing coexistence with clear use-case differentiation.)
3. **msgspec vs TypedDict:** msgspec is faster but adds a C extension. TypedDict is stdlib but no runtime validation. (Recommendation: msgspec for hot paths, TypedDict elsewhere.)
4. **Interactive Audio Mode legal risk:** does automating voice interaction violate NotebookLM ToS? (Recommendation: legal review before spike commits to real implementation.)
5. **Phase 4 timing:** wait until 1.0 announcement or ship internal refactors in 0.x with compat shims? (Recommendation: ship in 0.x; 1.0 announcement bundles all refactors as a "stability" milestone.)

### 10.3 Estimated Total Effort

| Phase | Sub-items | Effort range |
|---|---|---|
| 0 | 5 | 1 week |
| 1 | 4 | 2-3 weeks |
| 2 | 5 | 2 weeks |
| 3 | 1 | 1-2 weeks |
| 4 | 7 | 4-6 weeks |
| 5 | 3 | 1-2 weeks |
| 6 | 5 | 3-5 weeks (6.3 dominates) |
| **Total** | **30** | **14-21 weeks single-developer** |

Parallelizable across 2-3 contributors to ~6-9 weeks of wall-clock.

---

## 11. Sign-off

This document is ready for review. Recommended next steps:

1. **Maintainer review** (`teng-lin`) — pick a subset of phases to commit to; reject or defer the rest.
2. **GitHub Discussion** — open one per phase with the spec as the seed comment; let community input shape priorities.
3. **First implementation PR** — recommend starting with Spec 0.1 (recurring audit) and Spec 1.1 (chat streaming) in parallel; they're independent, low-risk, and high-visibility wins.

---

*End of specification document.*
