"""Snapshot tests asserting CLI group docstrings list every registered subcommand.

This guardrail prevents help-text drift: when a new subcommand is added to a
``click.group()`` (e.g. ``source clean``, ``artifact suggestions``), the group's
``--help`` output must continue to enumerate it in the docstring "Commands:"
block. Without this test, contributors could silently land a new subcommand
that ``--help`` users would never discover from the group's overview.

Coverage scope: the four CLI groups whose docstrings explicitly list their
subcommands as a discoverability aid (``source``, ``artifact``, ``note``,
``download``). Other groups (``profile``, ``share``, ``research``, etc.) rely
on Click's auto-generated subcommand table and are not in scope here.

Hidden subcommands: none of the in-scope groups currently mark any subcommand
``hidden=True``. If a future contributor adds one, this test will require it
to be listed in the docstring too — adjust the comprehension to filter
``c.hidden`` or move the hidden command out of the scoped groups.
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from notebooklm.cli.artifact import artifact
from notebooklm.cli.download import download
from notebooklm.cli.note import note
from notebooklm.cli.source import source


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# Groups whose docstring "Commands:" / "Types:" block must enumerate every
# registered subcommand. Keep this list in sync with cli/__init__.py.
GROUPS: list[tuple[str, click.Group]] = [
    ("source", source),
    ("artifact", artifact),
    ("note", note),
    ("download", download),
]


@pytest.mark.parametrize("group_name,group", GROUPS, ids=[g[0] for g in GROUPS])
def test_group_help_lists_every_subcommand(
    group_name: str,
    group: click.Group,
    runner: CliRunner,
) -> None:
    """Every subcommand registered on a group must appear in its help output.

    Walks ``group.commands`` and asserts each name is present in the rendered
    ``--help`` output. Click already auto-generates a "Commands:" table at the
    bottom of help, so this is a belt-and-suspenders check that also catches
    drift in the hand-written docstring "Commands:" block at the top of each
    group.
    """
    result = runner.invoke(group, ["--help"])
    assert result.exit_code == 0, (
        f"`{group_name} --help` failed with exit {result.exit_code}: {result.output}"
    )

    missing = [subcmd for subcmd in group.commands if subcmd not in result.output]
    assert not missing, (
        f"`{group_name} --help` is missing subcommand(s): {missing}. "
        f"Update the group docstring 'Commands:' block in "
        f"src/notebooklm/cli/{group_name}.py to include them."
    )


@pytest.mark.parametrize("group_name,group", GROUPS, ids=[g[0] for g in GROUPS])
def test_group_docstring_lists_every_subcommand(
    group_name: str,
    group: click.Group,
    runner: CliRunner,
) -> None:
    """Each subcommand must appear in the group's hand-written docstring.

    Click's auto-generated "Commands:" table can mask docstring drift in the
    rendered ``--help`` output (the same name shows up twice). This stricter
    check inspects ``group.help`` (the docstring) directly so a missing entry
    in the curated "Commands:" / "Types:" block is caught even when Click's
    table papers over it.
    """
    docstring = group.help or ""
    assert docstring, f"`{group_name}` group has no docstring"

    missing = [subcmd for subcmd in group.commands if subcmd not in docstring]
    assert not missing, (
        f"`{group_name}` group docstring is missing subcommand(s): {missing}. "
        f"Update the docstring 'Commands:' / 'Types:' block in "
        f"src/notebooklm/cli/{group_name}.py."
    )
