"""Artifacts API for NotebookLM studio content.

Provides operations for generating, listing, downloading, and managing
AI-generated artifacts including Audio Overviews, Video Overviews, Reports,
Quizzes, Flashcards, Infographics, Slide Decks, Data Tables, and Mind Maps.

This module serves as the public facade, delegating to:
- _artifact_generate.py for generation operations
- _artifact_download.py for download operations
"""

import asyncio
import builtins
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from ._artifact_download import ArtifactDownloader
from ._artifact_generate import ArtifactGenerator
from ._core import ClientCore
from .auth import load_httpx_cookies
from .exceptions import ValidationError
from .rpc import (
    ArtifactStatus,
    ArtifactTypeCode,
    AudioFormat,
    AudioLength,
    ExportType,
    InfographicDetail,
    InfographicOrientation,
    InfographicStyle,
    QuizDifficulty,
    QuizQuantity,
    ReportFormat,
    RPCError,
    RPCMethod,
    SlideDeckFormat,
    SlideDeckLength,
    VideoFormat,
    VideoStyle,
    artifact_status_to_str,
)
from .types import (
    Artifact,
    ArtifactDownloadError,
    ArtifactNotReadyError,
    ArtifactType,
    GenerationStatus,
    ReportSuggestion,
    _extract_artifact_url,
)

logger = logging.getLogger(__name__)

# Media artifact types that require URL availability before reporting completion
_MEDIA_ARTIFACT_TYPES = frozenset(
    {
        ArtifactTypeCode.AUDIO.value,
        ArtifactTypeCode.VIDEO.value,
        ArtifactTypeCode.INFOGRAPHIC.value,
        ArtifactTypeCode.SLIDE_DECK.value,
    }
)

if TYPE_CHECKING:
    from ._notes import NotesAPI


class ArtifactsAPI:
    """Operations on NotebookLM artifacts (studio content).

    Artifacts are AI-generated content including Audio Overviews, Video Overviews,
    Reports, Quizzes, Flashcards, Infographics, Slide Decks, Data Tables, and Mind Maps.

    Usage:
        async with NotebookLMClient.from_storage() as client:
            # Generate
            status = await client.artifacts.generate_audio(notebook_id)
            await client.artifacts.wait_for_completion(notebook_id, status.task_id)

            # Download
            await client.artifacts.download_audio(notebook_id, "output.mp4")

            # List and manage
            artifacts = await client.artifacts.list(notebook_id)
            await client.artifacts.rename(notebook_id, artifact_id, "New Title")
    """

    def __init__(
        self,
        core: ClientCore,
        notes_api: "NotesAPI",
        storage_path: Path | None = None,
    ):
        """Initialize the artifacts API.

        Args:
            core: The core client infrastructure.
            notes_api: The notes API for accessing notes/mind maps.
            storage_path: Path to storage state file for loading download cookies.
        """
        self._core = core
        self._notes = notes_api
        self._storage_path = storage_path

        # Initialize sub-components with callback injection
        self._generator = ArtifactGenerator(core, notes_api)
        self._downloader = ArtifactDownloader(
            core=core,
            notes_api=notes_api,
            list_raw_fn=self._list_raw,
            list_quizzes_fn=self.list_quizzes,
            list_flashcards_fn=self.list_flashcards,
            get_artifact_content_fn=lambda notebook_id, artifact_id: self._get_artifact_content(
                notebook_id, artifact_id
            ),
            storage_path=self._storage_path,
        )

    # =========================================================================
    # List/Get Operations
    # =========================================================================

    async def list(
        self, notebook_id: str, artifact_type: ArtifactType | None = None
    ) -> list[Artifact]:
        """List all artifacts in a notebook, including mind maps.

        This returns all AI-generated content: Audio Overviews, Video Overviews,
        Reports, Quizzes, Flashcards, Infographics, Slide Decks, Data Tables,
        and Mind Maps.

        Note: Mind maps are stored in a separate system (notes) but are included
        here since they are AI-generated studio content.

        Args:
            notebook_id: The notebook ID.
            artifact_type: Optional ArtifactType to filter by.
                Use ArtifactType.MIND_MAP to get only mind maps.

        Returns:
            List of Artifact objects.
        """
        logger.debug("Listing artifacts in notebook %s", notebook_id)
        artifacts: list[Artifact] = []

        # Fetch studio artifacts (audio, video, reports, etc.)
        params = [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']
        result = await self._core.rpc_call(
            RPCMethod.LIST_ARTIFACTS,
            params,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )

        artifacts_data: list[Any] = []
        if result and isinstance(result, list) and len(result) > 0:
            artifacts_data = result[0] if isinstance(result[0], list) else result

        for art_data in artifacts_data:
            if isinstance(art_data, list) and len(art_data) > 0:
                artifact = Artifact.from_api_response(art_data)
                if artifact_type is None or artifact.kind == artifact_type:
                    artifacts.append(artifact)

        # Fetch mind maps from notes system (if not filtering to non-mind-map type)
        if artifact_type is None or artifact_type == ArtifactType.MIND_MAP:
            try:
                mind_maps = await self._notes.list_mind_maps(notebook_id)
                for mm_data in mind_maps:
                    mind_map_artifact = Artifact.from_mind_map(mm_data)
                    if mind_map_artifact is not None:  # None means deleted (status=2)
                        if artifact_type is None or mind_map_artifact.kind == artifact_type:
                            artifacts.append(mind_map_artifact)
            except (RPCError, httpx.HTTPError) as e:
                # Network/API errors - log and continue with studio artifacts
                logger.warning("Failed to fetch mind maps: %s", e)

        return artifacts

    async def get(self, notebook_id: str, artifact_id: str) -> Artifact | None:
        """Get a specific artifact by ID.

        Args:
            notebook_id: The notebook ID.
            artifact_id: The artifact ID.

        Returns:
            Artifact object, or None if not found.
        """
        logger.debug("Getting artifact %s from notebook %s", artifact_id, notebook_id)
        artifacts = await self.list(notebook_id)
        for artifact in artifacts:
            if artifact.id == artifact_id:
                return artifact
        return None

    async def list_audio(self, notebook_id: str) -> builtins.list[Artifact]:
        """List audio overview artifacts."""
        return await self.list(notebook_id, ArtifactType.AUDIO)

    async def list_video(self, notebook_id: str) -> builtins.list[Artifact]:
        """List video overview artifacts."""
        return await self.list(notebook_id, ArtifactType.VIDEO)

    async def list_reports(self, notebook_id: str) -> builtins.list[Artifact]:
        """List report artifacts (Briefing Doc, Study Guide, Blog Post)."""
        return await self.list(notebook_id, ArtifactType.REPORT)

    async def list_quizzes(self, notebook_id: str) -> builtins.list[Artifact]:
        """List quiz artifacts."""
        return await self.list(notebook_id, ArtifactType.QUIZ)

    async def list_flashcards(self, notebook_id: str) -> builtins.list[Artifact]:
        """List flashcard artifacts."""
        return await self.list(notebook_id, ArtifactType.FLASHCARDS)

    async def list_infographics(self, notebook_id: str) -> builtins.list[Artifact]:
        """List infographic artifacts."""
        return await self.list(notebook_id, ArtifactType.INFOGRAPHIC)

    async def list_slide_decks(self, notebook_id: str) -> builtins.list[Artifact]:
        """List slide deck artifacts."""
        return await self.list(notebook_id, ArtifactType.SLIDE_DECK)

    async def list_data_tables(self, notebook_id: str) -> builtins.list[Artifact]:
        """List data table artifacts."""
        return await self.list(notebook_id, ArtifactType.DATA_TABLE)

    # =========================================================================
    # Generate Operations (delegated to ArtifactGenerator)
    # =========================================================================

    async def generate_audio(
        self,
        notebook_id: str,
        source_ids: builtins.list[str] | None = None,
        language: str = "en",
        instructions: str | None = None,
        audio_format: AudioFormat | None = None,
        audio_length: AudioLength | None = None,
    ) -> GenerationStatus:
        """Generate an Audio Overview (podcast).

        Args:
            notebook_id: The notebook ID.
            source_ids: Source IDs to include. If None, uses all sources.
            language: Language code (default: "en").
            instructions: Custom instructions for the podcast hosts.
            audio_format: DEEP_DIVE, BRIEF, CRITIQUE, or DEBATE.
            audio_length: SHORT, DEFAULT, or LONG.

        Returns:
            GenerationStatus with task_id for polling.
        """
        return await self._generator.generate_audio(
            notebook_id, source_ids, language, instructions, audio_format, audio_length
        )

    async def generate_video(
        self,
        notebook_id: str,
        source_ids: builtins.list[str] | None = None,
        language: str = "en",
        instructions: str | None = None,
        video_format: VideoFormat | None = None,
        video_style: VideoStyle | None = None,
    ) -> GenerationStatus:
        """Generate a Video Overview.

        Args:
            notebook_id: The notebook ID.
            source_ids: Source IDs to include. If None, uses all sources.
            language: Language code (default: "en").
            instructions: Custom instructions for video generation.
            video_format: EXPLAINER or BRIEF.
            video_style: AUTO_SELECT, CLASSIC, WHITEBOARD, etc.

        Returns:
            GenerationStatus with task_id for polling.
        """
        return await self._generator.generate_video(
            notebook_id, source_ids, language, instructions, video_format, video_style
        )

    async def generate_cinematic_video(
        self,
        notebook_id: str,
        source_ids: builtins.list[str] | None = None,
        language: str = "en",
        instructions: str | None = None,
    ) -> GenerationStatus:
        """Generate a Cinematic Video Overview.

        Cinematic videos use AI-generated documentary-style footage (Veo 3)
        instead of the slide-deck animations used by standard video overviews.
        They do not accept VideoStyle options.

        Requires a Google AI Ultra subscription. Uses the same CREATE_ARTIFACT
        RPC as standard videos with VideoFormat.CINEMATIC (3). Parameter
        structure verified against NotebookLM web UI network traffic
        (March 2026).

        Note: Generation takes significantly longer than standard videos
        (~30-40 minutes) due to Veo 3 rendering.

        Args:
            notebook_id: The notebook ID.
            source_ids: Source IDs to include. If None, uses all sources.
            language: Language code (default: "en").
            instructions: Custom instructions for video generation.

        Returns:
            GenerationStatus with task_id for polling.
        """
        if source_ids is None:
            source_ids = await self._core.get_source_ids(notebook_id)

        source_ids_triple = [[[sid]] for sid in source_ids] if source_ids else []
        source_ids_double = [[sid] for sid in source_ids] if source_ids else []

        params = [
            [2],
            notebook_id,
            [
                None,
                None,
                ArtifactTypeCode.VIDEO.value,
                source_ids_triple,
                None,
                None,
                None,
                None,
                [
                    None,
                    None,
                    [
                        source_ids_double,
                        language,
                        instructions,
                        None,
                        VideoFormat.CINEMATIC.value,
                    ],
                ],
            ],
        ]
        return await self._call_generate(notebook_id, params)

    async def generate_report(
        self,
        notebook_id: str,
        report_format: ReportFormat = ReportFormat.BRIEFING_DOC,
        source_ids: builtins.list[str] | None = None,
        language: str = "en",
        custom_prompt: str | None = None,
        extra_instructions: str | None = None,
    ) -> GenerationStatus:
        """Generate a report artifact.

        Args:
            notebook_id: The notebook ID.
            report_format: BRIEFING_DOC, STUDY_GUIDE, BLOG_POST, or CUSTOM.
            source_ids: Source IDs to include. If None, uses all sources.
            language: Language code (default: "en").
            custom_prompt: Prompt for CUSTOM format. Falls back to a generic
                default if None.
            extra_instructions: Additional instructions appended to the built-in
                template prompt. Ignored when report_format is CUSTOM; for custom
                reports, embed all instructions in custom_prompt instead.

        Returns:
            GenerationStatus with task_id for polling.
        """
        if source_ids is None:
            source_ids = await self._core.get_source_ids(notebook_id)

        format_configs = {
            ReportFormat.BRIEFING_DOC: {
                "title": "Briefing Doc",
                "description": "Key insights and important quotes",
                "prompt": (
                    "Create a comprehensive briefing document that includes an "
                    "Executive Summary, detailed analysis of key themes, important "
                    "quotes with context, and actionable insights."
                ),
            },
            ReportFormat.STUDY_GUIDE: {
                "title": "Study Guide",
                "description": "Short-answer quiz, essay questions, glossary",
                "prompt": (
                    "Create a comprehensive study guide that includes key concepts, "
                    "short-answer practice questions, essay prompts for deeper "
                    "exploration, and a glossary of important terms."
                ),
            },
            ReportFormat.BLOG_POST: {
                "title": "Blog Post",
                "description": "Insightful takeaways in readable article format",
                "prompt": (
                    "Write an engaging blog post that presents the key insights "
                    "in an accessible, reader-friendly format. Include an attention-"
                    "grabbing introduction, well-organized sections, and a compelling "
                    "conclusion with takeaways."
                ),
            },
            ReportFormat.CUSTOM: {
                "title": "Custom Report",
                "description": "Custom format",
                "prompt": custom_prompt or "Create a report based on the provided sources.",
            },
        }

        config = format_configs[report_format]
        if extra_instructions and report_format != ReportFormat.CUSTOM:
            config = {**config, "prompt": f"{config['prompt']}\n\n{extra_instructions}"}
        source_ids_triple = [[[sid]] for sid in source_ids] if source_ids else []
        source_ids_double = [[sid] for sid in source_ids] if source_ids else []

        params = [
            [2],
            notebook_id,
            [
                None,
                None,
                2,  # ArtifactTypeCode.REPORT
                source_ids_triple,
                None,
                None,
                None,
                [
                    None,
                    [
                        config["title"],
                        config["description"],
                        None,
                        source_ids_double,
                        language,
                        config["prompt"],
                        None,
                        True,
                    ],
                ],
            ],
        ]
        return await self._call_generate(notebook_id, params)

    async def generate_study_guide(
        self,
        notebook_id: str,
        source_ids: builtins.list[str] | None = None,
        language: str = "en",
        extra_instructions: str | None = None,
    ) -> GenerationStatus:
        """Generate a study guide report.

        Convenience method wrapping generate_report with STUDY_GUIDE format.

        Args:
            notebook_id: The notebook ID.
            source_ids: Source IDs to include. If None, uses all sources.
            language: Language code (default: "en").
            extra_instructions: Additional instructions appended to the default template.

        Returns:
            GenerationStatus with task_id for polling.
        """
        return await self.generate_report(
            notebook_id,
            report_format=ReportFormat.STUDY_GUIDE,
            source_ids=source_ids,
            language=language,
            extra_instructions=extra_instructions,
        )

    async def generate_quiz(
        self,
        notebook_id: str,
        source_ids: builtins.list[str] | None = None,
        instructions: str | None = None,
        quantity: QuizQuantity | None = None,
        difficulty: QuizDifficulty | None = None,
    ) -> GenerationStatus:
        """Generate a quiz.

        Args:
            notebook_id: The notebook ID.
            source_ids: Source IDs to include. If None, uses all sources.
            instructions: Custom instructions for quiz generation.
            quantity: FEWER, STANDARD, or MORE questions.
            difficulty: EASY, MEDIUM, or HARD.

        Returns:
            GenerationStatus with task_id for polling.
        """
        return await self._generator.generate_quiz(
            notebook_id, source_ids, instructions, quantity, difficulty
        )

    async def generate_flashcards(
        self,
        notebook_id: str,
        source_ids: builtins.list[str] | None = None,
        instructions: str | None = None,
        quantity: QuizQuantity | None = None,
        difficulty: QuizDifficulty | None = None,
    ) -> GenerationStatus:
        """Generate flashcards.

        Args:
            notebook_id: The notebook ID.
            source_ids: Source IDs to include. If None, uses all sources.
            instructions: Custom instructions for flashcard generation.
            quantity: FEWER, STANDARD, or MORE cards.
            difficulty: EASY, MEDIUM, or HARD.

        Returns:
            GenerationStatus with task_id for polling.
        """
        return await self._generator.generate_flashcards(
            notebook_id, source_ids, instructions, quantity, difficulty
        )

    async def generate_infographic(
        self,
        notebook_id: str,
        source_ids: builtins.list[str] | None = None,
        language: str = "en",
        instructions: str | None = None,
        orientation: InfographicOrientation | None = None,
        detail_level: InfographicDetail | None = None,
        style: InfographicStyle | None = None,
    ) -> GenerationStatus:
        """Generate an infographic.

        Args:
            notebook_id: The notebook ID.
            source_ids: Source IDs to include. If None, uses all sources.
            language: Language code (default: "en").
            instructions: Custom instructions for infographic generation.
            orientation: LANDSCAPE, PORTRAIT, or SQUARE.
            detail_level: CONCISE, STANDARD, or DETAILED.
            style: Visual style preset for the infographic.

        Returns:
            GenerationStatus with task_id for polling.
        """
        if source_ids is None:
            source_ids = await self._core.get_source_ids(notebook_id)

        source_ids_triple = [[[sid]] for sid in source_ids] if source_ids else []
        orientation_code = orientation.value if orientation else None
        detail_code = detail_level.value if detail_level else None
        style_code = style.value if style else None

        params = [
            [2],
            notebook_id,
            [
                None,
                None,
                7,  # ArtifactTypeCode.INFOGRAPHIC
                source_ids_triple,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                [[instructions, language, None, orientation_code, detail_code, style_code]],
            ],
        ]
        return await self._call_generate(notebook_id, params)

    async def generate_slide_deck(
        self,
        notebook_id: str,
        source_ids: builtins.list[str] | None = None,
        language: str = "en",
        instructions: str | None = None,
        slide_format: SlideDeckFormat | None = None,
        slide_length: SlideDeckLength | None = None,
    ) -> GenerationStatus:
        """Generate a slide deck.

        Args:
            notebook_id: The notebook ID.
            source_ids: Source IDs to include. If None, uses all sources.
            language: Language code (default: "en").
            instructions: Custom instructions for slide deck generation.
            slide_format: DETAILED_DECK or PRESENTER_SLIDES.
            slide_length: DEFAULT or SHORT.

        Returns:
            GenerationStatus with task_id for polling.
        """
        return await self._generator.generate_slide_deck(
            notebook_id, source_ids, language, instructions, slide_format, slide_length
        )

    async def revise_slide(
        self,
        notebook_id: str,
        artifact_id: str,
        slide_index: int,
        prompt: str,
    ) -> GenerationStatus:
        """Revise an individual slide in a completed slide deck using a prompt.

        The slide deck must already be generated (status=COMPLETED) before
        calling this method. Use poll_status() to wait for the revision to complete.

        Args:
            notebook_id: The notebook ID.
            artifact_id: The slide deck artifact ID to revise.
            slide_index: Zero-based index of the slide to revise.
            prompt: Natural language instruction for the revision
                    (e.g. "Move the title up", "Remove taxonomy section").

        Returns:
            GenerationStatus with task_id for polling.
        """
        if slide_index < 0:
            raise ValidationError(f"slide_index must be >= 0, got {slide_index}")

        params = [
            [2],
            artifact_id,
            [[[slide_index, prompt]]],
        ]
        try:
            result = await self._core.rpc_call(
                RPCMethod.REVISE_SLIDE,
                params,
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            if result is None:
                logger.warning("REVISE_SLIDE returned null result for artifact %s", artifact_id)
            return self._parse_generation_result(result)
        except RPCError as e:
            if e.rpc_code == "USER_DISPLAYABLE_ERROR":
                return GenerationStatus(
                    task_id="",
                    status="failed",
                    error=str(e),
                    error_code=str(e.rpc_code) if e.rpc_code is not None else None,
                )
            raise

    async def generate_data_table(
        self,
        notebook_id: str,
        source_ids: builtins.list[str] | None = None,
        language: str = "en",
        instructions: str | None = None,
    ) -> GenerationStatus:
        """Generate a data table.

        Args:
            notebook_id: The notebook ID.
            source_ids: Source IDs to include. If None, uses all sources.
            language: Language code (default: "en").
            instructions: Description of desired table structure.

        Returns:
            GenerationStatus with task_id for polling.
        """
        return await self._generator.generate_data_table(
            notebook_id, source_ids, language, instructions
        )

    async def generate_mind_map(
        self,
        notebook_id: str,
        source_ids: builtins.list[str] | None = None,
        language: str = "en",
        instructions: str | None = None,
    ) -> dict[str, Any]:
        """Generate an interactive mind map.

        The mind map is generated and saved as a note in the notebook.
        It will appear in artifact listings with type MIND_MAP (5).

        Args:
            notebook_id: The notebook ID.
            source_ids: Source IDs to include. If None, uses all sources.
            language: Language code (default: "en").
            instructions: Custom instructions for the mind map.

        Returns:
            Dictionary with 'mind_map' (JSON data) and 'note_id'.
        """
        import json as json_module

        if source_ids is None:
            source_ids = await self._core.get_source_ids(notebook_id)

        source_ids_nested = [[[sid]] for sid in source_ids] if source_ids else []

        params = [
            source_ids_nested,
            None,
            None,
            None,
            None,
            ["interactive_mindmap", [["[CONTEXT]", instructions or ""]], language],
            None,
            [2, None, [1]],
        ]

        result = await self._core.rpc_call(
            RPCMethod.GENERATE_MIND_MAP,
            params,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )

        if result and isinstance(result, list) and len(result) > 0:
            inner = result[0]
            if isinstance(inner, list) and len(inner) > 0:
                mind_map_json = inner[0]

                # Parse the mind map JSON
                if isinstance(mind_map_json, str):
                    try:
                        mind_map_data = json_module.loads(mind_map_json)
                    except json_module.JSONDecodeError:
                        mind_map_data = mind_map_json
                        mind_map_json = str(mind_map_json)
                else:
                    mind_map_data = mind_map_json
                    mind_map_json = json_module.dumps(mind_map_json)

                # Extract title from mind map data
                title = "Mind Map"
                if isinstance(mind_map_data, dict) and "name" in mind_map_data:
                    title = mind_map_data["name"]

                # The GENERATE_MIND_MAP RPC generates content but does NOT persist it.
                # We must explicitly create a note to save the mind map.
                note = await self._notes.create(notebook_id, title=title, content=mind_map_json)
                note_id = note.id if note else None

                return {
                    "mind_map": mind_map_data,
                    "note_id": note_id,
                }

        return {"mind_map": None, "note_id": None}

    # =========================================================================
    # Download Operations (delegated to ArtifactDownloader)
    # =========================================================================

    async def download_audio(
        self, notebook_id: str, output_path: str, artifact_id: str | None = None
    ) -> str:
        """Download an Audio Overview to a file.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the audio file (MP4/MP3).
            artifact_id: Specific artifact ID, or uses first completed audio.

        Returns:
            The output path.
        """
        return await self._downloader.download_audio(notebook_id, output_path, artifact_id)

    async def download_video(
        self, notebook_id: str, output_path: str, artifact_id: str | None = None
    ) -> str:
        """Download a Video Overview to a file.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the video file (MP4).
            artifact_id: Specific artifact ID, or uses first completed video.

        Returns:
            The output path.
        """
        return await self._downloader.download_video(notebook_id, output_path, artifact_id)

    async def download_infographic(
        self, notebook_id: str, output_path: str, artifact_id: str | None = None
    ) -> str:
        """Download an Infographic to a file.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the image file (PNG).
            artifact_id: Specific artifact ID, or uses first completed infographic.

        Returns:
            The output path.
        """
        return await self._downloader.download_infographic(notebook_id, output_path, artifact_id)

    async def download_slide_deck(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "pdf",
    ) -> str:
        """Download a slide deck as PDF or PPTX.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the file.
            artifact_id: Specific artifact ID, or uses first completed slide deck.
            output_format: Download format: "pdf" (default) or "pptx".

        Returns:
            The output path.
        """
        return await self._downloader.download_slide_deck(
            notebook_id, output_path, artifact_id, output_format
        )

    async def _get_artifact_content(self, notebook_id: str, artifact_id: str) -> str | None:
        """Fetch artifact HTML content for quiz/flashcard types."""
        result = await self._core.rpc_call(
            RPCMethod.GET_INTERACTIVE_HTML,
            [artifact_id],
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        # Response is wrapped: result[0] contains the artifact data.
        if result and isinstance(result, list) and len(result) > 0:
            data = result[0]
            if isinstance(data, list) and len(data) > 9 and data[9]:
                return data[9][0]
        return None

    async def download_report(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
    ) -> str:
        """Download a report artifact as markdown.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the markdown file.
            artifact_id: Specific artifact ID, or uses first completed report.

        Returns:
            The output path where the file was saved.
        """
        return await self._downloader.download_report(notebook_id, output_path, artifact_id)

    async def download_mind_map(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
    ) -> str:
        """Download a mind map as JSON.

        Mind maps are stored in the notes system, not the regular artifacts list.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the JSON file.
            artifact_id: Specific mind map ID (note ID), or uses first available.

        Returns:
            The output path where the file was saved.
        """
        return await self._downloader.download_mind_map(notebook_id, output_path, artifact_id)

    async def download_data_table(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
    ) -> str:
        """Download a data table as CSV.

        Args:
            notebook_id: The notebook ID.
            output_path: Path to save the CSV file.
            artifact_id: Specific artifact ID, or uses first completed data table.

        Returns:
            The output path where the file was saved.
        """
        return await self._downloader.download_data_table(notebook_id, output_path, artifact_id)

    async def download_quiz(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "json",
    ) -> str:
        """Download quiz questions.

        Args:
            notebook_id: Notebook ID.
            output_path: Output file path.
            artifact_id: Specific quiz artifact ID (optional).
            output_format: Output format - json, markdown, or html.

        Returns:
            Path to downloaded file.

        Raises:
            ValueError: If no completed quiz artifact found.
        """
        return await self._downloader.download_quiz(
            notebook_id, output_path, artifact_id, output_format
        )

    async def download_flashcards(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "json",
    ) -> str:
        """Download flashcard deck.

        Args:
            notebook_id: Notebook ID.
            output_path: Output file path.
            artifact_id: Specific flashcard artifact ID (optional).
            output_format: Output format - json, markdown, or html.

        Returns:
            Path to downloaded file.

        Raises:
            ValueError: If no completed flashcard artifact found.
        """
        return await self._downloader.download_flashcards(
            notebook_id, output_path, artifact_id, output_format
        )

    # =========================================================================
    # Management Operations
    # =========================================================================

    async def delete(self, notebook_id: str, artifact_id: str) -> bool:
        """Delete an artifact.

        Args:
            notebook_id: The notebook ID.
            artifact_id: The artifact ID to delete.

        Returns:
            True if deletion succeeded.
        """
        logger.debug("Deleting artifact %s from notebook %s", artifact_id, notebook_id)
        params = [[2], artifact_id]
        await self._core.rpc_call(
            RPCMethod.DELETE_ARTIFACT,
            params,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        return True

    async def rename(self, notebook_id: str, artifact_id: str, new_title: str) -> None:
        """Rename an artifact.

        Args:
            notebook_id: The notebook ID.
            artifact_id: The artifact ID to rename.
            new_title: The new title.
        """
        params = [[artifact_id, new_title], [["title"]]]
        await self._core.rpc_call(
            RPCMethod.RENAME_ARTIFACT,
            params,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )

    # =========================================================================
    # Polling Operations
    # =========================================================================

    async def poll_status(self, notebook_id: str, task_id: str) -> GenerationStatus:
        """Poll the status of a generation task.

        Args:
            notebook_id: The notebook ID.
            task_id: The task/artifact ID to check.

        Returns:
            GenerationStatus with current status.  When the artifact is not
            found in the list, ``status`` is set to ``"not_found"`` so that
            callers can distinguish "genuinely pending" from "removed by the
            server" (e.g. after a quota rejection).

        .. versionchanged:: 0.4.0
            **Breaking change:** Previously returned ``status="pending"``
            when an artifact was absent from the list.  Now returns
            ``status="not_found"`` to allow callers to distinguish a
            genuinely pending artifact from one that was removed.
        """
        # List all artifacts and find by ID (no poll-by-ID RPC exists)
        artifacts_data = await self._list_raw(notebook_id)
        for art in artifacts_data:
            if len(art) > 0 and art[0] == task_id:
                status_code = art[4] if len(art) > 4 else 0
                artifact_type = art[2] if len(art) > 2 else 0

                # For media artifacts, verify URL availability before reporting completion.
                # The API may set status=COMPLETED before media URLs are populated.
                if status_code == ArtifactStatus.COMPLETED:
                    if not self._is_media_ready(art, artifact_type):
                        type_name = self._get_artifact_type_name(artifact_type)
                        logger.debug(
                            "Artifact %s (type=%s) status=COMPLETED but media not ready, "
                            "continuing poll",
                            task_id,
                            type_name,
                        )
                        # Downgrade to PROCESSING to continue polling
                        status_code = ArtifactStatus.PROCESSING

                status = artifact_status_to_str(status_code)

                # Extract error details from failed artifacts.
                # The API may embed an error reason string at art[3] when
                # the artifact fails (e.g. daily quota exceeded).
                error_msg: str | None = None
                if status == "failed":
                    error_msg = self._extract_artifact_error(art)
                url = _extract_artifact_url(art, artifact_type)

                return GenerationStatus(
                    task_id=task_id,
                    status=status,
                    url=url,
                    error=error_msg,
                )

        # Artifact not found in the list.  Use a distinct status so
        # wait_for_completion can differentiate from genuine "pending".
        return GenerationStatus(task_id=task_id, status="not_found")

    async def wait_for_completion(
        self,
        notebook_id: str,
        task_id: str,
        initial_interval: float = 2.0,
        max_interval: float = 10.0,
        timeout: float = 300.0,
        poll_interval: float | None = None,  # Deprecated, use initial_interval
        max_not_found: int = 5,
        min_not_found_window: float = 10.0,
    ) -> GenerationStatus:
        """Wait for a generation task to complete.

        Uses exponential backoff for polling to reduce API load.

        Args:
            notebook_id: The notebook ID.
            task_id: The task/artifact ID to wait for.
            initial_interval: Initial seconds between status checks.
            max_interval: Maximum seconds between status checks.
            timeout: Maximum seconds to wait.
            poll_interval: Deprecated. Use initial_interval instead.
            max_not_found: Consecutive "not found" polls before treating
                the task as failed.  When the API removes an artifact
                from the list (e.g. after a daily-quota rejection), the
                poller would otherwise spin until *timeout*.  Defaults
                to 5 to tolerate brief replication lag and slow networks.
            min_not_found_window: Minimum seconds that must have elapsed
                since the *first* not-found response before a consecutive
                run triggers failure.  This avoids false positives on
                slow or unreliable networks.  Defaults to 10.0.

        Returns:
            Final GenerationStatus.

        Raises:
            TimeoutError: If task doesn't complete within timeout.
        """
        # Backward compatibility: poll_interval overrides initial_interval.
        if poll_interval is not None:
            import warnings

            warnings.warn(
                "poll_interval is deprecated, use initial_interval instead",
                DeprecationWarning,
                stacklevel=2,
            )
            initial_interval = poll_interval

        start_time = asyncio.get_running_loop().time()
        current_interval = initial_interval
        consecutive_not_found = 0
        total_not_found = 0
        first_not_found_time: float | None = None
        last_status: str | None = None

        while True:
            status = await self.poll_status(notebook_id, task_id)
            last_status = status.status

            if status.is_complete or status.is_failed:
                return status

            # Track consecutive and total "not found" responses.  The API
            # may remove quota-rejected artifacts from the list entirely
            # instead of setting them to FAILED.  We track both a
            # consecutive run *and* a total count to handle "flickering"
            # artifacts that alternate between found/not-found due to API
            # replication lag.
            if status.status == "not_found":
                consecutive_not_found += 1
                total_not_found += 1
                now = asyncio.get_running_loop().time()
                if first_not_found_time is None:
                    first_not_found_time = now
                not_found_elapsed = now - first_not_found_time

                # Trigger failure when consecutive threshold is met AND
                # enough wall-clock time has passed (avoids false positives
                # on fast networks), OR when total not-found count is high
                # enough to indicate flickering artifacts.
                consecutive_trigger = (
                    consecutive_not_found >= max_not_found
                    and not_found_elapsed >= min_not_found_window
                )
                total_trigger = total_not_found >= max_not_found * 2

                if consecutive_trigger or total_trigger:
                    trigger = (
                        f"consecutive={consecutive_not_found}"
                        if consecutive_trigger
                        else f"total={total_not_found}"
                    )
                    logger.warning(
                        "Artifact %s disappeared from list (%s not-found polls, "
                        "%s) — treating as failed",
                        task_id,
                        trigger,
                        f"elapsed={not_found_elapsed:.1f}s",
                    )
                    return GenerationStatus(
                        task_id=task_id,
                        status="failed",
                        error=(
                            "Generation failed: artifact was removed by the server. "
                            "This may indicate a daily quota/rate limit was exceeded, "
                            "an invalid notebook ID, or a transient API issue. "
                            "Try again later."
                        ),
                    )
            else:
                consecutive_not_found = 0

            elapsed = asyncio.get_running_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(
                    f"Task {task_id} timed out after {timeout}s (last status: {last_status})"
                )

            # Clamp sleep duration to respect timeout
            remaining_time = timeout - elapsed
            sleep_duration = min(current_interval, remaining_time)
            if sleep_duration > 0:
                await asyncio.sleep(sleep_duration)

            # Exponential backoff: double the interval up to max_interval
            current_interval = min(current_interval * 2, max_interval)

    # =========================================================================
    # Export Operations
    # =========================================================================

    async def export_report(
        self,
        notebook_id: str,
        artifact_id: str,
        title: str = "Export",
        export_type: ExportType = ExportType.DOCS,
    ) -> Any:
        """Export a report to Google Docs.

        Args:
            notebook_id: The notebook ID.
            artifact_id: The report artifact ID.
            title: Title for the exported document.
            export_type: ExportType.DOCS (default) or ExportType.SHEETS.

        Returns:
            Export result with document URL.
        """
        params = [None, artifact_id, None, title, int(export_type)]
        return await self._core.rpc_call(
            RPCMethod.EXPORT_ARTIFACT,
            params,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )

    async def export_data_table(
        self,
        notebook_id: str,
        artifact_id: str,
        title: str = "Export",
    ) -> Any:
        """Export a data table to Google Sheets.

        Args:
            notebook_id: The notebook ID.
            artifact_id: The data table artifact ID.
            title: Title for the exported spreadsheet.

        Returns:
            Export result with spreadsheet URL.
        """
        params = [None, artifact_id, None, title, int(ExportType.SHEETS)]
        return await self._core.rpc_call(
            RPCMethod.EXPORT_ARTIFACT,
            params,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )

    async def export(
        self,
        notebook_id: str,
        artifact_id: str | None = None,
        content: str | None = None,
        title: str = "Export",
        export_type: ExportType = ExportType.DOCS,
    ) -> Any:
        """Export an artifact to Google Docs/Sheets.

        Generic export method for any artifact type.

        Args:
            notebook_id: The notebook ID.
            artifact_id: The artifact ID (optional).
            content: Content to export (optional).
            title: Title for the exported document.
            export_type: ExportType.DOCS (default) or ExportType.SHEETS.

        Returns:
            Export result with document URL.
        """
        params = [None, artifact_id, content, title, int(export_type)]
        return await self._core.rpc_call(
            RPCMethod.EXPORT_ARTIFACT,
            params,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )

    # =========================================================================
    # Suggestions
    # =========================================================================

    async def suggest_reports(
        self,
        notebook_id: str,
    ) -> builtins.list[ReportSuggestion]:
        """Get AI-suggested report formats for a notebook.

        Args:
            notebook_id: The notebook ID.

        Returns:
            List of ReportSuggestion objects.
        """
        params = [[2], notebook_id]

        result = await self._core.rpc_call(
            RPCMethod.GET_SUGGESTED_REPORTS,
            params,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )

        suggestions = []
        # Response format: [[[title, description, null, null, prompt, audience_level], ...]]
        if result and isinstance(result, list) and len(result) > 0:
            items = result[0] if isinstance(result[0], list) else result
            for item in items:
                if isinstance(item, list) and len(item) >= 5:
                    suggestions.append(
                        ReportSuggestion(
                            title=item[0] if isinstance(item[0], str) else "",
                            description=item[1] if isinstance(item[1], str) else "",
                            prompt=item[4] if isinstance(item[4], str) else "",
                            audience_level=item[5] if len(item) > 5 else 2,
                        )
                    )

        return suggestions

    # =========================================================================
    # Private Helpers
    # =========================================================================

    async def _list_raw(self, notebook_id: str) -> builtins.list[Any]:
        """Get raw artifact list data."""
        params = [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"']
        result = await self._core.rpc_call(
            RPCMethod.LIST_ARTIFACTS,
            params,
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        if result and isinstance(result, list) and len(result) > 0:
            return result[0] if isinstance(result[0], list) else result
        return []

    def _select_artifact(
        self,
        candidates: builtins.list[Any],
        artifact_id: str | None,
        type_name: str,
        type_name_lower: str,
    ) -> Any:
        """Select an artifact from candidates by ID or return first available.

        Args:
            candidates: List of candidate artifacts.
            artifact_id: Specific artifact ID to select, or None for first.
            type_name: Display name for error messages (e.g., "Report").
            type_name_lower: Lowercase name for error messages (e.g., "report").

        Returns:
            Selected artifact data.

        Raises:
            ValueError: If artifact not found or no candidates available.
        """
        if artifact_id:
            artifact = next((a for a in candidates if a[0] == artifact_id), None)
            if not artifact:
                raise ArtifactNotReadyError(
                    type_name.lower().replace(" ", "_"), artifact_id=artifact_id
                )
            return artifact

        if not candidates:
            raise ArtifactNotReadyError(type_name_lower)

        # Sort by creation timestamp (descending) to get the latest.
        # Timestamp is at index 15, position 0.
        candidates.sort(
            key=lambda a: a[15][0] if len(a) > 15 and isinstance(a[15], list) and a[15] else 0,
            reverse=True,
        )

        return candidates[0]

    async def _download_urls_batch(
        self, urls_and_paths: builtins.list[tuple[str, str]]
    ) -> builtins.list[str]:
        """Download multiple files using httpx with proper cookie handling.

        Args:
            urls_and_paths: List of (url, output_path) tuples.

        Returns:
            List of successfully downloaded output paths.
        """
        downloaded: list[str] = []

        # Load cookies with domain info for cross-domain redirect handling
        cookies = load_httpx_cookies(path=self._storage_path)

        async with httpx.AsyncClient(
            cookies=cookies,
            follow_redirects=True,
            timeout=60.0,
        ) as client:
            for url, output_path in urls_and_paths:
                try:
                    # Validate URL scheme and domain before sending auth cookies
                    parsed = urlparse(url)
                    if parsed.scheme != "https":
                        raise ArtifactDownloadError(
                            "media", details=f"Download URL must use HTTPS: {url[:80]}"
                        )
                    trusted = (".google.com", ".googleusercontent.com", ".googleapis.com")
                    if not any(
                        parsed.netloc == d.lstrip(".") or parsed.netloc.endswith(d) for d in trusted
                    ):
                        raise ArtifactDownloadError(
                            "media", details=f"Untrusted download domain: {parsed.netloc}"
                        )

                    response = await client.get(url)
                    response.raise_for_status()

                    content_type = response.headers.get("content-type", "")
                    if "text/html" in content_type:
                        raise ArtifactDownloadError(
                            "media", details="Received HTML instead of media file"
                        )

                    output_file = Path(output_path)
                    output_file.parent.mkdir(parents=True, exist_ok=True)
                    output_file.write_bytes(response.content)
                    downloaded.append(output_path)
                    logger.debug("Downloaded %s (%d bytes)", url[:60], len(response.content))

                except (httpx.HTTPError, ValueError) as e:
                    logger.warning("Download failed for %s: %s", url[:60], e)

        return downloaded

    async def _download_url(self, url: str, output_path: str) -> str:
        """Download a file from URL using streaming with proper cookie handling.

        Uses streaming download to handle large files (audio/video) without
        loading entire file into memory, and with per-chunk timeouts instead
        of a single timeout for the entire download.

        Args:
            url: URL to download from.
            output_path: Path to save the file.

        Returns:
            The output path on success.

        Raises:
            ArtifactDownloadError: If download fails or authentication expired.
        """
        # Validate URL scheme and domain before sending auth cookies.
        # httpx sends cookies to every request made by the client regardless of
        # domain, so we must ensure the URL belongs to a trusted Google domain.
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ArtifactDownloadError("media", details=f"Download URL must use HTTPS: {url[:80]}")
        trusted = (".google.com", ".googleusercontent.com", ".googleapis.com")
        if not any(parsed.netloc == d.lstrip(".") or parsed.netloc.endswith(d) for d in trusted):
            raise ArtifactDownloadError(
                "media", details=f"Untrusted download domain: {parsed.netloc}"
            )

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Use temp file to avoid leaving corrupted partial files on failure
        temp_file = output_file.with_suffix(output_file.suffix + ".tmp")

        # Load cookies with domain info for cross-domain redirect handling
        cookies = load_httpx_cookies(path=self._storage_path)

        # Use granular timeouts: 10s to connect, 30s per chunk read/write
        # This allows large files to download without timeout while still
        # detecting network failures quickly
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=30.0)

        try:
            # Nested context managers required: client.stream() returns an async
            # context manager that must run within the client's scope
            async with httpx.AsyncClient(  # noqa: SIM117
                cookies=cookies,
                follow_redirects=True,
                timeout=timeout,
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()

                    content_type = response.headers.get("content-type", "")
                    if "text/html" in content_type:
                        raise ArtifactDownloadError(
                            "media",
                            details="Download failed: received HTML instead of media file. "
                            "Authentication may have expired. Run 'notebooklm login'.",
                        )

                    # Stream to file in chunks to handle large files efficiently
                    total_bytes = 0
                    with open(temp_file, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            total_bytes += len(chunk)

                    if total_bytes == 0:
                        raise ArtifactDownloadError(
                            "media",
                            details="Download produced 0 bytes -- the remote file may be missing or empty",
                        )

                    # Only move to final location on success
                    temp_file.rename(output_file)
                    logger.debug("Downloaded %s (%d bytes)", url[:60], total_bytes)
                    return output_path
        except Exception:
            # Clean up partial temp file on any failure
            temp_file.unlink(missing_ok=True)
            raise

    async def _call_generate(
        self, notebook_id: str, params: builtins.list[Any]
    ) -> GenerationStatus:
        """Make a generation RPC call with error handling."""
        artifact_type = params[2][2] if len(params) > 2 and len(params[2]) > 2 else "unknown"
        logger.debug("Generating artifact type=%s in notebook %s", artifact_type, notebook_id)
        try:
            result = await self._core.rpc_call(
                RPCMethod.CREATE_ARTIFACT,
                params,
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return self._parse_generation_result(result)
        except RPCError as e:
            if e.rpc_code == "USER_DISPLAYABLE_ERROR":
                return GenerationStatus(
                    task_id="",
                    status="failed",
                    error=str(e),
                    error_code=str(e.rpc_code) if e.rpc_code is not None else None,
                )
            raise

    def _parse_generation_result(self, result: Any) -> GenerationStatus:
        """Parse generation API result into GenerationStatus.

        The API returns a single ID that serves as both the task_id (for polling
        during generation) and the artifact_id (once complete). This ID is at
        position [0][0] in the response and becomes Artifact.id in the list.
        """
        if result and isinstance(result, list) and len(result) > 0:
            artifact_data = result[0]
            artifact_id = (
                artifact_data[0]
                if isinstance(artifact_data, list) and len(artifact_data) > 0
                else None
            )
            status_code = (
                artifact_data[4]
                if isinstance(artifact_data, list) and len(artifact_data) > 4
                else None
            )

            if artifact_id:
                status = (
                    artifact_status_to_str(status_code) if status_code is not None else "pending"
                )
                return GenerationStatus(task_id=artifact_id, status=status)

        return GenerationStatus(
            task_id="", status="failed", error="Generation failed - no artifact_id returned"
        )

    @staticmethod
    def _extract_artifact_error(art: builtins.list[Any]) -> str | None:
        """Try to extract a human-readable error from a failed artifact.

        Google's batchexecute responses embed error information in varying
        positions depending on the artifact type.  This method walks through
        known locations and returns the first non-empty string it finds.

        Known error locations (reverse-engineered):
        - art[3]: Sometimes contains an error reason string.
        - art[5]: May contain a nested error payload similar to the
          UserDisplayableError structure in RPC responses.

        Args:
            art: Raw artifact data from ``_list_raw()``.

        Returns:
            A human-readable error string, or ``None`` if no error detail
            could be extracted.
        """
        try:
            # art[3] — simple string error reason
            if len(art) > 3 and isinstance(art[3], str) and art[3].strip():
                return art[3].strip()

            # art[5] — nested structure that may contain error text.
            # NOTE: This position is protocol-dependent and was
            # reverse-engineered; it may change without notice.
            if len(art) > 5 and isinstance(art[5], list):
                logger.debug(
                    "Falling back to art[5] for error extraction (art[3]=%r)",
                    art[3] if len(art) > 3 else "<missing>",
                )
                # Walk the list looking for the first non-empty string
                for item in art[5]:
                    if isinstance(item, str) and item.strip():
                        return item.strip()
                    if isinstance(item, list):
                        for sub in item:
                            if isinstance(sub, str) and sub.strip():
                                return sub.strip()

            return None
        except Exception:
            logger.warning(
                "Failed to extract error from artifact data: %r",
                art[:6] if len(art) > 6 else art,
                exc_info=True,
            )
            return None

    def _get_artifact_type_name(self, artifact_type: int) -> str:
        """Get human-readable name for an artifact type."""
        try:
            return ArtifactTypeCode(artifact_type).name
        except ValueError:
            return str(artifact_type)

    def _is_media_ready(self, art: builtins.list[Any], artifact_type: int) -> bool:
        """Check if media artifact has URLs populated.

        For media artifacts (audio, video, infographic, slide deck), the API may
        set status=COMPLETED before the actual media URLs are populated. This
        method verifies that URLs are available for download.

        Artifact array structure (from BATCHEXECUTE responses):
        - art[0]: artifact_id
        - art[2]: artifact_type (ArtifactTypeCode enum value)
        - art[4]: status_code (ArtifactStatus enum value)
        - art[6][5]: audio media URL list
        - art[8][i][0][0]: video media URL string (within nested variants and entries)
        - art[16][3]: slide deck PDF URL

        Args:
            art: Raw artifact data from _list_raw().
            artifact_type: The ArtifactTypeCode enum value.

        Returns:
            True if media URLs are available, or if artifact is non-media type.
            Returns True on unexpected structure (defensive fallback).
        """
        try:
            if artifact_type in _MEDIA_ARTIFACT_TYPES:
                return _extract_artifact_url(art, artifact_type) is not None

            # Non-media artifacts: status code alone is sufficient
            return True

        except (IndexError, TypeError) as e:
            # Defensive: if structure is unexpected, be conservative for media types
            is_media = artifact_type in _MEDIA_ARTIFACT_TYPES
            logger.debug(
                "Unexpected artifact structure for type %s (media=%s): %s",
                artifact_type,
                is_media,
                e,
            )
            return not is_media
