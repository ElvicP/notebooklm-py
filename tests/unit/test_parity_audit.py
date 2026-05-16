"""Tests for ``scripts/parity_audit.py`` (Spec 0.1).

The script is imported via spec-loading rather than ``from scripts...``
because ``scripts/`` is not a package in this repo (mirrors
``test_check_coverage_thresholds.py``).

The audit is heuristic by design (the spec says false positives are
acceptable and it cannot detect rotated RPC IDs). These tests pin the
*contract*: HTML→text extraction, enum-derived known vocabulary covering
the four required areas, "<X> Overview" drift detection, an injectable
fetcher (so no network in tests), exit codes, and the generated doc.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "parity_audit.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("parity_audit", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["parity_audit"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pa():
    return _load_module()


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------


def test_extract_text_strips_tags_script_and_style(pa):
    html = (
        "<html><head><style>.x{color:red}</style>"
        "<script>var leak='SECRET'</script></head>"
        "<body><h1>Audio Overview</h1><p>Create a&nbsp;<b>Mind Map</b>.</p></body></html>"
    )
    text = pa.extract_text(html)
    assert "Audio Overview" in text
    assert "Mind Map" in text
    assert "SECRET" not in text  # script contents dropped
    assert "color:red" not in text  # style contents dropped
    assert "<" not in text and ">" not in text


# ---------------------------------------------------------------------------
# Known vocabulary covers the four required areas
# ---------------------------------------------------------------------------


def test_known_vocabulary_has_four_required_groups(pa):
    vocab = pa.build_known_vocabulary()
    assert {"studio_output", "source_type", "chat_config", "sharing"} <= set(vocab)


def test_known_vocabulary_reflects_enums(pa):
    vocab = pa.build_known_vocabulary()
    # Studio outputs (ArtifactType)
    assert {"audio", "video", "mind_map", "infographic", "slide_deck"} <= vocab["studio_output"]
    # Source types (SourceType)
    assert {"pdf", "youtube", "web_page", "google_docs"} <= vocab["source_type"]
    # Chat config (ChatMode/ChatGoal/ChatResponseLength) — at least the modes
    assert {"learning_guide"} <= vocab["chat_config"]
    # Sharing (ShareAccess/ShareViewLevel/SharePermission)
    assert len(vocab["sharing"]) >= 2


# ---------------------------------------------------------------------------
# Drift detection: "<X> Overview" heuristic
# ---------------------------------------------------------------------------


def test_no_drift_when_only_known_overviews_present(pa):
    text = "NotebookLM can make an Audio Overview and a Video Overview from your sources."
    drift = pa.detect_potential_drift(text, pa.build_known_vocabulary())
    assert drift == set()


def test_detects_unknown_overview_as_drift(pa):
    text = "Introducing the new Hologram Overview — a 3D way to explore your notebook."
    drift = pa.detect_potential_drift(text, pa.build_known_vocabulary())
    assert any("hologram" in d.lower() for d in drift)


def test_run_audit_reports_known_features_covered(pa):
    pages = {
        "https://example.test/a": (
            "<body>Audio Overview, Video Overview, Mind Map, Reports, "
            "Flashcards, Quiz. Sources: PDF, YouTube, Google Docs.</body>"
        )
    }
    result = pa.run_audit(pages)
    assert result.pages_fetched == 1
    assert "audio" in result.covered["studio_output"]
    assert "video" in result.covered["studio_output"]
    assert result.potential_new == set()


def test_run_audit_flags_new_studio_output(pa):
    pages = {"u": "<body>Try the brand new Quantum Overview today.</body>"}
    result = pa.run_audit(pages)
    assert result.potential_new  # non-empty → drift


# ---------------------------------------------------------------------------
# Injectable fetcher (no network in tests)
# ---------------------------------------------------------------------------


def test_fetch_pages_uses_injected_fetcher(pa):
    calls = []

    def fake_fetch(url: str) -> str:
        calls.append(url)
        return f"<html><body>content of {url}</body></html>"

    pages, failed = pa.fetch_pages(["https://a.test", "https://b.test"], fetcher=fake_fetch)
    assert failed == []
    assert set(pages) == {"https://a.test", "https://b.test"}
    assert calls == ["https://a.test", "https://b.test"]


def test_fetch_pages_records_failures(pa):
    good = "https://good.test"
    bad = "https://bad.test"

    def flaky(url: str) -> str:
        # Exact match (not a URL substring check) — keeps CodeQL's
        # py/incomplete-url-substring-sanitization heuristic from firing
        # on test scaffolding.
        if url == bad:
            raise RuntimeError("boom")
        return "<body>ok</body>"

    pages, failed = pa.fetch_pages([good, bad], fetcher=flaky)
    assert list(pages) == [good]
    assert failed == [bad]


# ---------------------------------------------------------------------------
# Sources file parsing
# ---------------------------------------------------------------------------


def test_load_sources_ignores_comments_and_blanks(pa, tmp_path):
    f = tmp_path / "sources.txt"
    f.write_text(
        "# a comment\n\nhttps://one.test\n  https://two.test  \n# trailing\n",
        encoding="utf-8",
    )
    assert pa.load_sources(f) == ["https://one.test", "https://two.test"]


# ---------------------------------------------------------------------------
# CLI: exit codes + doc generation
# ---------------------------------------------------------------------------


def _write_html_dir(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / "pages"
    d.mkdir(exist_ok=True)
    (d / name).write_text(f"<html><body>{body}</body></html>", encoding="utf-8")
    return d


def test_main_clean_returns_0_and_writes_doc(pa, tmp_path):
    d = _write_html_dir(tmp_path, "p.html", "Audio Overview and Video Overview and Mind Map")
    out = tmp_path / "feature-parity.md"
    rc = pa.main(["--input-dir", str(d), "--output", str(out)])
    assert rc == 0
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "Studio" in content or "studio_output" in content
    assert "audio" in content


def test_main_drift_returns_1(pa, tmp_path):
    d = _write_html_dir(tmp_path, "p.html", "Brand new Telepathy Overview just shipped")
    out = tmp_path / "fp.md"
    rc = pa.main(["--input-dir", str(d), "--output", str(out)])
    assert rc == 1


def test_main_all_sources_fail_returns_2(pa, tmp_path, monkeypatch):
    def always_fail(url: str) -> str:
        raise RuntimeError("network down")

    monkeypatch.setattr(pa, "_default_fetcher", lambda: always_fail)
    out = tmp_path / "fp.md"
    rc = pa.main(
        ["--sources", str(tmp_path / "missing.txt"), "--output", str(out)],
    )
    assert rc == 2


def test_main_diff_only_does_not_write_full_doc(pa, tmp_path, capsys):
    d = _write_html_dir(tmp_path, "p.html", "Unknown Mirage Overview here")
    out = tmp_path / "fp.md"
    rc = pa.main(["--input-dir", str(d), "--output", str(out), "--diff-only"])
    assert rc == 1
    # --diff-only must not overwrite the canonical doc.
    assert not out.exists()
    captured = capsys.readouterr()
    assert "Mirage" in captured.out or "Mirage" in captured.err
