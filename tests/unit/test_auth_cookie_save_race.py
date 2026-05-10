"""Tests for the open-time snapshot + dirty-flag merge in
``save_cookies_to_storage`` — the fix for issue #361 (stale in-memory
cookies clobbering fresh disk state) and the side-effect closure of
``docs/auth-keepalive.md`` §3.4.2 (path collapse).

The canonical race that motivated this code (#361):

    Process A and Process B both share the same ``storage_state.json``.
    A loads ``*PSIDTS=OLD`` at open time and never rotates.
    B rotates ``*PSIDTS`` to ``NEW`` and writes to disk.
    A's ``close()`` reads disk under flock, sees its in-memory ``OLD``
    differs from disk's ``NEW``, and "merges" by writing ``OLD`` —
    silently undoing B's rotation.

The fix is an open-time snapshot per ``ClientCore`` instance, plus a
``save_cookies_to_storage`` mode that writes only the deltas relative to
that snapshot. Cookies the in-process code never touched are left to
disk; sibling-process writes survive.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from notebooklm.auth import (
    AuthTokens,
    CookieSnapshotKey,
    save_cookies_to_storage,
    snapshot_cookie_jar,
)


def _read_cookies(storage_path: Path) -> list[dict]:
    """Helper: read the cookies array from a Playwright storage_state.json."""
    return json.loads(storage_path.read_text(encoding="utf-8"))["cookies"]


def _cookie_value(storage_path: Path, name: str, domain: str, path: str = "/") -> str | None:
    """Helper: extract a single cookie's value from disk by (name, domain, path)."""
    for c in _read_cookies(storage_path):
        if c.get("name") == name and c.get("domain") == domain and (c.get("path") or "/") == path:
            return c.get("value")
    return None


def _write_storage(storage_path: Path, cookies: list[dict]) -> None:
    """Helper: write a Playwright-shaped storage_state.json."""
    storage_path.write_text(json.dumps({"cookies": cookies}), encoding="utf-8")


class TestSnapshotKey:
    """The ``CookieSnapshotKey`` NamedTuple is the path-aware key used by
    the snapshot/delta machinery. It must be a NamedTuple (so it's
    hashable + structurally typed) and not collapse different paths."""

    def test_named_tuple_fields(self):
        key = CookieSnapshotKey("SID", ".google.com", "/")
        assert key.name == "SID"
        assert key.domain == ".google.com"
        assert key.path == "/"

    def test_path_distinguishes_keys(self):
        a = CookieSnapshotKey("OSID", "accounts.google.com", "/")
        b = CookieSnapshotKey("OSID", "accounts.google.com", "/u/0/")
        assert a != b
        assert hash(a) != hash(b) or a != b  # tuple-hash, distinct via inequality

    def test_named_tuple_is_hashable(self):
        key = CookieSnapshotKey("SID", ".google.com", "/")
        assert {key: "value"}[key] == "value"


class TestSnapshotCookieJar:
    """``snapshot_cookie_jar`` captures the path-aware ``(name, domain, path)
    -> value`` map that downstream merges depend on."""

    def test_captures_basic_cookie(self):
        jar = httpx.Cookies()
        jar.set("SID", "abc", domain=".google.com", path="/")
        snap = snapshot_cookie_jar(jar)
        assert snap == {CookieSnapshotKey("SID", ".google.com", "/"): "abc"}

    def test_path_aware_keys_do_not_collapse(self):
        """Two cookies with the same name+domain but different paths are
        distinct entries in the snapshot — closes §3.4.2."""
        jar = httpx.Cookies()
        jar.set("OSID", "root", domain="accounts.google.com", path="/")
        jar.set("OSID", "scoped", domain="accounts.google.com", path="/u/0/")
        snap = snapshot_cookie_jar(jar)
        assert snap[CookieSnapshotKey("OSID", "accounts.google.com", "/")] == "root"
        assert snap[CookieSnapshotKey("OSID", "accounts.google.com", "/u/0/")] == "scoped"

    def test_normalizes_missing_path_to_root(self):
        """Cookies without an explicit path default to ``/`` in the snapshot key."""
        jar = httpx.Cookies()
        # http.cookiejar normalizes empty path to "/", but verify the
        # snapshot helper agrees.
        jar.set("SID", "abc", domain=".google.com")
        snap = snapshot_cookie_jar(jar)
        assert CookieSnapshotKey("SID", ".google.com", "/") in snap


class TestStaleOverwriteFreshRace:
    """The §3.4.1 / #361 failure timeline as a unit test.

    Two simulated processes share one ``storage_state.json``. Process A
    holds a stale in-memory jar (snapshot captured at open time, never
    rotated). Process B rotates ``*PSIDTS`` to ``NEW`` between A's open
    and A's close, and writes to disk. When A's ``close()`` save runs,
    the snapshot/delta merge must NOT clobber B's fresh value.
    """

    def test_stale_in_memory_does_not_clobber_fresh_disk(self, tmp_path):
        storage = tmp_path / "storage_state.json"
        # t=0: disk has *PSIDTS=OLD
        _write_storage(
            storage,
            [
                {
                    "name": "__Secure-1PSIDTS",
                    "value": "OLD",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
                {
                    "name": "SID",
                    "value": "sid-A",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
            ],
        )

        # Process A opens: builds jar from disk, captures snapshot.
        jar_a = httpx.Cookies()
        jar_a.set("__Secure-1PSIDTS", "OLD", domain=".google.com", path="/")
        jar_a.set("SID", "sid-A", domain=".google.com", path="/")
        snapshot_a = snapshot_cookie_jar(jar_a)

        # Process B (simulated): loaded OLD at its own open, rotates
        # *PSIDTS to NEW, and writes to disk via the same save API any
        # other writer would use.
        jar_b = httpx.Cookies()
        jar_b.set("__Secure-1PSIDTS", "OLD", domain=".google.com", path="/")
        jar_b.set("SID", "sid-A", domain=".google.com", path="/")
        snapshot_b = snapshot_cookie_jar(jar_b)
        jar_b.set("__Secure-1PSIDTS", "NEW", domain=".google.com", path="/")
        save_cookies_to_storage(jar_b, storage, original_snapshot=snapshot_b)

        assert (
            _cookie_value(storage, "__Secure-1PSIDTS", ".google.com") == "NEW"
        ), "Process B's rotation should land on disk before A closes"

        # Process A closes: jar still holds OLD (it never rotated). Without
        # the fix, this save would write OLD over NEW. With the fix, A's
        # delta vs its snapshot is empty — nothing is written.
        save_cookies_to_storage(jar_a, storage, original_snapshot=snapshot_a)

        # Verify: disk still has B's fresh NEW value.
        assert _cookie_value(storage, "__Secure-1PSIDTS", ".google.com") == "NEW", (
            "Process A's close must NOT clobber the fresh *PSIDTS=NEW that "
            "Process B rotated; A never rotated so its snapshot/delta is empty"
        )
        # And SID (which neither process touched) is intact.
        assert _cookie_value(storage, "SID", ".google.com") == "sid-A"


class TestCookieDeletionPropagation:
    """When httpx auto-deletes a cookie (e.g. ``Max-Age=0`` from a
    Set-Cookie response), the deletion must propagate to disk on the
    next save — otherwise the disk remembers a cookie the wire-side
    has explicitly invalidated."""

    def test_deletion_propagates_to_disk(self, tmp_path):
        storage = tmp_path / "storage_state.json"
        _write_storage(
            storage,
            [
                {
                    "name": "A",
                    "value": "a",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "None",
                },
                {
                    "name": "B",
                    "value": "b",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "None",
                },
                {
                    "name": "C",
                    "value": "c",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "None",
                },
            ],
        )

        # Open: jar has A, B, C.
        jar = httpx.Cookies()
        jar.set("A", "a", domain=".google.com", path="/")
        jar.set("B", "b", domain=".google.com", path="/")
        jar.set("C", "c", domain=".google.com", path="/")
        snapshot = snapshot_cookie_jar(jar)

        # Simulate httpx auto-deleting B mid-session (Max-Age=0 evict).
        jar.delete("B", domain=".google.com", path="/")

        save_cookies_to_storage(jar, storage, original_snapshot=snapshot)

        cookies = _read_cookies(storage)
        names = {c["name"] for c in cookies}
        assert "A" in names
        assert "C" in names
        assert "B" not in names, (
            "Cookie deleted from the in-memory jar must be removed from disk; "
            "without deletion propagation, the disk keeps a wire-side-invalidated cookie"
        )


class TestPathAwareKeyRegression:
    """§3.4.2: two storage entries with the same ``(name, domain)`` but
    different paths are distinct cookies. A snapshot/save round trip
    must not collapse them."""

    def test_two_paths_survive_round_trip(self, tmp_path):
        storage = tmp_path / "storage_state.json"
        _write_storage(
            storage,
            [
                {
                    "name": "OSID",
                    "value": "root",
                    "domain": "accounts.google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
                {
                    "name": "OSID",
                    "value": "scoped",
                    "domain": "accounts.google.com",
                    "path": "/u/0/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
            ],
        )

        # Build a jar that mirrors disk; rotate the root variant only.
        jar = httpx.Cookies()
        jar.set("OSID", "root", domain="accounts.google.com", path="/")
        jar.set("OSID", "scoped", domain="accounts.google.com", path="/u/0/")
        snapshot = snapshot_cookie_jar(jar)

        # Rotate only the root-path cookie.
        jar.set("OSID", "rotated_root", domain="accounts.google.com", path="/")

        save_cookies_to_storage(jar, storage, original_snapshot=snapshot)

        # Both path variants must survive; the unrotated /u/0/ entry is
        # untouched, and the rotated root entry has the new value.
        assert _cookie_value(storage, "OSID", "accounts.google.com", "/") == "rotated_root"
        assert _cookie_value(storage, "OSID", "accounts.google.com", "/u/0/") == "scoped"


class TestLegitimateRotationLastWriterWins:
    """When two processes legitimately rotate the same cookie in their
    own jars, last-writer-wins is acceptable. The fix is specifically
    about *stale* clobbering *fresh*, not about preserving every
    concurrent write — that's what the cross-process flock + true
    multi-master replication would buy, which is out of scope."""

    def test_concurrent_rotations_resolve_by_last_writer(self, tmp_path):
        storage = tmp_path / "storage_state.json"
        _write_storage(
            storage,
            [
                {
                    "name": "__Secure-1PSIDTS",
                    "value": "OLD",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
            ],
        )

        # A opens, rotates *PSIDTS to NEW_A in its own jar (not yet saved).
        jar_a = httpx.Cookies()
        jar_a.set("__Secure-1PSIDTS", "OLD", domain=".google.com", path="/")
        snapshot_a = snapshot_cookie_jar(jar_a)
        jar_a.set("__Secure-1PSIDTS", "NEW_A", domain=".google.com", path="/")

        # B writes NEW_B to disk.
        jar_b = httpx.Cookies()
        jar_b.set("__Secure-1PSIDTS", "OLD", domain=".google.com", path="/")
        snapshot_b = snapshot_cookie_jar(jar_b)
        jar_b.set("__Secure-1PSIDTS", "NEW_B", domain=".google.com", path="/")
        save_cookies_to_storage(jar_b, storage, original_snapshot=snapshot_b)

        assert _cookie_value(storage, "__Secure-1PSIDTS", ".google.com") == "NEW_B"

        # A closes after B; A genuinely rotated, so its delta is non-empty
        # — last writer wins.
        save_cookies_to_storage(jar_a, storage, original_snapshot=snapshot_a)
        assert _cookie_value(storage, "__Secure-1PSIDTS", ".google.com") == "NEW_A", (
            "Concurrent legitimate rotations resolve by last-writer-wins; "
            "the fix only prevents *stale* writers from winning"
        )


class TestSiblingWrittenCookieSurvives:
    """A cookie that a sibling process wrote to disk while we were
    holding the jar — and that was never in our snapshot — must survive
    our save unchanged. This is the inverse-side of the §3.4.1 fix:
    not just "don't clobber rotated values" but also "don't drop
    sibling-only entries"."""

    def test_sibling_only_cookie_is_left_alone(self, tmp_path):
        storage = tmp_path / "storage_state.json"
        # Process A opens with just SID.
        jar_a = httpx.Cookies()
        jar_a.set("SID", "sid-A", domain=".google.com", path="/")
        snapshot_a = snapshot_cookie_jar(jar_a)

        # Sibling process B writes a cookie A has never seen
        # (e.g. a per-product OSID it minted while doing its own work).
        _write_storage(
            storage,
            [
                {
                    "name": "SID",
                    "value": "sid-A",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
                {
                    "name": "OSID",
                    "value": "sibling-only",
                    "domain": "accounts.google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
            ],
        )

        # A saves; nothing rotated, snapshot/delta is empty.
        save_cookies_to_storage(jar_a, storage, original_snapshot=snapshot_a)

        # Sibling's OSID must still be on disk.
        assert _cookie_value(storage, "OSID", "accounts.google.com") == "sibling-only", (
            "A cookie a sibling process wrote that A never saw must NOT be "
            "dropped from disk by A's save"
        )


class TestLegacyCallerCompatibility:
    """Callers that don't pass ``original_snapshot`` get the legacy
    full-merge behavior. This is back-compat with code paths that
    haven't yet opted in."""

    def test_legacy_call_writes_in_memory_value(self, tmp_path):
        storage = tmp_path / "storage_state.json"
        _write_storage(
            storage,
            [
                {
                    "name": "SID",
                    "value": "old",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "None",
                },
            ],
        )

        jar = httpx.Cookies()
        jar.set("SID", "new", domain=".google.com", path="/")

        # No original_snapshot → legacy mode → in-memory wins on differing values.
        save_cookies_to_storage(jar, storage)

        assert _cookie_value(storage, "SID", ".google.com") == "new"


class TestRefreshAuthOnBoundSessionIsNoOp:
    """``NotebookLMClient.refresh_auth`` does only a homepage GET. For a
    bound (Playwright-minted) session, that GET does NOT rotate
    ``*PSIDTS`` (per docs/auth-keepalive.md §5.4). With snapshot
    semantics the resulting save must be a no-op — closing the
    "bound-session refresh broadcasts stale state" reachability path
    listed in #361.
    """

    @pytest.mark.asyncio
    async def test_refresh_auth_does_not_clobber_when_nothing_rotated(self, tmp_path, httpx_mock):
        from notebooklm.client import NotebookLMClient

        storage = tmp_path / "storage_state.json"
        _write_storage(
            storage,
            [
                {
                    "name": "__Secure-1PSIDTS",
                    "value": "ONDISK",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
                {
                    "name": "SID",
                    "value": "sid-bound",
                    "domain": ".google.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                },
            ],
        )

        # The client is opened with a stale in-memory copy: *PSIDTS=STALE,
        # mirroring the §3.4.1 timeline where another process has already
        # rotated to ONDISK on disk.
        auth = AuthTokens(
            cookies={
                ("__Secure-1PSIDTS", ".google.com"): "STALE",
                ("SID", ".google.com"): "sid-bound",
            },
            csrf_token="csrf-old",
            session_id="sid-old",
            storage_path=storage,
        )

        # Bound-session homepage GET: no Set-Cookie header, so no rotation.
        httpx_mock.add_response(
            url="https://notebooklm.google.com/",
            content=(
                b"<html><script>window.WIZ_global_data="
                b'{"SNlM0e":"new_csrf","FdrFJe":"new_sid"};</script></html>'
            ),
        )

        client = NotebookLMClient(auth)
        async with client:
            await client.refresh_auth()

        # *PSIDTS on disk must still be ONDISK — refresh_auth's save must
        # NOT have broadcast the stale STALE value back over it.
        assert _cookie_value(storage, "__Secure-1PSIDTS", ".google.com") == "ONDISK", (
            "refresh_auth on a bound session that didn't rotate must not "
            "clobber the disk value with the in-memory stale value"
        )
