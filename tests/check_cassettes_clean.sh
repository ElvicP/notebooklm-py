#!/usr/bin/env bash
# CI grep guard for cassette PII leaks.
#
# Fails the build (exit 1) if any cassette under tests/cassettes/ contains:
#   - an unsanitized email at a real provider (gmail, googlemail, google,
#     anthropic, outlook, hotmail, yahoo, icloud, protonmail)
#   - an unsanitized Google session cookie value (SID/SAPISID/HSID/SSID/APISID
#     or any __Secure-[13]PSID variant) whose value does NOT start with 'S'
#     — the canonical scrubbed sentinel is "SCRUBBED".
#
# Usage:
#   ./tests/check_cassettes_clean.sh
#
# Exit codes:
#   0 — cassettes are clean
#   1 — one or more leaks found
set -e

# Use git grep when inside a repo (fast, respects .gitignore); fall back to
# plain grep otherwise so callers can run this against an untracked seeded
# cassette in a tmpdir during tests.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    GREP=(git grep -nE)
else
    GREP=(grep -rnE)
fi

email_re='"[A-Za-z0-9._%+-]+@(gmail|googlemail|google|anthropic|outlook|hotmail|yahoo|icloud|protonmail)\.com"'
cookie_re='"(SID|SAPISID|HSID|SSID|APISID|__Secure-[13]PSID)"[[:space:]]*:[[:space:]]*"[^S][^"]+"'

if "${GREP[@]}" "$email_re" tests/cassettes/ ; then
    echo "ERROR: unsanitized email found in cassette" >&2
    exit 1
fi

if "${GREP[@]}" "$cookie_re" tests/cassettes/ ; then
    echo "ERROR: unsanitized cookie value found in cassette" >&2
    exit 1
fi

echo "OK: cassettes are sanitized"
