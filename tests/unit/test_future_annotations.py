"""Enforce ``from __future__ import annotations`` across every source module.

Spec 0.2 of the improvement plan establishes this invariant. The companion
ruff rule ``FA`` enforces it on changed lines via the linter, but a runtime
test guards against the case where ruff's per-file ignores grow over time
and let a regression slip in. Keeping the assertion as a test also makes
the rationale visible in CI failure output.

Why the invariant matters:
- Eager evaluation of PEP 604 union syntax (``X | Y``) in annotations
  imposes import-time cost and triggers ``NameError`` on forward refs
  unless quoted.
- ``from __future__ import annotations`` defers all annotations to strings,
  which removes both costs and unifies the surface so downstream tools
  (mypy, sphinx) see the same shape regardless of file age.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "notebooklm"


def _has_future_annotations(source: str) -> bool:
    """Return True if ``source`` imports ``annotations`` from ``__future__``."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            for alias in node.names:
                if alias.name == "annotations":
                    return True
    return False


def _all_source_files() -> list[Path]:
    """All ``.py`` files under ``src/notebooklm/``."""
    return sorted(SRC_ROOT.rglob("*.py"))


def test_every_source_file_has_future_annotations() -> None:
    """Every module under ``src/notebooklm/`` declares the future import.

    If this fails on a freshly-added file, prepend
    ``from __future__ import annotations`` immediately after the docstring.
    """
    offenders: list[str] = []
    for path in _all_source_files():
        source = path.read_text(encoding="utf-8")
        if not _has_future_annotations(source):
            offenders.append(str(path.relative_to(SRC_ROOT.parent.parent)))

    assert not offenders, (
        "Files missing 'from __future__ import annotations':\n  "
        + "\n  ".join(offenders)
        + "\n\nFix: add the import directly after the module docstring."
    )


@pytest.mark.parametrize("path", _all_source_files(), ids=lambda p: p.name)
def test_individual_file_has_future_annotations(path: Path) -> None:
    """Per-file parametrized check for easier triage in CI output."""
    source = path.read_text(encoding="utf-8")
    assert _has_future_annotations(source), (
        f"{path.relative_to(SRC_ROOT.parent.parent)} is missing "
        "'from __future__ import annotations'"
    )
