# CLI Exit-Code Convention

**Status:** Active
**Last Updated:** 2026-05-14

This document defines the exit-code policy for the `notebooklm` CLI. Shell
scripts, CI pipelines, and AI-agent automations should rely on these codes for
control flow rather than scraping stdout/stderr text — the text is intended for
humans and may evolve, but the exit-code contract is stable.

For the canonical implementation, see
[`src/notebooklm/cli/error_handler.py`](../src/notebooklm/cli/error_handler.py)
(lines 64-67 for the policy table, line 81 for the SIGINT handler).

## Standard exit codes

| Code | Meaning | When you'll see it |
|------|---------|-------------------|
| `0`  | Success | The command completed and produced its intended effect. |
| `1`  | User / application error | Validation, authentication, rate limiting, network failure, configuration error, or any `NotebookLMError` raised by the library. |
| `2`  | System / unexpected error | Unhandled exception (likely a bug). The CLI suggests reporting at the issue tracker. Also used for the `source wait` timeout (see exceptions below). |
| `130`| Cancelled by user | The process received `SIGINT` (Ctrl-C). `130 = 128 + signal 2`, the conventional shell value for SIGINT-terminated processes. |

The policy comment in `error_handler.py:64-67` is the source of truth:

```text
Exit codes:
    1: User/application error (validation, auth, rate limit, etc.)
    2: System/unexpected error (bugs, unhandled exceptions)
    130: Keyboard interrupt (128 + signal 2)
```

## Exception → exit-code mapping

The `handle_errors` context manager wrapping every CLI command translates
library exceptions into exit codes. The table below summarises the live
mapping in `error_handler.py`:

| Library exception | JSON `code` | Exit |
|---|---|---|
| `RateLimitError`        | `RATE_LIMITED`      | `1` |
| `AuthError`             | `AUTH_ERROR`        | `1` |
| `ValidationError`       | `VALIDATION_ERROR`  | `1` |
| `ConfigurationError`    | `CONFIG_ERROR`      | `1` |
| `NetworkError`          | `NETWORK_ERROR`     | `1` |
| `NotebookLimitError`    | `NOTEBOOK_LIMIT`    | `1` |
| `NotebookLMError` (other) | `NOTEBOOKLM_ERROR` | `1` |
| `KeyboardInterrupt`     | `CANCELLED`         | `130` |
| Anything else (`Exception`) | `UNEXPECTED_ERROR` | `2` |
| `click.ClickException` (e.g. bad CLI args) | — | re-raised; Click exits `2` |

`click.ClickException` (raised by `click.UsageError` / `click.BadParameter` and
the like) is intentionally re-raised so Click can render its own
`Usage: ...` error and exit with its standard code (`2` for usage errors).

## JSON output mode (`--json`)

When a command supports `--json` (or `--json-output`) and the flag is set,
errors are emitted as a JSON document on stdout *and* the exit code still
applies. The shape is:

```json
{
  "error": true,
  "code": "RATE_LIMITED",
  "message": "Error: Rate limited. Retry after 30s.",
  "retry_after": 30
}
```

The `code` field is the stable identifier (see table above); `message` is the
human string and may change. Some errors include extra fields
(`retry_after`, `method_id` when `-v/--verbose` is set, etc.). Automation
should branch on `code` (or, more simply, on the exit code).

## Intentional exceptions to the standard convention

Two commands deliberately invert or extend the standard codes because their
primary use case is shell control flow. **These are by design and will not
change.** Code referencing them should comment the inverted semantics.

### `notebooklm source stale <SOURCE_ID>` — inverted

Implemented at
[`src/notebooklm/cli/source.py`](../src/notebooklm/cli/source.py) lines
1056-1082.

| Exit | Meaning |
|------|---------|
| `0`  | Source is **stale** (needs `source refresh`) |
| `1`  | Source is **fresh** (no action required) |

The inversion lets you write the natural shell idiom:

```sh
if notebooklm source stale "$SRC_ID"; then
    notebooklm source refresh "$SRC_ID"
fi
```

A `0` exit reads as "yes, the predicate (stale) holds, run the body" — the
same convention as `test`, `grep -q`, etc.

Note: under `set -e` the `1` exit when the source is fresh will abort the
script. Use the predicate inside an `if`/`elif`/`||` (as above), which
shell's errexit explicitly excludes, or `set +e` around the call.

### `notebooklm source wait <SOURCE_ID>` — three-way

Implemented at
[`src/notebooklm/cli/source.py`](../src/notebooklm/cli/source.py) lines
1113-1116.

| Exit | Meaning |
|------|---------|
| `0`  | Source is ready |
| `1`  | Source not found or processing failed |
| `2`  | Timeout reached before the source became ready |

This is the only command whose `2` exit does **not** indicate a bug — it is
a recoverable condition the caller may want to retry with a longer
`--timeout`. Scripts that distinguish "transient" from "fatal" should branch
on the specific code rather than the truthy/falsy value:

```sh
notebooklm source wait "$SRC_ID" --timeout 300
case $? in
  0)  echo "ready" ;;
  1)  echo "failed"; exit 1 ;;
  2)  echo "timed out, retry later"; exit 75 ;;  # EX_TEMPFAIL
  *)  echo "unexpected"; exit 1 ;;
esac
```

## Recipes for callers

### Shell

```sh
# Standard — non-zero is failure
if ! notebooklm ask "$NOTEBOOK_ID" "Summarize"; then
    echo "ask failed (exit $?)" >&2
    exit 1
fi

# Distinguish bug from user error
notebooklm <cmd> --json > out.json
case $? in
  0)   ;;                                 # success
  1)   jq -r .code out.json ;;            # user/app error — branch on code
  2)   echo "internal CLI error" >&2 ;;   # bug; report it
  130) echo "cancelled by user" >&2 ;;    # ^C
esac
```

### Python `subprocess`

```python
import json
import subprocess
import time

result = subprocess.run(
    ["notebooklm", "ask", nb_id, prompt, "--json"],
    capture_output=True, text=True,
)
if result.returncode == 0:
    payload = json.loads(result.stdout)
elif result.returncode == 1:
    err = json.loads(result.stdout)  # JSON error document
    if err["code"] == "RATE_LIMITED":
        time.sleep(err.get("retry_after", 30))
elif result.returncode == 2:
    raise RuntimeError(f"CLI bug: {result.stdout}")
elif result.returncode == 130:
    raise KeyboardInterrupt
```

## Migration notes

The following shifts will land in **Phase 3** of the
[`cli-ux-remediation`](../.sisyphus/plans/cli-ux-remediation.md) plan and are
documented here so callers can prepare. The current behavior is described
above; these notes describe the upcoming change.

### C1 — `get`-on-not-found will exit `1` (currently `0`)

`notebooklm source get`, `notebooklm artifact get`, and `notebooklm note get`
currently print a "not found" message to stdout and exit `0` when the
requested ID is missing. Phase 3 will change all three to exit `1` so the
"not found" condition matches the rest of the CLI's user-error convention
and so scripts can branch on the exit code without parsing output text.

If your script relies on the current `0`-on-not-found behavior, switch to
inspecting the command output (or, after Phase 3 lands, branch on the exit
code: `notebooklm source get "$SRC_ID" || handle_missing`).

### I14 — `download` exception paths will route through the typed handler

The `download` command group currently catches some exceptions in
command-local `try/except` blocks that bypass the central `handle_errors`
context. Concretely, generic `Exception` bubbles do not always honor `--json`
(emitting plain stderr text instead of the JSON error document) and the exit
code may not match the standard exception → exit-code mapping.

Phase 3 will route all `download` exception paths through `handle_errors` so
that:

- `--json` consistently produces the JSON error document on every failure.
- Exit codes match the standard table above (`1` for known library errors,
  `2` for unexpected, `130` for `^C`).

Callers already relying on `--json` should see no behavior change for
*successful* downloads or for already-typed errors; only the previously
plain-text exception paths will start emitting JSON.

## See also

- [CLI Reference](cli-reference.md) — command-by-command documentation
- [Configuration](configuration.md) — `--json` and global options
- [Troubleshooting](troubleshooting.md) — interpreting common errors
- [`src/notebooklm/cli/error_handler.py`](../src/notebooklm/cli/error_handler.py)
  — canonical implementation
