"""Notebook management CLI commands.

Commands:
    list       List all notebooks
    create     Create a new notebook
    delete     Delete a notebook
    rename     Rename a notebook
    summary    Get notebook summary with AI-generated insights
    metadata   Export notebook metadata with sources list

Note: Sharing commands moved to 'share' command group.
"""

import click
from rich.table import Table

from ..client import NotebookLMClient
from .helpers import (
    clear_context,
    console,
    get_current_notebook,
    json_output_response,
    output_result,
    require_notebook,
    resolve_notebook_id,
    should_confirm,
    with_client,
)
from .options import json_option


def register_notebook_commands(cli):
    """Register notebook commands on the main CLI group."""

    @cli.command("list")
    @json_option
    @with_client
    def list_cmd(ctx, json_output, client_auth):
        """List all notebooks."""

        async def _run():
            async with NotebookLMClient(client_auth) as client:
                notebooks = await client.notebooks.list()

                def render():
                    table = Table(title="Notebooks")
                    table.add_column("ID", style="cyan")
                    table.add_column("Title", style="green")
                    table.add_column("Owner")
                    table.add_column("Created", style="dim")

                    for nb in notebooks:
                        created = nb.created_at.strftime("%Y-%m-%d") if nb.created_at else "-"
                        owner_status = "Owner" if nb.is_owner else "Shared"
                        table.add_row(nb.id, nb.title, owner_status, created)

                    console.print(table)

                output_result(
                    json_output,
                    {
                        "notebooks": [
                            {
                                "index": i,
                                "id": nb.id,
                                "title": nb.title,
                                "is_owner": nb.is_owner,
                                "created_at": nb.created_at.isoformat() if nb.created_at else None,
                            }
                            for i, nb in enumerate(notebooks, 1)
                        ],
                        "count": len(notebooks),
                    },
                    render,
                )

        return _run()

    @cli.command("create")
    @click.argument("title")
    @json_option
    @with_client
    def create_cmd(ctx, title, json_output, client_auth):
        """Create a new notebook."""

        async def _run():
            async with NotebookLMClient(client_auth) as client:
                nb = await client.notebooks.create(title)

                output_result(
                    json_output,
                    {
                        "notebook": {
                            "id": nb.id,
                            "title": nb.title,
                            "created_at": nb.created_at.isoformat() if nb.created_at else None,
                        }
                    },
                    lambda: console.print(f"[green]Created notebook:[/green] {nb.id} - {nb.title}"),
                )

        return _run()

    @cli.command("delete")
    @click.option(
        "-n",
        "--notebook",
        "notebook_id",
        default=None,
        help="Notebook ID (uses current if not set). Supports partial IDs.",
    )
    @click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
    @json_option
    @with_client
    def delete_cmd(ctx, notebook_id, yes, json_output, client_auth):
        """Delete a notebook.

        Supports partial IDs - 'notebooklm delete -n abc' matches 'abc123...'
        """
        notebook_id = require_notebook(notebook_id)

        async def _run():
            async with NotebookLMClient(client_auth) as client:
                # Resolve partial ID to full ID
                resolved_id = await resolve_notebook_id(client, notebook_id)

                # Confirm after resolution so user sees the full ID
                if should_confirm(yes, json_output) and not click.confirm(
                    f"Delete notebook {resolved_id}?"
                ):
                    return

                success = await client.notebooks.delete(resolved_id)

                # Clear context regardless of output mode (side effect must happen)
                context_cleared = False
                if success and get_current_notebook() == resolved_id:
                    clear_context()
                    context_cleared = True

                def render():
                    if success:
                        console.print(f"[green]Deleted notebook:[/green] {resolved_id}")
                        if context_cleared:
                            console.print("[dim]Cleared current notebook context[/dim]")
                    else:
                        console.print("[yellow]Delete may have failed[/yellow]")

                output_result(
                    json_output,
                    {"notebook_id": resolved_id, "deleted": success},
                    render,
                )

        return _run()

    @cli.command("rename")
    @click.argument("new_title")
    @click.option(
        "-n",
        "--notebook",
        "notebook_id",
        default=None,
        help="Notebook ID (uses current if not set). Supports partial IDs.",
    )
    @json_option
    @with_client
    def rename_cmd(ctx, new_title, notebook_id, json_output, client_auth):
        """Rename a notebook.

        NOTEBOOK_ID supports partial matching (e.g., 'abc' matches 'abc123...').
        """
        notebook_id = require_notebook(notebook_id)

        async def _run():
            async with NotebookLMClient(client_auth) as client:
                resolved_id = await resolve_notebook_id(client, notebook_id)
                await client.notebooks.rename(resolved_id, new_title)

                def render():
                    console.print(f"[green]Renamed notebook:[/green] {resolved_id}")
                    console.print(f"[bold]New title:[/bold] {new_title}")

                output_result(
                    json_output,
                    {"notebook_id": resolved_id, "new_title": new_title},
                    render,
                )

        return _run()

    @cli.command("summary")
    @click.option(
        "-n",
        "--notebook",
        "notebook_id",
        default=None,
        help="Notebook ID (uses current if not set). Supports partial IDs.",
    )
    @click.option("--topics", is_flag=True, help="Include suggested topics")
    @json_option
    @with_client
    def summary_cmd(ctx, notebook_id, topics, json_output, client_auth):
        """Get notebook summary with AI-generated insights.

        NOTEBOOK_ID supports partial matching (e.g., 'abc' matches 'abc123...').

        \b
        Examples:
          notebooklm summary              # Summary only
          notebooklm summary --topics     # With suggested topics
        """
        notebook_id = require_notebook(notebook_id)

        async def _run():
            async with NotebookLMClient(client_auth) as client:
                resolved_id = await resolve_notebook_id(client, notebook_id)
                description = await client.notebooks.get_description(resolved_id)

                if json_output:
                    data: dict = {
                        "notebook_id": resolved_id,
                        "summary": description.summary if description else None,
                    }
                    if topics and description and description.suggested_topics:
                        data["suggested_topics"] = [
                            {"index": i, "question": t.question}
                            for i, t in enumerate(description.suggested_topics, 1)
                        ]
                    json_output_response(data)
                    return

                if description and description.summary:
                    console.print("[bold cyan]Summary:[/bold cyan]")
                    console.print(description.summary)

                    if topics and description.suggested_topics:
                        console.print("\n[bold cyan]Suggested Topics:[/bold cyan]")
                        for i, topic in enumerate(description.suggested_topics, 1):
                            console.print(f"  {i}. {topic.question}")
                else:
                    console.print("[yellow]No summary available[/yellow]")

        return _run()

    @cli.command("metadata")
    @click.option(
        "-n",
        "--notebook",
        "notebook_id",
        default=None,
        help="Notebook ID (uses current if not set). Supports partial IDs.",
    )
    @click.option(
        "--json",
        "json_output",
        is_flag=True,
        help="Output as JSON (default: human-readable)",
    )
    @with_client
    def metadata_cmd(ctx, notebook_id, json_output, client_auth):
        """Export notebook metadata with sources list.

        Outputs notebook details (id, title, created_at, is_owner) along with
        a simplified list of sources (type, title, url).

        By default, outputs in human-readable format. Use --json for machine parsing.

        NOTEBOOK_ID supports partial matching (e.g., 'abc' matches 'abc123...').

        \b
        Examples:
          notebooklm metadata              # Human-readable for current notebook
          notebooklm metadata -n abc       # Human-readable for notebook starting with 'abc'
          notebooklm metadata --json       # JSON output
          notebooklm metadata -n abc --json  # JSON for specific notebook
        """
        notebook_id = require_notebook(notebook_id)

        async def _run():
            async with NotebookLMClient(client_auth) as client:
                # Resolve partial ID
                resolved_id = await resolve_notebook_id(client, notebook_id)

                # Get metadata (use notebooks.get_metadata)
                metadata = await client.notebooks.get_metadata(resolved_id)

                if json_output:
                    # JSON output
                    data = metadata.to_dict()
                    json_output_response(data)
                else:
                    # Human-readable output (default)
                    console.print(f"[bold cyan]Notebook:[/bold cyan] {metadata.title}")
                    console.print(f"[dim]ID:[/dim] {metadata.id}")
                    if metadata.created_at:
                        console.print(
                            f"[dim]Created:[/dim] {metadata.created_at.strftime('%Y-%m-%d %H:%M')}"
                        )
                    owner_status = "Owner" if metadata.is_owner else "Shared"
                    console.print(f"[dim]Access:[/dim] {owner_status}")

                    console.print(f"\n[bold]Sources ({len(metadata.sources)}):[/bold]")
                    if not metadata.sources:
                        console.print("[dim]  No sources[/dim]")
                    else:
                        for i, source in enumerate(metadata.sources, 1):
                            source_type = source.kind.value
                            title = source.title or "(untitled)"

                            # Always print the source line (use Text to avoid Rich markup interpretation)
                            from rich.text import Text

                            console.print(
                                Text(f"  {i}. "),
                                Text(f"[{source_type}]", style="default"),
                                Text(f" {title}"),
                            )
                            if source.url:
                                console.print(f"     {source.url}")

        return _run()
