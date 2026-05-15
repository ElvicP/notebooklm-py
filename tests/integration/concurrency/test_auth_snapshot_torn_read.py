"""T7.F2 — atomic ``(csrf, sid, cookies)`` snapshot during refresh.

The race fixed here is a torn read of the auth-headers triple
``(csrf_token, session_id, cookies)`` while a refresh runs concurrently
with in-flight RPCs. Today's ``ClientCore._snapshot()`` reads the four
scalar fields off ``self.auth`` without holding any lock, and
``_build_url()`` reads ``session_id``/``authuser``/``account_email``
directly off ``self.auth`` (not the snapshot). A concurrent ``refresh_auth``
mutates ``csrf_token`` and ``session_id`` in two separate Python statements
— there's no asyncio yield between them in production today, but the
moment any maintainer introduces an ``await`` in that prologue, an RPC
can observe one field from the OLD generation and another from the NEW
generation. The fix introduces a dedicated ``_auth_snapshot_lock`` that:

1. ``_snapshot()`` acquires under ``async with`` to read all scalars
   atomically.
2. The refresh-side mutation block in ``client.refresh_auth`` writes
   ``csrf_token`` + ``session_id`` under the same lock — tiny critical
   section, no awaits inside.
3. ``_build_url()`` consumes the resulting ``_AuthSnapshot`` rather than
   re-reading ``self.auth`` live, so the URL is built from the same
   generation the body was.

This test stresses that contract: fan 50 concurrent RPCs, fire a refresh
halfway through, and assert that every wire request carries a
generation-coherent ``(csrf, sid, cookies)`` triple. Each generation is
tagged by writing a monotonic counter into all three positions
simultaneously under the lock — so the assertion is purely "for every
captured request, the three observed generation tags must match".

If the lock or snapshot regress (e.g. someone removes the lock, or
``_build_url`` reverts to reading ``self.auth`` directly), the test will
observe a torn ``(csrf=N, sid=N, cookies=N+1)`` or similar tuple and
fail loudly.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from collections.abc import Iterator

import httpx
import pytest

from notebooklm._core import ClientCore
from notebooklm.auth import AuthTokens
from notebooklm.rpc import RPCMethod

# -- Generation tagging -----------------------------------------------------
#
# Each "generation" of credentials is a monotonic integer N. We encode N
# into all three axes simultaneously:
#   csrf_token  = f"CSRF_{N}"            (goes into request body via f.req)
#   session_id  = f"SID_{N}"             (goes into URL via f.sid=)
#   cookies     = SID=sid_cookie_{N}     (goes into Cookie: header)
#
# When the test asserts coherence, it extracts the N from each axis and
# requires all three to be equal per captured request.
RPC_METHOD = RPCMethod.LIST_NOTEBOOKS
RPC_METHOD_ID = RPC_METHOD.value


def _synthetic_rpc_response_text(rpc_id: str = RPC_METHOD_ID) -> str:
    """Minimal valid batchexecute response that decodes to ``[]``."""
    inner = json.dumps([])
    chunk = json.dumps([["wrb.fr", rpc_id, inner, None, None]])
    return f")]}}'\n{len(chunk)}\n{chunk}\n"


def _gen_counter() -> Iterator[int]:
    i = 0
    while True:
        i += 1
        yield i


def _extract_csrf_gen(body: bytes) -> int:
    """Extract generation N from ``CSRF_N`` embedded in the request body."""
    text = body.decode("utf-8", errors="replace")
    # The body is URL-encoded form data; ``at=CSRF_N`` lives in there.
    decoded = urllib.parse.unquote_plus(text)
    m = re.search(r"CSRF_(\d+)", decoded)
    assert m is not None, f"Could not locate CSRF tag in body: {text!r}"
    return int(m.group(1))


def _extract_sid_gen(url: str) -> int:
    """Extract generation N from ``f.sid=SID_N`` in the URL query."""
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    sid_values = qs.get("f.sid", [])
    assert sid_values, f"Could not locate f.sid in URL: {url!r}"
    m = re.match(r"SID_(\d+)", sid_values[0])
    assert m is not None, f"Could not parse SID tag from f.sid={sid_values[0]!r}"
    return int(m.group(1))


def _extract_cookie_gen(cookie_header: str) -> int:
    """Extract generation N from ``SID=sid_cookie_N`` in the Cookie header."""
    m = re.search(r"sid_cookie_(\d+)", cookie_header)
    assert m is not None, f"Could not locate sid_cookie tag in Cookie: {cookie_header!r}"
    return int(m.group(1))


@pytest.mark.asyncio
async def test_concurrent_refresh_does_not_tear_auth_triple_across_fan_out():
    """Fan 50 RPCs, fire a refresh halfway, assert no torn ``(csrf, sid, cookies)``.

    Mechanism:

    - ``ConcurrentMockTransport``-style handler queues each POST behind an
      ``asyncio.Event`` so we can hold all 50 RPCs on the wire while the
      refresh runs.
    - The refresh is *synthetic*: a callable wired in as
      ``refresh_callback`` that, when invoked, increments a generation
      counter and atomically writes csrf/sid/cookies all stamped with the
      new generation — all three writes inside ``async with
      _auth_snapshot_lock`` so a snapshot-side reader cannot observe a
      partial update.
    - We then fan 50 ``rpc_call`` invocations and trigger one refresh
      halfway through. Every captured POST must carry generation-matched
      values across all three axes.

    If ``_snapshot()`` or ``_build_url()`` regress to reading
    ``self.auth`` live (outside the lock), an RPC that captured the
    snapshot in generation N and then ran ``_build_url`` after the
    refresh's lock-write in generation N+1 would observe ``sid_gen=N+1``
    while ``csrf_gen=N`` — a torn read this test asserts against.
    """
    fan_out = 50
    refresh_at = fan_out // 2  # Trigger refresh after this many RPCs land.

    captured: list[httpx.Request] = []

    gen_iter = _gen_counter()
    current_gen = next(gen_iter)  # Start in generation 1.

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured.append(request)
            # Synthetic ``ack`` — the transport response itself doesn't
            # matter; we're asserting on the captured outgoing request.
            return httpx.Response(200, text=_synthetic_rpc_response_text())
        # No GETs expected in this test (synthetic refresh below skips them).
        return httpx.Response(500, text="unexpected GET")

    transport = httpx.MockTransport(handler)

    auth = AuthTokens(
        csrf_token=f"CSRF_{current_gen}",
        session_id=f"SID_{current_gen}",
        cookies={("SID", ".google.com"): f"sid_cookie_{current_gen}"},
    )

    # Synthetic refresh callback wired into ClientCore. Bumps the
    # generation and writes all three axes atomically under
    # ``_auth_snapshot_lock``. Returns updated AuthTokens to satisfy the
    # refresh_callback contract.
    async def refresh_callback() -> AuthTokens:
        nonlocal current_gen
        new_gen = next(gen_iter)
        # Atomic write under the same lock that ``_snapshot()`` acquires.
        # Verify the lock exists — if the fix isn't in place yet, this
        # attribute access will fail and the test will skip-fail loudly.
        lock = core._auth_snapshot_lock
        assert lock is not None, (
            "T7.F2 fix not applied: ClientCore._auth_snapshot_lock is None. "
            "The synthetic refresh callback cannot serialize csrf+sid+cookies "
            "writes without it. Implement the lock per audit §12."
        )
        async with lock:
            core.auth.csrf_token = f"CSRF_{new_gen}"
            core.auth.session_id = f"SID_{new_gen}"
            # Update the live httpx cookie jar synchronously — this is the
            # same jar that ``client.post`` merges into outgoing requests.
            assert core._http_client is not None
            core._http_client.cookies.set("SID", f"sid_cookie_{new_gen}", domain=".google.com")
            core.auth.cookies = {("SID", ".google.com"): f"sid_cookie_{new_gen}"}
            current_gen = new_gen
        return core.auth

    core = ClientCore(
        auth=auth,
        refresh_callback=refresh_callback,
        refresh_retry_delay=0.0,
    )
    await core.open()
    try:
        # Replace the auto-built client with one using our MockTransport so
        # we can observe outgoing requests post-cookie-merge.
        prior_cookies = core._http_client.cookies
        await core._http_client.aclose()
        core._http_client = httpx.AsyncClient(
            cookies=prior_cookies,
            transport=transport,
            timeout=httpx.Timeout(connect=1.0, read=5.0, write=5.0, pool=1.0),
        )

        # Fan out 50 RPCs. Fire a refresh halfway by interleaving an
        # ``await refresh_callback()`` call between batches.
        async def one_rpc() -> None:
            await core.rpc_call(RPC_METHOD, [])

        first_batch = [asyncio.create_task(one_rpc()) for _ in range(refresh_at)]
        # Wait for the first batch's POSTs to all land (synchronously
        # captured before any await inside the handler — the handler is
        # purely synchronous post-capture).
        await asyncio.gather(*first_batch)

        # Refresh: bumps gen 1 → 2 atomically under the lock.
        await refresh_callback()

        # Second batch — these should all see generation 2.
        second_batch = [asyncio.create_task(one_rpc()) for _ in range(fan_out - refresh_at)]
        await asyncio.gather(*second_batch)
    finally:
        await core.close()

    # Assertion: every captured request must be coherent across all three
    # axes. Mixed generations (e.g. csrf=1, sid=1, cookie=2) indicate a
    # torn read.
    assert len(captured) == fan_out, f"Expected {fan_out} POSTs captured, got {len(captured)}"
    torn = []
    for i, req in enumerate(captured):
        url = str(req.url)
        body = bytes(req.content)
        cookie_header = req.headers.get("cookie", "")
        try:
            csrf_gen = _extract_csrf_gen(body)
            sid_gen = _extract_sid_gen(url)
            cookie_gen = _extract_cookie_gen(cookie_header)
        except AssertionError as exc:
            torn.append((i, f"extract-failed: {exc}"))
            continue
        if not (csrf_gen == sid_gen == cookie_gen):
            torn.append(
                (
                    i,
                    f"torn: csrf={csrf_gen}, sid={sid_gen}, cookies={cookie_gen}",
                )
            )

    assert not torn, (
        f"{len(torn)}/{len(captured)} requests carried mixed-generation auth state. "
        f"Sample: {torn[:5]}. This indicates the (csrf, sid, cookies) triple is no "
        f"longer atomic under refresh — check _snapshot() lock acquisition and that "
        f"_build_url() consumes _AuthSnapshot rather than reading self.auth live."
    )

    # Sanity check the test actually exercised both generations.
    gens_observed = set()
    for req in captured:
        gens_observed.add(_extract_csrf_gen(bytes(req.content)))
    assert gens_observed == {1, 2}, (
        f"Test scaffolding did not exercise both generations. Observed: {sorted(gens_observed)}"
    )
