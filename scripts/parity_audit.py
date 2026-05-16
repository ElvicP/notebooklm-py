#!/usr/bin/env python3
"""Recurring API parity audit (Spec 0.1).

Heuristically detects when Google ships a NotebookLM feature the library
doesn't model yet. It scrapes a curated list of NotebookLM feature pages
and compares what it finds against the library's own enums
(``ArtifactType``, ``SourceType``, ``ChatMode``/``ChatGoal``/
``ChatResponseLength``, ``ShareAccess``/``ShareViewLevel``/
``SharePermission``).

This is intentionally heuristic: it cannot detect rotated RPC method IDs
(those need network capture) and false positives are acceptable. What it
*can* catch is a brand-new Studio output marketed as "<Name> Overview",
plus a freshness report of which known features are still mentioned.

Exit codes (mirrors scripts/check_rpc_health.py's taxonomy so the
workflow can branch on them):
    0 - No drift detected (and at least one page was audited)
    1 - Potential new feature detected (workflow opens an issue)
    2 - Infrastructure failure: nothing could be audited (all source
        fetches failed / no sources configured). NOT a drift signal.

Usage:
    python scripts/parity_audit.py --output docs/feature-parity.md
    python scripts/parity_audit.py --diff-only        # print drift only
    python scripts/parity_audit.py --input-dir ./pages # offline, local HTML
    make audit
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    # Allow ``make audit`` to work from a source checkout without an install.
    sys.path.insert(0, str(_SRC))

DEFAULT_SOURCES_FILE = REPO_ROOT / "scripts" / "parity_sources.txt"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "feature-parity.md"

GROUPS = ("studio_output", "source_type", "chat_config", "sharing")

# NotebookLM markets exactly two Studio outputs as "<X> Overview". Any other
# "<Word> Overview" phrase on a feature page is a candidate new output.
_KNOWN_OVERVIEW_PREFIXES = {"audio", "video"}
_OVERVIEW_RE = re.compile(r"\b([A-Z][A-Za-z0-9]+)\s+Overview\b")

# Human-readable phrases that map a scraped mention back to an enum value.
# Substring-matched against lowercased page text. Values absent here fall
# back to ``value.replace("_", " ")``.
_ALIASES: dict[str, dict[str, list[str]]] = {
    "studio_output": {
        "audio": ["audio overview", "audio"],
        "video": ["video overview", "video"],
        "report": ["report", "briefing", "study guide"],
        "quiz": ["quiz"],
        "flashcards": ["flashcards", "flashcard"],
        "mind_map": ["mind map"],
        "infographic": ["infographic"],
        "slide_deck": ["slide deck", "slides"],
        "data_table": ["data table"],
    },
    "source_type": {
        "google_docs": ["google doc"],
        "google_slides": ["google slides"],
        "google_spreadsheet": ["google spreadsheet", "google sheet"],
        "pdf": ["pdf"],
        "pasted_text": ["pasted text", "copied text"],
        "web_page": ["web page", "website", "web url"],
        "google_drive_audio": ["drive audio", "google drive audio"],
        "google_drive_video": ["drive video", "google drive video"],
        "youtube": ["youtube"],
        "markdown": ["markdown"],
        "docx": ["docx", "word document"],
        "csv": ["csv"],
        "epub": ["epub"],
        "image": ["image"],
        "media": ["media"],
    },
    "chat_config": {
        "default": ["default chat", "default"],
        "learning_guide": ["learning guide"],
        "concise": ["concise"],
        "detailed": ["detailed"],
        "custom": ["custom prompt"],
        "longer": ["longer response", "verbose"],
        "shorter": ["shorter response", "brief response"],
    },
    "sharing": {
        "restricted": ["restrict", "restricted", "specific people"],
        "anyone_with_link": ["public", "anyone with the link", "share link"],
        "full_notebook": ["full notebook"],
        "chat_only": ["chat only", "chat-only"],
        "owner": ["owner"],
        "editor": ["editor"],
        "viewer": ["viewer"],
    },
}


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join(self._chunks)


def extract_text(page_html: str) -> str:
    """Strip tags (and ``<script>``/``<style>`` bodies), unescape entities,
    and collapse whitespace into a single clean string."""
    parser = _TextExtractor()
    parser.feed(page_html)
    parser.close()
    return re.sub(r"\s+", " ", html.unescape(parser.text())).strip()


# ---------------------------------------------------------------------------
# Known vocabulary (derived from the library's own enums)
# ---------------------------------------------------------------------------


def build_known_vocabulary() -> dict[str, set[str]]:
    """Return the four required feature groups as sets of enum values/names.

    Sourced live from the library so the audit can never drift from the
    code it is auditing.
    """
    from notebooklm.rpc.types import (
        ChatGoal,
        ChatResponseLength,
        ShareAccess,
        SharePermission,
        ShareViewLevel,
    )
    from notebooklm.types import ArtifactType, ChatMode, SourceType

    studio = {a.value for a in ArtifactType if a.value != "unknown"}
    source = {s.value for s in SourceType if s.value != "unknown"}
    chat = {m.value for m in ChatMode}
    chat |= {e.name.lower() for e in ChatGoal if not e.name.startswith("_")}
    chat |= {e.name.lower() for e in ChatResponseLength if not e.name.startswith("_")}
    sharing = {
        e.name.lower()
        for enum in (ShareAccess, ShareViewLevel, SharePermission)
        for e in enum
        if not e.name.startswith("_")
    }
    return {
        "studio_output": studio,
        "source_type": source,
        "chat_config": chat,
        "sharing": sharing,
    }


def _aliases_for(group: str, value: str) -> list[str]:
    return _ALIASES.get(group, {}).get(value, [value.replace("_", " ")])


# ---------------------------------------------------------------------------
# Drift detection + audit
# ---------------------------------------------------------------------------


def detect_potential_drift(text: str, vocabulary: dict[str, set[str]]) -> set[str]:
    """Flag ``<Word> Overview`` phrases whose prefix is not a known Studio
    output. Heuristic — false positives are acceptable per the spec."""
    found: set[str] = set()
    for match in _OVERVIEW_RE.finditer(text):
        prefix = match.group(1)
        if prefix.lower() not in _KNOWN_OVERVIEW_PREFIXES:
            found.add(f"{prefix} Overview")
    return found


@dataclass
class AuditResult:
    pages_fetched: int
    pages_failed: list[str] = field(default_factory=list)
    covered: dict[str, set[str]] = field(default_factory=dict)
    potential_new: set[str] = field(default_factory=set)


def run_audit(pages: dict[str, str]) -> AuditResult:
    """Audit ``{url: html}`` against the known vocabulary (pure / no I/O)."""
    vocab = build_known_vocabulary()
    covered: dict[str, set[str]] = {g: set() for g in GROUPS}
    potential_new: set[str] = set()

    for page_html in pages.values():
        text = extract_text(page_html)
        lowered = text.lower()
        for group in GROUPS:
            for value in vocab[group]:
                if any(alias in lowered for alias in _aliases_for(group, value)):
                    covered[group].add(value)
        potential_new |= detect_potential_drift(text, vocab)

    return AuditResult(
        pages_fetched=len(pages),
        covered=covered,
        potential_new=potential_new,
    )


# ---------------------------------------------------------------------------
# Fetching (injectable so tests never hit the network)
# ---------------------------------------------------------------------------


def _default_fetcher() -> Callable[[str], str]:
    import httpx

    def _get(url: str) -> str:
        resp = httpx.get(
            url,
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "notebooklm-py-parity-audit"},
        )
        resp.raise_for_status()
        return resp.text

    return _get


def fetch_pages(
    urls: list[str], *, fetcher: Callable[[str], str] | None = None
) -> tuple[dict[str, str], list[str]]:
    """Fetch ``urls`` with an injectable ``fetcher``. Returns
    ``({url: html}, [failed_url, ...])`` — a failed fetch is recorded, not
    raised, so one dead link can't abort the whole audit."""
    if fetcher is None:
        fetcher = _default_fetcher()
    pages: dict[str, str] = {}
    failed: list[str] = []
    for url in urls:
        try:
            pages[url] = fetcher(url)
        except Exception:  # noqa: BLE001 — any fetch error is just a skip
            failed.append(url)
    return pages, failed


def load_sources(path: str | Path) -> list[str]:
    """Read a newline-delimited sources file. ``#`` comments and blank
    lines are ignored; surrounding whitespace is stripped. Missing file
    yields an empty list (treated as an infra failure by ``main``)."""
    p = Path(path)
    if not p.is_file():
        return []
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(stripped)
    return out


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_GROUP_TITLES = {
    "studio_output": "Studio output types",
    "source_type": "Source types",
    "chat_config": "Chat configuration options",
    "sharing": "Sharing options",
}


def render_markdown(result: AuditResult, *, generated_at: str | None = None) -> str:
    vocab = build_known_vocabulary()
    when = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "# NotebookLM Feature Parity",
        "",
        "_Generated automatically by `scripts/parity_audit.py` (Spec 0.1)._",
        f"_Last audited: {when} · pages audited: {result.pages_fetched}._",
        "",
        "This is a **heuristic** freshness check, not an exhaustive contract. "
        "It cannot detect rotated RPC method IDs.",
        "",
    ]
    if result.potential_new:
        lines += [
            "## ⚠️ Potential new features detected",
            "",
            "These phrases looked like Studio outputs the library does not "
            "model yet — verify manually:",
            "",
        ]
        lines += [f"- `{p}`" for p in sorted(result.potential_new)]
        lines.append("")

    for group in GROUPS:
        known = sorted(vocab[group])
        seen = result.covered.get(group, set())
        lines += [f"## {_GROUP_TITLES[group]}", ""]
        for value in known:
            mark = "x" if value in seen else " "
            lines.append(f"- [{mark}] `{value}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _pages_from_input_dir(input_dir: str) -> dict[str, str]:
    d = Path(input_dir)
    pages: dict[str, str] = {}
    for html_file in sorted(d.glob("*.html")):
        pages[str(html_file)] = html_file.read_text(encoding="utf-8")
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NotebookLM API parity audit")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path to write the generated feature-parity doc.",
    )
    parser.add_argument(
        "--sources",
        default=str(DEFAULT_SOURCES_FILE),
        help="Newline-delimited file of feature-page URLs.",
    )
    parser.add_argument(
        "--input-dir",
        default=None,
        help="Audit local *.html files instead of fetching (offline / tests).",
    )
    parser.add_argument(
        "--diff-only",
        action="store_true",
        help="Print drift to stdout and do NOT (re)write the doc.",
    )
    args = parser.parse_args(argv)

    if args.input_dir is not None:
        pages = _pages_from_input_dir(args.input_dir)
        failed: list[str] = []
    else:
        urls = load_sources(args.sources)
        pages, failed = fetch_pages(urls, fetcher=_default_fetcher())

    if not pages:
        print(
            f"parity-audit: nothing audited (no reachable sources). failed={failed}",
            file=sys.stderr,
        )
        return 2

    result = run_audit(pages)
    result.pages_failed = failed

    if result.potential_new:
        print("parity-audit: potential new feature(s) detected:", file=sys.stderr)
        for phrase in sorted(result.potential_new):
            print(f"  - {phrase}", file=sys.stderr)

    if args.diff_only:
        if not result.potential_new:
            print("parity-audit: no drift detected.")
        return 1 if result.potential_new else 0

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(render_markdown(result), encoding="utf-8")
    print(f"parity-audit: wrote {args.output} ({result.pages_fetched} page(s)).")
    return 1 if result.potential_new else 0


if __name__ == "__main__":
    raise SystemExit(main())
