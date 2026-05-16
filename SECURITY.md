# Security Policy

## Supported Versions

Only the latest minor release receives security fixes. Earlier `0.x` releases predate the current API surface and are no longer maintained — please upgrade to the latest version.

| Version | Supported          |
| ------- | ------------------ |
| 0.4.x   | :white_check_mark: |
| < 0.4   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it by:

1. **DO NOT** open a public GitHub issue
2. Email the maintainers directly (see `pyproject.toml` for contact info)
3. Include a detailed description of the vulnerability
4. Allow reasonable time for a fix before public disclosure

## Credential Security

This library stores authentication credentials locally. Please understand these security considerations:

### Storage Locations

Default location is `~/.notebooklm/` (can be changed via `NOTEBOOKLM_HOME` environment variable):

| File | Contents | Permissions |
|------|----------|-------------|
| `storage_state.json` | Google session cookies | `0o600` (owner-only) |
| `browser_profile/` | Chromium profile data | `0o700` (owner-only) |
| `context.json` | Active notebook ID | Default |

### Security Best Practices

1. **Protect your credentials**
   - The `storage_state.json` file contains your Google session cookies
   - Anyone with access to this file can impersonate your Google account to NotebookLM
   - Never share, commit, or expose this file

2. **Add to .gitignore**
   ```gitignore
   .notebooklm/
   ```

3. **Credential rotation**
   - Re-run `notebooklm login` periodically to refresh credentials
   - Sessions typically last days to weeks before expiring

4. **If credentials are compromised**
   - Immediately revoke access at [Google Security Settings](https://myaccount.google.com/permissions)
   - Delete the `~/.notebooklm/` directory
   - Re-authenticate with `notebooklm login`

5. **CI/CD usage**
   - Do not commit credentials to repositories
   - Use `NOTEBOOKLM_AUTH_JSON` environment variable for secure, file-free authentication
   - Store the JSON value in GitHub Secrets or similar secure secret management
   - The env var approach keeps credentials in memory only, never written to disk

### Log Redaction

Logs are scrubbed of credential-shaped data (CSRF/`at=` tokens, `f.sid=`,
OAuth params, Google session cookies, `Authorization`/`Cookie`/`Set-Cookie`
headers) before they are emitted. This applies to:

- The `notebooklm` package logger and all of its child loggers.
- The third-party **`httpx` and `httpcore`** loggers — which otherwise leak
  full request URLs (with auth query params) and cookie headers when you
  enable them at `DEBUG`. Redaction is applied automatically on import and
  is **on by default**.

You can opt out of the `httpx`/`httpcore` scrubbing **only** for advanced
debugging by setting `NOTEBOOKLM_REDACT_HTTPX=0` (also accepts
`false`/`no`/`off`). Doing so will print raw credentials to your logs — do
not enable it in shared, persisted, or CI logs. See
[docs/troubleshooting.md](docs/troubleshooting.md#httpx--httpcore-log-redaction)
for details.

### What This Library Does NOT Do

- Does not transmit credentials to any third party
- Does not store passwords (uses browser-based OAuth)
- Does not access data outside of NotebookLM
- Does not modify Google account settings

## Dependency Security

This library uses minimal dependencies:

| Dependency | Purpose | Security Notes |
|------------|---------|----------------|
| `httpx` | HTTP client | Well-maintained, security-focused |
| `click` | CLI framework | Stable, minimal attack surface |
| `rich` | Terminal output | Cosmetic, no network access |
| `playwright` | Browser automation (optional) | Used only for login |

### Auditing Dependencies

```bash
# Install pip-audit
pip install pip-audit

# Run security audit
pip-audit
```

## Known Limitations

### Undocumented API

This library uses Google's internal APIs, which means:

- **No official security guarantees** from Google
- **API changes without notice** may break functionality
- **Rate limiting** may be applied by Google
- **Account restrictions** are possible for unusual usage patterns

### Session Security

- Sessions are cookie-based (standard web authentication)
- CSRF tokens are required and automatically handled
- No long-lived API keys or OAuth tokens

## Questions?

For security questions that are not vulnerabilities, open a [GitHub Discussion](https://github.com/teng-lin/notebooklm-py/discussions).
