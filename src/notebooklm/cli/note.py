"""Note management CLI commands.

Commands:
    list    List all notes
    create  Create a new note
    get     Get note content
    save    Update note content
    rename  Rename a note
    delete  Delete a note
"""

from dataclasses import asdict

import click
from rich.table import Table

from ..client import NotebookLMClient
from ..types import Note
from .helpers import (
    console,
    json_output_response,
    require_notebook,
    resolve_note_id,
    resolve_notebook_id,
    with_client,
)
from .options import json_option, notebook_option


@click.group()
def note():
    """Note management commands.

    \b
    Commands:
      list    List all notes
      create  Create a new note
      get     Get note content
      save    Update note content
      rename  Rename a note
      delete  Delete a note

    \b
    Partial ID Support:
      NOTE_ID arguments support partial matching. Instead of typing the full
      UUID, you can use a prefix (e.g., 'abc' matches 'abc123def456...').
    """
    pass


@note.command("list")
@notebook_option
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@with_client
def note_list(ctx, notebook_id, json_output, client_auth):
    """List all notes in a notebook."""
    nb_id = require_notebook(notebook_id)

    async def _run():
        async with NotebookLMClient(client_auth) as client:
            nb_id_resolved = await resolve_notebook_id(client, nb_id, json_output=json_output)
            notes = await client.notes.list(nb_id_resolved)

            if json_output:
                serialized = [
                    {
                        "id": n.id,
                        "title": n.title or "Untitled",
                        "preview": (n.content or "")[:100],
                    }
                    for n in notes
                    if isinstance(n, Note)
                ]
                json_output_response(
                    {
                        "notebook_id": nb_id_resolved,
                        "notes": serialized,
                        "count": len(serialized),
                    }
                )
                return

            if not notes:
                console.print("[yellow]No notes found[/yellow]")
                return

            table = Table(title=f"Notes in {nb_id_resolved}")
            table.add_column("ID", style="cyan", no_wrap=True)
            table.add_column("Title", style="green")
            table.add_column("Preview", style="dim", max_width=50)

            for n in notes:
                if isinstance(n, Note):
                    preview = n.content[:50] if n.content else ""
                    table.add_row(
                        n.id,
                        n.title or "Untitled",
                        preview + "..." if len(n.content or "") > 50 else preview,
                    )

            console.print(table)

    return _run()


@note.command("create")
@click.argument("content", default="", required=False)
@notebook_option
@click.option("-t", "--title", default="New Note", help="Note title")
@json_option
@with_client
def note_create(ctx, content, notebook_id, title, json_output, client_auth):
    """Create a new note.

    \b
    Examples:
      notebooklm note create                        # Empty note with default title
      notebooklm note create "My note content"     # Note with content
      notebooklm note create "Content" -t "Title"  # Note with title and content
    """
    nb_id = require_notebook(notebook_id)

    async def _run():
        async with NotebookLMClient(client_auth) as client:
            nb_id_resolved = await resolve_notebook_id(client, nb_id, json_output=json_output)
            result = await client.notes.create(nb_id_resolved, title, content)

            # The notes.create RPC returns a nested list whose first element is
            # the new note ID, e.g. ["note_xyz", ["note_xyz", content, ...]].
            # Extract it defensively for the JSON shape.
            new_id: str | None = None
            if isinstance(result, list) and result:
                first = result[0]
                if isinstance(first, str):
                    new_id = first

            if json_output:
                if result and new_id:
                    json_output_response(
                        {
                            "id": new_id,
                            "notebook_id": nb_id_resolved,
                            "title": title,
                            "created": True,
                        }
                    )
                else:
                    json_output_response(
                        {
                            "id": None,
                            "notebook_id": nb_id_resolved,
                            "title": title,
                            "created": False,
                            "error": "Creation may have failed",
                        }
                    )
                return

            if result:
                console.print("[green]Note created[/green]")
                console.print(result)
            else:
                console.print("[yellow]Creation may have failed[/yellow]")

    return _run()


@note.command("get")
@click.argument("note_id")
@notebook_option
@json_option
@with_client
def note_get(ctx, note_id, notebook_id, json_output, client_auth):
    """Get note content.

    NOTE_ID can be a full UUID or a partial prefix (e.g., 'abc' matches 'abc123...').
    """
    nb_id = require_notebook(notebook_id)

    async def _run():
        async with NotebookLMClient(client_auth) as client:
            nb_id_resolved = await resolve_notebook_id(client, nb_id, json_output=json_output)
            resolved_id = await resolve_note_id(
                client, nb_id_resolved, note_id, json_output=json_output
            )
            n = await client.notes.get(nb_id_resolved, resolved_id)

            if json_output:
                if n and isinstance(n, Note):
                    # Mirror the Note dataclass shape; ``json_output_response``
                    # uses ``default=str`` which handles ``datetime`` fields.
                    json_output_response(asdict(n))
                else:
                    json_output_response(
                        {
                            "id": resolved_id,
                            "notebook_id": nb_id_resolved,
                            "found": False,
                            "error": "Note not found",
                        }
                    )
                return

            if n and isinstance(n, Note):
                console.print(f"[bold cyan]ID:[/bold cyan] {n.id}")
                console.print(f"[bold cyan]Title:[/bold cyan] {n.title or 'Untitled'}")
                console.print(f"[bold cyan]Content:[/bold cyan]\n{n.content or ''}")
            else:
                console.print("[yellow]Note not found[/yellow]")

    return _run()


@note.command("save")
@click.argument("note_id")
@notebook_option
@click.option("--title", help="New title")
@click.option("--content", help="New content")
@json_option
@with_client
def note_save(ctx, note_id, notebook_id, title, content, json_output, client_auth):
    """Update note content.

    NOTE_ID can be a full UUID or a partial prefix (e.g., 'abc' matches 'abc123...').
    """
    # Validate up-front so we don't make a network round-trip for a no-op.
    # The early-return must yield a coroutine because ``@with_client`` feeds
    # whatever this function returns into ``asyncio.run`` — returning ``None``
    # here would surface as the misleading "a coroutine was expected, got None"
    # UNEXPECTED_ERROR path that this command silently produced before.
    if not title and not content:

        async def _no_changes():
            if json_output:
                json_output_response(
                    {
                        "id": note_id,
                        "saved": False,
                        "error": "Provide --title and/or --content",
                    }
                )
                return
            console.print("[yellow]Provide --title and/or --content[/yellow]")

        return _no_changes()

    nb_id = require_notebook(notebook_id)

    async def _run():
        async with NotebookLMClient(client_auth) as client:
            nb_id_resolved = await resolve_notebook_id(client, nb_id, json_output=json_output)
            resolved_id = await resolve_note_id(
                client, nb_id_resolved, note_id, json_output=json_output
            )
            await client.notes.update(nb_id_resolved, resolved_id, content=content, title=title)

            if json_output:
                payload: dict = {
                    "id": resolved_id,
                    "notebook_id": nb_id_resolved,
                    "saved": True,
                }
                if title is not None:
                    payload["title"] = title
                if content is not None:
                    payload["content"] = content
                json_output_response(payload)
                return

            console.print(f"[green]Note updated:[/green] {resolved_id}")

    return _run()


@note.command("rename")
@click.argument("note_id")
@click.argument("new_title")
@notebook_option
@json_option
@with_client
def note_rename(ctx, note_id, new_title, notebook_id, json_output, client_auth):
    """Rename a note.

    NOTE_ID can be a full UUID or a partial prefix (e.g., 'abc' matches 'abc123...').
    """
    nb_id = require_notebook(notebook_id)

    async def _run():
        async with NotebookLMClient(client_auth) as client:
            nb_id_resolved = await resolve_notebook_id(client, nb_id, json_output=json_output)
            resolved_id = await resolve_note_id(
                client, nb_id_resolved, note_id, json_output=json_output
            )
            # Get current note to preserve content
            n = await client.notes.get(nb_id_resolved, resolved_id)
            if not n or not isinstance(n, Note):
                if json_output:
                    json_output_response(
                        {
                            "id": resolved_id,
                            "notebook_id": nb_id_resolved,
                            "renamed": False,
                            "error": "Note not found",
                        }
                    )
                    return
                console.print("[yellow]Note not found[/yellow]")
                return

            await client.notes.update(
                nb_id_resolved, resolved_id, content=n.content or "", title=new_title
            )

            if json_output:
                json_output_response(
                    {
                        "id": resolved_id,
                        "notebook_id": nb_id_resolved,
                        "title": new_title,
                        "renamed": True,
                    }
                )
                return

            console.print(f"[green]Note renamed:[/green] {new_title}")

    return _run()


@note.command("delete")
@click.argument("note_id")
@notebook_option
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@json_option
@with_client
def note_delete(ctx, note_id, notebook_id, yes, json_output, client_auth):
    """Delete a note.

    NOTE_ID can be a full UUID or a partial prefix (e.g., 'abc' matches 'abc123...').
    """
    nb_id = require_notebook(notebook_id)

    async def _run():
        async with NotebookLMClient(client_auth) as client:
            nb_id_resolved = await resolve_notebook_id(client, nb_id, json_output=json_output)
            resolved_id = await resolve_note_id(
                client, nb_id_resolved, note_id, json_output=json_output
            )

            if not yes and not click.confirm(f"Delete note {resolved_id}?"):
                if json_output:
                    json_output_response(
                        {
                            "id": resolved_id,
                            "notebook_id": nb_id_resolved,
                            "deleted": False,
                            "error": "Cancelled by user",
                        }
                    )
                return

            await client.notes.delete(nb_id_resolved, resolved_id)

            if json_output:
                json_output_response(
                    {
                        "id": resolved_id,
                        "notebook_id": nb_id_resolved,
                        "deleted": True,
                    }
                )
                return

            console.print(f"[green]Deleted note:[/green] {resolved_id}")

    return _run()
