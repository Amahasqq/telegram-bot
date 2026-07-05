import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.memory import DEFAULT_COSTS, MemoryManager


@pytest.mark.asyncio
async def test_load_fresh_start(mock_hf_api):
    mm = None
    try:
        mock_hf_api.hf_hub_download.side_effect = Exception("Dataset not found")
        mm = MemoryManager(hf_token="test", dataset_repo="test/test")
        await mm.load()
        assert mm.data["conversations"] == {}
        assert mm.data["notes"] == []
        assert mm.data["user_facts"] == {}
        assert mm.data["processed_updates"] == []
        assert mm.data["user_chat_ids"] == {}
        assert mm.data["costs"] == DEFAULT_COSTS
    finally:
        if mm:
            with patch.object(mm, "_save_now", new_callable=AsyncMock):
                await mm.stop()


@pytest.mark.asyncio
async def test_load_existing_data(mock_hf_api):
    mm = None
    try:
        existing = {
            "conversations": {"1": [{"role": "user", "content": "hi"}]},
            "notes": [{"text": "test", "timestamp": "2024-01-01"}],
            "user_facts": {"1": ["likes python"]},
            "costs": {"total_input_tokens": 100, "total_output_tokens": 50, "daily_date": "2024-01-01", "daily_input_tokens": 10, "daily_output_tokens": 5},
        }
        mock_data = json.dumps(existing)
        mock_open = MagicMock()
        mock_open.__enter__.return_value.read.return_value = mock_data

        with patch("builtins.open", return_value=mock_open):
            mm = MemoryManager(hf_token="test", dataset_repo="test/test")
            await mm.load()

        assert mm.data["conversations"]["1"][0]["content"] == "hi"
        assert mm.data["notes"][0]["text"] == "test"
        assert mm.data["user_facts"]["1"] == ["likes python"]
        assert mm.data["costs"]["total_input_tokens"] == 100
        assert mm.data["processed_updates"] == []
        assert mm.data["user_chat_ids"] == {}
    finally:
        if mm:
            with patch.object(mm, "_save_now", new_callable=AsyncMock):
                await mm.stop()


@pytest.mark.asyncio
async def test_add_and_get_messages(memory_manager):
    await memory_manager.add_message("1", "user", "hello")
    await memory_manager.add_message("1", "assistant", "hi there")

    history = await memory_manager.get_user_history("1")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "hello"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "hi there"


@pytest.mark.asyncio
async def test_history_max_limit(memory_manager):
    for i in range(25):
        await memory_manager.add_message("1", "user", f"msg {i}")

    history = await memory_manager.get_user_history("1")
    assert len(history) == 20
    assert history[0]["content"] == "msg 5"
    assert history[-1]["content"] == "msg 24"


@pytest.mark.asyncio
async def test_clear_history(memory_manager):
    await memory_manager.add_message("1", "user", "hello")
    await memory_manager.clear_history("1")
    history = await memory_manager.get_user_history("1")
    assert history == []


@pytest.mark.asyncio
async def test_add_and_get_facts(memory_manager):
    await memory_manager.add_facts("1", ["likes python", "works remotely"])
    facts = await memory_manager.get_user_facts("1")
    assert "likes python" in facts
    assert "works remotely" in facts
    assert len(facts) == 2


@pytest.mark.asyncio
async def test_add_facts_deduplication(memory_manager):
    await memory_manager.add_facts("1", ["likes python"])
    await memory_manager.add_facts("1", ["likes python", "works remotely"])
    facts = await memory_manager.get_user_facts("1")
    assert len(facts) == 2
    assert facts == ["likes python", "works remotely"]


@pytest.mark.asyncio
async def test_add_facts_empty(memory_manager):
    await memory_manager.add_facts("1", [])
    facts = await memory_manager.get_user_facts("1")
    assert facts == []


@pytest.mark.asyncio
async def test_notes(memory_manager):
    await memory_manager.add_note("first note")
    await memory_manager.add_note("second note")
    notes = await memory_manager.get_notes()
    assert len(notes) == 2
    assert notes[0]["text"] == "first note"
    assert notes[1]["text"] == "second note"


@pytest.mark.asyncio
async def test_clear_notes(memory_manager):
    await memory_manager.add_note("test")
    await memory_manager.clear_notes()
    notes = await memory_manager.get_notes()
    assert notes == []


@pytest.mark.asyncio
async def test_log_and_get_costs(memory_manager):
    await memory_manager.log_costs(100, 50)
    costs = await memory_manager.get_costs()
    assert costs["total_input_tokens"] == 100
    assert costs["total_output_tokens"] == 50
    assert costs["daily_input_tokens"] == 100
    assert costs["daily_output_tokens"] == 50


@pytest.mark.asyncio
async def test_log_costs_incremental(memory_manager):
    await memory_manager.log_costs(100, 50)
    await memory_manager.log_costs(200, 100)
    costs = await memory_manager.get_costs()
    assert costs["total_input_tokens"] == 300
    assert costs["total_output_tokens"] == 150
    assert costs["daily_input_tokens"] == 300
    assert costs["daily_output_tokens"] == 150


@pytest.mark.asyncio
async def test_set_user_chat_id(memory_manager):
    await memory_manager.set_user_chat_id("1", 12345)
    assert memory_manager.data["user_chat_ids"]["1"] == 12345


@pytest.mark.asyncio
async def test_claim_update_deduplication(memory_manager):
    assert await memory_manager.claim_update(1) is True
    assert await memory_manager.claim_update(1) is False
    assert await memory_manager.claim_update(2) is True


@pytest.mark.asyncio
async def test_save_dirty_flag(memory_manager):
    memory_manager.dirty = True
    memory_manager.last_sync = 0
    with patch.object(memory_manager, "_save_now", new_callable=AsyncMock) as mock_save:
        await memory_manager.save()
        mock_save.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_not_dirty(memory_manager):
    memory_manager.dirty = False
    with patch.object(memory_manager, "_save_now", new_callable=AsyncMock) as mock_save:
        await memory_manager.save()
        mock_save.assert_not_awaited()
