"""Spec 0.3 — backoff jitter on every retry path.

Pins the acceptance criteria:

- All retry paths (5xx server error, network error, 429 rate limit) carry
  jitter on their sleep delay.
- An injected deterministic ``jitter_rng`` makes the jittered schedule
  reproducible (the test-only seam the spec calls for).
- The jitter actually varies the delay (anti thundering-herd), and the
  default constructor wires a per-instance ``random.Random`` — not the
  process-global RNG.
"""

from __future__ import annotations

import random

import httpx
import pytest

from notebooklm._core import (
    ClientCore,
    _AuthSnapshot,
    _TransportRateLimited,
    _TransportServerError,
)
from notebooklm.auth import AuthTokens


def _make_core(
    *,
    rate_limit_max_retries: int = 0,
    server_error_max_retries: int = 0,
    jitter_rng: random.Random | None = None,
) -> ClientCore:
    auth = AuthTokens(
        csrf_token="CSRF",
        session_id="SID",
        cookies={"SID": "sid_cookie"},
    )
    return ClientCore(
        auth=auth,
        refresh_retry_delay=0.0,
        rate_limit_max_retries=rate_limit_max_retries,
        server_error_max_retries=server_error_max_retries,
        jitter_rng=jitter_rng,
    )


def _ok_response() -> httpx.Response:
    return httpx.Response(200, text="OK", request=httpx.Request("POST", "https://example.test/x"))


def _status_error(code: int, *, retry_after: str | None = None) -> httpx.HTTPStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx.Request("POST", "https://example.test/x")
    response = httpx.Response(code, request=request, headers=headers)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


def _build(snapshot: _AuthSnapshot) -> tuple[str, str, dict[str, str]]:
    return "https://example.test/x", "payload", {}


async def _capture_sleeps(monkeypatch) -> list[float]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("notebooklm._core.asyncio.sleep", fake_sleep)
    return sleeps


# ---------------------------------------------------------------------------
# 5xx server-error path carries deterministic, injectable jitter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_5xx_backoff_has_injected_deterministic_jitter(monkeypatch):
    """A persistent 502 with an injected seeded RNG produces the exact
    jittered schedule a replay of that RNG predicts — and every delay is
    strictly above the pure-exponential base (jitter is non-negative)."""
    core = _make_core(server_error_max_retries=3, jitter_rng=random.Random(42))
    await core.open()
    try:
        sleeps = await _capture_sleeps(monkeypatch)

        async def fake_post(*args, **kwargs):
            raise _status_error(502)

        monkeypatch.setattr(core._http_client, "post", fake_post)

        with pytest.raises(_TransportServerError):
            await core._perform_authed_post(build_request=_build, log_label="t")

        # Independently replay the same seed to derive the expected schedule.
        replay = random.Random(42)
        expected = [
            base + replay.uniform(0, base * 0.1) for base in (min(2**n, 30) for n in range(3))
        ]
        assert sleeps == pytest.approx(expected)
        # Jitter is additive and non-negative: each delay exceeds its base.
        for delay, base in zip(sleeps, (1, 2, 4), strict=True):
            assert delay > base
    finally:
        await core.close()


# ---------------------------------------------------------------------------
# Network-error path carries jitter (same code path as 5xx)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_error_backoff_carries_jitter(monkeypatch):
    """An httpx.RequestError follows the server-error retry path and must
    also carry jitter, not a bare exponential delay."""
    core = _make_core(server_error_max_retries=2, jitter_rng=random.Random(1))
    await core.open()
    try:
        sleeps = await _capture_sleeps(monkeypatch)

        async def fake_post(*args, **kwargs):
            raise httpx.ConnectError("boom")

        monkeypatch.setattr(core._http_client, "post", fake_post)

        with pytest.raises(_TransportServerError):
            await core._perform_authed_post(build_request=_build, log_label="t")

        replay = random.Random(1)
        expected = [
            base + replay.uniform(0, base * 0.1) for base in (min(2**n, 30) for n in range(2))
        ]
        assert sleeps == pytest.approx(expected)
        assert all(d != b for d, b in zip(sleeps, (1, 2), strict=True))
    finally:
        await core.close()


# ---------------------------------------------------------------------------
# 429 rate-limit path carries jitter (the acceptance gap)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_retry_after_carries_jitter(monkeypatch):
    """The 429 path sleeps ``Retry-After`` plus jitter so a fleet of clients
    rate-limited at the same instant don't all wake in lockstep."""
    core = _make_core(rate_limit_max_retries=2, jitter_rng=random.Random(7))
    await core.open()
    try:
        sleeps = await _capture_sleeps(monkeypatch)

        async def fake_post(*args, **kwargs):
            raise _status_error(429, retry_after="5")

        monkeypatch.setattr(core._http_client, "post", fake_post)

        with pytest.raises(_TransportRateLimited):
            await core._perform_authed_post(build_request=_build, log_label="t")

        replay = random.Random(7)
        expected = [5 + replay.uniform(0, 5 * 0.1) for _ in range(2)]
        assert sleeps == pytest.approx(expected)
        # The whole point: never the bare integer Retry-After.
        assert all(d > 5 for d in sleeps)
    finally:
        await core.close()


# ---------------------------------------------------------------------------
# Jitter actually varies the delay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jitter_produces_delay_variance(monkeypatch):
    """At the capped tail (base == 30 for every retry) the delays must still
    differ from each other — that variance is the anti-thundering-herd
    property the spec requires."""
    core = _make_core(server_error_max_retries=8, jitter_rng=random.Random(99))
    await core.open()
    try:
        sleeps = await _capture_sleeps(monkeypatch)

        async def fake_post(*args, **kwargs):
            raise _status_error(500)

        monkeypatch.setattr(core._http_client, "post", fake_post)

        with pytest.raises(_TransportServerError):
            await core._perform_authed_post(build_request=_build, log_label="t")

        # Attempts 5..7 all have base == min(2**n, 30) == 30. With jitter
        # they must not collapse to a single repeated value.
        capped_tail = sleeps[5:]
        assert len(capped_tail) == 3
        assert len(set(capped_tail)) > 1
        assert all(30 <= d <= 33 for d in capped_tail)
    finally:
        await core.close()


# ---------------------------------------------------------------------------
# Default RNG is per-instance, not the process-global random module
# ---------------------------------------------------------------------------


def test_default_jitter_rng_is_per_instance_random_instance():
    """Spec: 'Use random.Random seeded per-process (not the global RNG).'
    Two cores get independent Random instances; neither is the module RNG."""
    auth = AuthTokens(csrf_token="C", session_id="S", cookies={})
    c1 = ClientCore(auth=auth)
    c2 = ClientCore(auth=auth)

    assert isinstance(c1._jitter_rng, random.Random)
    assert isinstance(c2._jitter_rng, random.Random)
    assert c1._jitter_rng is not c2._jitter_rng
    # Not the global RNG used by ``random.uniform`` at module scope.
    assert c1._jitter_rng is not random._inst  # type: ignore[attr-defined]


def test_injected_jitter_rng_is_used_verbatim():
    """An explicitly injected RNG is stored as-is (the test seam)."""
    auth = AuthTokens(csrf_token="C", session_id="S", cookies={})
    rng = random.Random(123)
    core = ClientCore(auth=auth, jitter_rng=rng)
    assert core._jitter_rng is rng
