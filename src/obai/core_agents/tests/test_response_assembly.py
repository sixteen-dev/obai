"""Unit tests for final-answer assembly from streamed hub events."""

from __future__ import annotations

import pytest

from core_agents.response_assembly import AnswerAccumulator


def test_commentary_message_is_dropped_from_the_answer() -> None:
    """Interim narration must not be glued onto the final answer.

    Captured from a real gate run: the hub emitted a commentary message
    alongside its specialist calls, and the client concatenated it to the
    answer with no separator, producing
    "...before concluding.**Tesla's 2027 Outlook -- ...".
    """
    acc = AnswerAccumulator()

    for delta in ("The first pass found useful evidence; ", "verifying dates before concluding."):
        acc.add_delta("msg_commentary", delta)
    acc.note_message("msg_commentary", "ignored", "commentary")
    acc.add_delta("msg_answer", "**Tesla's 2027 Outlook**")
    acc.note_message("msg_answer", "**Tesla's 2027 Outlook**", "final_answer")

    assert acc.text() == "**Tesla's 2027 Outlook**"


def test_unlabelled_messages_are_kept() -> None:
    """A model that never sets phase must still produce its answer."""
    acc = AnswerAccumulator()

    acc.add_delta("msg_1", "NVDA closed at $180.")
    acc.note_message("msg_1", "NVDA closed at $180.", None)

    assert acc.text() == "NVDA closed at $180."


def test_message_without_deltas_falls_back_to_its_text() -> None:
    """Non-streaming turns deliver the message whole, with no deltas."""
    acc = AnswerAccumulator()

    acc.note_message("msg_1", "AAPL trades at $212.", "final_answer")

    assert acc.text() == "AAPL trades at $212."


def test_streamed_text_is_not_duplicated_by_the_completed_message() -> None:
    """The completed message repeats what the deltas already carried."""
    acc = AnswerAccumulator()

    acc.add_delta("msg_1", "AAPL trades ")
    acc.add_delta("msg_1", "at $212.")
    acc.note_message("msg_1", "AAPL trades at $212.", "final_answer")

    assert acc.text() == "AAPL trades at $212."


def test_reset_discards_pre_answer_text() -> None:
    """A hub tool call invalidates everything streamed before it."""
    acc = AnswerAccumulator()

    acc.add_delta("msg_1", "Checking the chain first.")
    acc.reset()
    acc.add_delta("msg_2", "SPY closed at $640.")

    assert acc.text() == "SPY closed at $640."


def test_commentary_streaming_past_a_reset_is_still_dropped() -> None:
    """The real event order, which the old reset-only heuristic missed.

    The SDK emits a turn's tool-call event before that turn's text deltas
    finish flushing, so the reset lands mid-commentary and the tail arrives
    afterwards. That tail is what leaked into the CORE-RESEARCH answer.
    """
    acc = AnswerAccumulator()

    acc.add_delta("msg_commentary", "The first pass found useful evidence; ")
    acc.note_message("msg_commentary", "…", "commentary")
    acc.reset()  # tool_call_item for the same turn
    acc.add_delta("msg_commentary", "verifying dates before concluding.")
    acc.add_delta("msg_answer", "**Tesla's 2027 Outlook**")

    assert acc.text() == "**Tesla's 2027 Outlook**"


def test_multiple_answer_messages_join_in_arrival_order() -> None:
    """Two non-commentary messages in one segment stay ordered."""
    acc = AnswerAccumulator()

    acc.add_delta("msg_1", "Part one. ")
    acc.add_delta("msg_2", "Part two.")
    acc.add_delta("msg_1", "Still one. ")

    assert acc.text() == "Part one. Still one. Part two."


def test_empty_deltas_are_ignored() -> None:
    """Providers send empty keepalive deltas; they add no text."""
    acc = AnswerAccumulator()

    acc.add_delta("msg_1", "")

    assert acc.text() == ""


@pytest.mark.parametrize("method", ["add_delta", "note_message"])
def test_empty_item_id_fails_loud(method: str) -> None:
    """An unattributable chunk cannot be filtered, so it must not be silent."""
    acc = AnswerAccumulator()
    args = ("", "text") if method == "add_delta" else ("", "text", "final_answer")

    with pytest.raises(ValueError, match="non-empty item_id"):
        getattr(acc, method)(*args)
