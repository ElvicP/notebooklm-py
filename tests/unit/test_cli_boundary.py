"""AST lint: enforce the CLI -> client/types boundary.

Block-list rules applied to every ``src/notebooklm/cli/**/*.py`` file:

1. No imports of private top-level modules:
   ``from notebooklm._foo import ...`` / ``from .._foo import ...`` /
   ``from notebooklm import _foo`` / ``from .. import _foo`` are all rejected.
2. No imports from the RPC layer:
   ``from notebooklm.rpc`` / ``from notebooklm.rpc.<x>`` / ``from ..rpc`` /
   ``from ..rpc.<x>`` / ``import notebooklm.rpc`` are all rejected. The CLI
   must consume RPC enums via the public ``notebooklm.types`` re-export.
3. No private-name leakage from a public module:
   ``from notebooklm.<public> import _symbol`` / ``from ..<public> import _symbol``
   is rejected when ``<public>`` does not itself start with ``_``. This stops
   the CLI from reaching into a public module's internals (e.g.
   ``from notebooklm.auth import _internal_helper``). Dunders (``__version__``)
   are allowed.

Allowed:
- Intra-cli imports (level == 1): ``from ._encoding import ...``, including
  underscored siblings — those are the CLI's own private modules.
- Imports of non-underscored siblings/parents:
  ``from ..types import ...``, ``from ..research import ...``, etc.
"""

from __future__ import annotations

import ast
import pathlib

CLI_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "notebooklm" / "cli"


def _is_rpc_module(mod: str) -> bool:
    """True if ``mod`` is the RPC layer (``rpc`` or ``rpc.<anything>``)."""
    return mod == "rpc" or mod.startswith("rpc.")


def _violations(tree: ast.AST) -> list[str]:
    bad: list[str] = []
    for node in ast.walk(tree):
        # `from X import …`
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 0:
                # Absolute imports: only inspect notebooklm.* roots.
                parts = mod.split(".")
                if len(parts) >= 2 and parts[0] == "notebooklm":
                    sub = parts[1]
                    sub_path = ".".join(parts[1:])
                    # Rule 1 (notebooklm._<private_module>) or
                    # Rule 2 (notebooklm.rpc[.x]) — both reject the whole import.
                    if sub.startswith("_") or _is_rpc_module(sub_path):
                        bad.append(f"from {mod} import ...")
                    # Rule 3: notebooklm.<public_module> import _symbol
                    elif len(parts) == 2:
                        for alias in node.names:
                            if alias.name.startswith("_") and not alias.name.startswith("__"):
                                bad.append(f"from {mod} import {alias.name}")
                # `from notebooklm import _foo` — private module via imported names.
                # Dunders like `__version__` are public package attrs by convention.
                if mod == "notebooklm":
                    for alias in node.names:
                        if alias.name.startswith("_") and not alias.name.startswith("__"):
                            bad.append(f"from notebooklm import {alias.name}")
            elif node.level >= 2:
                # Relative parent-package imports (cli reaches into notebooklm/*).
                # Rule 1 (`from .._foo import ...`) or
                # Rule 2 (`from ..rpc[.x] import ...`) — both reject the import.
                if mod.startswith("_") or (mod and _is_rpc_module(mod)):
                    bad.append(f"from {'.' * node.level}{mod} import ...")
                # Rule 3: `from ..<public> import _symbol`
                # Only single-segment public modules — sub-modules under a
                # private parent are already covered by rule 1 on the parent.
                elif mod and "." not in mod:
                    for alias in node.names:
                        if alias.name.startswith("_") and not alias.name.startswith("__"):
                            bad.append(f"from {'.' * node.level}{mod} import {alias.name}")
                # `from .. import _foo` — private parent module via imported names.
                # Dunders like `__version__` are public package attrs by convention.
                if mod == "":
                    for alias in node.names:
                        if alias.name.startswith("_") and not alias.name.startswith("__"):
                            bad.append(f"from {'.' * node.level} import {alias.name}")
            # level == 1 is intra-cli; underscore there is fine (cli's own private modules)
        # `import X` / `import X as Y`
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if len(parts) >= 2 and parts[0] == "notebooklm":
                    sub = parts[1]
                    sub_path = ".".join(parts[1:])
                    # Rule 1 (import notebooklm._<private>) or
                    # Rule 2 (import notebooklm.rpc[.x]) — both rejected.
                    if sub.startswith("_") or _is_rpc_module(sub_path):
                        bad.append(f"import {alias.name}")
    return bad


def test_no_private_module_imports_in_cli():
    offenders: list[tuple[str, list[str]]] = []
    for path in sorted(CLI_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bad = _violations(tree)
        if bad:
            offenders.append((str(path.relative_to(CLI_ROOT.parent)), bad))
    assert not offenders, (
        "CLI must not import notebooklm._* (private modules), notebooklm.rpc.*, "
        "or `_private` names out of public notebooklm modules. "
        "Promote needed symbols to a public module (config/urls/log/research/types) "
        f"and import from there.\nOffenders: {offenders}"
    )
