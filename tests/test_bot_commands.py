"""The dashboard's command queue for cBots (app/bot_commands.py).

Each bot polls every 2 s. A command it has not picked up within 15 s expires and is never
handed out afterwards, so a bot that restarts later cannot act on a stale close.
"""
import asyncio

import pytest

from app import bot_commands
from app.bot_commands import CommandNotFound, CommandNotOwned, CommandQueue


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def queue(clock):
    return CommandQueue(clock=clock)


def test_a_command_is_handed_out_once(queue):
    cmd = queue.enqueue("bot-a", "close_position", 111)
    taken = queue.take_pending("bot-a")
    assert [c.id for c in taken] == [cmd.id]
    assert taken[0].for_bot() == {"id": cmd.id, "action": "close_position", "position_id": 111}
    assert queue.take_pending("bot-a") == []
    assert queue.get(cmd.id).status == "delivered"


def test_commands_go_only_to_their_bot(queue):
    queue.enqueue("bot-a", "close_all")
    assert queue.take_pending("bot-b") == []
    assert len(queue.take_pending("bot-a")) == 1


def test_an_unpolled_command_expires_and_is_never_delivered(queue, clock):
    cmd = queue.enqueue("bot-a", "close_all")
    clock.now += bot_commands.PENDING_TTL_S + 0.1
    assert queue.get(cmd.id).status == "expired"
    assert queue.take_pending("bot-a") == []      # a bot restarting later must not close anything


def test_a_delivered_command_without_a_result_becomes_unconfirmed(queue, clock):
    cmd = queue.enqueue("bot-a", "close_all")
    queue.take_pending("bot-a")
    clock.now += bot_commands.RESULT_TIMEOUT_S + 0.1
    assert queue.get(cmd.id).status == "unconfirmed"


def test_the_bot_reports_the_result(queue):
    cmd = queue.enqueue("bot-a", "close_all")
    queue.take_pending("bot-a")
    queue.record_result(cmd.id, "bot-a", "done", "closed 2", closed=2, failed=0)
    got = queue.get(cmd.id)
    assert (got.status, got.message, got.closed, got.failed) == ("done", "closed 2", 2, 0)
    assert got.for_dashboard()["status"] == "done"


def test_bad_results_are_refused(queue):
    cmd = queue.enqueue("bot-a", "close_all")
    with pytest.raises(CommandNotOwned):
        queue.record_result(cmd.id, "bot-b", "done")
    with pytest.raises(CommandNotFound):
        queue.record_result("nope", "bot-a", "done")
    with pytest.raises(ValueError):
        queue.record_result(cmd.id, "bot-a", "maybe")


def test_a_double_click_returns_the_same_command(queue):
    first = queue.enqueue("bot-a", "close_position", 111)
    assert queue.enqueue("bot-a", "close_position", 111).id == first.id
    assert queue.enqueue("bot-a", "close_position", 222).id != first.id
    queue.take_pending("bot-a")
    assert queue.enqueue("bot-a", "close_position", 111).id == first.id   # delivered, not finished yet


def test_a_finished_command_can_be_sent_again(queue):
    first = queue.enqueue("bot-a", "close_all")
    queue.take_pending("bot-a")
    queue.record_result(first.id, "bot-a", "failed", "MarketClosed")
    assert queue.enqueue("bot-a", "close_all").id != first.id


def test_finished_commands_are_purged_after_ten_minutes(queue, clock):
    old = queue.enqueue("bot-a", "close_all")
    queue.take_pending("bot-a")
    queue.record_result(old.id, "bot-a", "done")
    clock.now += bot_commands.RETAIN_S + 1
    queue.enqueue("bot-b", "close_all")           # enqueue purges
    assert queue.get(old.id) is None


@pytest.mark.parametrize("action, position_id", [("reboot", None), ("close_position", None)])
def test_invalid_commands_are_refused(queue, action, position_id):
    with pytest.raises(ValueError):
        queue.enqueue("bot-a", action, position_id)


def test_returned_commands_are_copies(queue):
    cmd = queue.enqueue("bot-a", "close_all")
    cmd.status = "done"
    assert queue.get(cmd.id).status == "pending"


def test_wait_for_final_returns_once_the_bot_reports(monkeypatch):
    monkeypatch.setattr(bot_commands, "WAIT_POLL_S", 0.01)
    queue = CommandQueue()
    cmd = queue.enqueue("bot-a", "close_all")

    async def scenario():
        async def bot():
            await asyncio.sleep(0.05)
            queue.take_pending("bot-a")
            queue.record_result(cmd.id, "bot-a", "done", closed=1)

        task = asyncio.create_task(bot())
        final = await bot_commands.wait_for_final(queue, cmd.id, timeout_s=2.0)
        await task
        return final

    final = asyncio.run(scenario())
    assert final.status == "done" and final.closed == 1


def test_wait_for_final_gives_up_at_the_timeout(monkeypatch):
    monkeypatch.setattr(bot_commands, "WAIT_POLL_S", 0.01)
    queue = CommandQueue()
    cmd = queue.enqueue("bot-a", "close_all")
    final = asyncio.run(bot_commands.wait_for_final(queue, cmd.id, timeout_s=0.05))
    assert final.status == "pending"
