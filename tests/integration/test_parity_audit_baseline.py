"""Integration: run the parity audit against a frozen baseline fixture.

Asserts **no false negatives** — every Studio output type that the
``ArtifactType`` enum knows about is recognized in a page that mentions
them all — and **no false positive** drift on a curated known-only page.
This is the regression guard the spec asks for: if the heuristic ever
stops recognizing a known feature, this fails.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "parity_audit.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "parity" / "known_features.html"


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


def test_baseline_no_false_negative_on_known_studio_outputs(pa):
    from notebooklm.types import ArtifactType

    html = FIXTURE.read_text(encoding="utf-8")
    result = pa.run_audit({str(FIXTURE): html})

    expected = {a.value for a in ArtifactType if a.value != "unknown"}
    missing = expected - result.covered["studio_output"]
    assert not missing, f"audit failed to recognize known Studio outputs: {missing}"


def test_baseline_has_no_false_positive_drift(pa):
    html = FIXTURE.read_text(encoding="utf-8")
    result = pa.run_audit({str(FIXTURE): html})
    assert result.potential_new == set(), (
        f"curated known-only baseline should not report drift: {result.potential_new}"
    )


def test_baseline_covers_all_four_required_areas(pa):
    html = FIXTURE.read_text(encoding="utf-8")
    result = pa.run_audit({str(FIXTURE): html})
    for group in ("studio_output", "source_type", "chat_config", "sharing"):
        assert result.covered[group], f"no coverage detected for required area: {group}"
