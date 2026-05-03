"""Unit tests for notebook operations."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm._notebooks import NotebooksAPI, _infer_known_notebook_limit
from notebooklm.exceptions import NetworkError, NotebookLimitError, RPCError
from notebooklm.rpc import RPCMethod
from notebooklm.types import Notebook


def _make_api() -> NotebooksAPI:
    core = MagicMock()
    core.rpc_call = AsyncMock()
    return NotebooksAPI(core, sources_api=MagicMock())


def _owned_notebooks(count: int) -> list[Notebook]:
    return [Notebook(id=f"owned_{i}", title=f"Owned {i}", is_owner=True) for i in range(count)]


def _shared_notebooks(count: int) -> list[Notebook]:
    return [Notebook(id=f"shared_{i}", title=f"Shared {i}", is_owner=False) for i in range(count)]


def _create_invalid_argument_error(
    *, method_id: str = RPCMethod.CREATE_NOTEBOOK.value, rpc_code: int = 3
) -> RPCError:
    return RPCError(
        "RPC CCqFvf returned null result with status code 3 (Invalid argument).",
        method_id=method_id,
        rpc_code=rpc_code,
    )


class TestInferKnownNotebookLimit:
    def test_free_limit_boundary(self):
        assert _infer_known_notebook_limit(99) == 100
        assert _infer_known_notebook_limit(100) == 100

    def test_paid_limit_boundary(self):
        assert _infer_known_notebook_limit(499) == 500
        assert _infer_known_notebook_limit(500) == 500

    def test_non_boundary_count_is_not_classified(self):
        assert _infer_known_notebook_limit(0) is None
        assert _infer_known_notebook_limit(98) is None
        assert _infer_known_notebook_limit(250) is None
        assert _infer_known_notebook_limit(498) is None
        assert _infer_known_notebook_limit(600) is None


class TestCreateNotebookQuotaDetection:
    @pytest.mark.asyncio
    async def test_create_invalid_argument_near_paid_limit_raises_limit_error(self):
        api = _make_api()
        original = _create_invalid_argument_error()
        api._core.rpc_call = AsyncMock(side_effect=original)
        api.list = AsyncMock(return_value=_owned_notebooks(499))

        with pytest.raises(NotebookLimitError) as exc_info:
            await api.create("Daily News")

        assert exc_info.value.current_count == 499
        assert exc_info.value.limit == 500
        assert exc_info.value.original_error is original
        assert "499/500" in str(exc_info.value)
        api.list.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_invalid_argument_near_free_limit_raises_limit_error(self):
        api = _make_api()
        api._core.rpc_call = AsyncMock(side_effect=_create_invalid_argument_error())
        api.list = AsyncMock(return_value=_owned_notebooks(100))

        with pytest.raises(NotebookLimitError) as exc_info:
            await api.create("Free Limit")

        assert exc_info.value.current_count == 100
        assert exc_info.value.limit == 100

    @pytest.mark.asyncio
    async def test_create_invalid_argument_away_from_limit_preserves_rpc_error(self):
        api = _make_api()
        original = _create_invalid_argument_error()
        api._core.rpc_call = AsyncMock(side_effect=original)
        api.list = AsyncMock(return_value=_owned_notebooks(250))

        with pytest.raises(RPCError) as exc_info:
            await api.create("Probably Bad Payload")

        assert exc_info.value is original

    @pytest.mark.asyncio
    async def test_non_quota_rpc_code_preserves_rpc_error_without_listing(self):
        api = _make_api()
        original = _create_invalid_argument_error(rpc_code=13)
        api._core.rpc_call = AsyncMock(side_effect=original)
        api.list = AsyncMock(return_value=_owned_notebooks(500))

        with pytest.raises(RPCError) as exc_info:
            await api.create("Internal Failure")

        assert exc_info.value is original
        api.list.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_create_method_preserves_rpc_error_without_listing(self):
        api = _make_api()
        original = _create_invalid_argument_error(method_id=RPCMethod.GET_NOTEBOOK.value)
        api._core.rpc_call = AsyncMock(side_effect=original)
        api.list = AsyncMock(return_value=_owned_notebooks(500))

        with pytest.raises(RPCError) as exc_info:
            await api.create("Unexpected Method")

        assert exc_info.value is original
        api.list.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shared_notebooks_do_not_trigger_owned_quota_error(self):
        api = _make_api()
        original = _create_invalid_argument_error()
        api._core.rpc_call = AsyncMock(side_effect=original)
        api.list = AsyncMock(return_value=_owned_notebooks(20) + _shared_notebooks(479))

        with pytest.raises(RPCError) as exc_info:
            await api.create("Shared Notebooks Should Not Count")

        assert exc_info.value is original

    @pytest.mark.asyncio
    async def test_list_failure_preserves_original_create_error(self):
        api = _make_api()
        original = _create_invalid_argument_error()
        api._core.rpc_call = AsyncMock(side_effect=original)
        api.list = AsyncMock(side_effect=NetworkError("list failed"))

        with pytest.raises(RPCError) as exc_info:
            await api.create("List Fails")

        assert exc_info.value is original

    @pytest.mark.asyncio
    async def test_list_parse_bug_preserves_original_create_error(self):
        api = _make_api()
        original = _create_invalid_argument_error()
        api._core.rpc_call = AsyncMock(side_effect=original)
        api.list = AsyncMock(side_effect=ValueError("bad notebook data"))

        with pytest.raises(RPCError) as exc_info:
            await api.create("List Parse Fails")

        assert exc_info.value is original
