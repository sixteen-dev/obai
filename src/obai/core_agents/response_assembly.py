"""Assemble a hub run's final answer from its streamed events.

Reasoning models emit interim assistant messages labelled
``phase="commentary"`` next to their tool calls ("The first pass found
useful evidence; I'm verifying dates before concluding."). Showing them
live is the point of streaming, but concatenating them into the final
response glues a status line onto the front of the answer with no
separator. The API reports the phase when the message item completes,
which is after its text has already streamed, so text is grouped by its
originating message id and discarded once the phase is known.
"""

from __future__ import annotations

from dataclasses import dataclass, field

COMMENTARY_PHASE = "commentary"


@dataclass
class AnswerAccumulator:
    """Collect streamed assistant text, dropping interim commentary.

    Attributes:
        _order: Message ids in first-seen order, so the join is stable.
        _deltas: Streamed text chunks per message id.
        _dropped: Message ids whose phase marked them as commentary.
    """

    _order: list[str] = field(default_factory=list)
    _deltas: dict[str, list[str]] = field(default_factory=dict)
    _dropped: set[str] = field(default_factory=set)

    def add_delta(self, item_id: str, delta: str) -> None:
        """Record one streamed text chunk.

        Args:
            item_id: Id of the assistant message the chunk belongs to.
            delta: The chunk. Empty chunks are ignored.

        Raises:
            ValueError: If ``item_id`` is empty.
        """
        if not item_id:
            raise ValueError("add_delta requires a non-empty item_id")
        if not delta:
            return
        self._track(item_id).append(delta)

    def note_message(self, item_id: str, text: str, phase: str | None) -> None:
        """Record a completed assistant message and its phase.

        Commentary is dropped. For a non-commentary message, ``text`` is
        used only when no deltas arrived for it, which is the non-streaming
        path; a streamed message already holds its own chunks.

        Args:
            item_id: Id of the completed assistant message.
            text: The message's full text.
            phase: ``commentary``, ``final_answer``, or None when the model
                does not label phases at all.

        Raises:
            ValueError: If ``item_id`` is empty.
        """
        if not item_id:
            raise ValueError("note_message requires a non-empty item_id")
        chunks = self._track(item_id)
        if phase == COMMENTARY_PHASE:
            self._dropped.add(item_id)
            return
        if not chunks and text:
            chunks.append(text)

    def reset(self) -> None:
        """Discard the text collected so far, keeping commentary verdicts.

        A hub tool call means any text before it was pre-answer narration
        from a model that does not label phases. Kept as the fallback for
        those models; phase labelling handles the rest.

        ``_dropped`` deliberately survives: the SDK emits a turn's tool-call
        event before that turn's text deltas finish flushing, so a message
        already judged commentary can still be streaming when the reset
        lands. Forgetting the verdict here would let it back into the answer.
        """
        self._order.clear()
        self._deltas.clear()

    def text(self) -> str:
        """Return the answer text with commentary removed."""
        return "".join(
            chunk
            for item_id in self._order
            if item_id not in self._dropped
            for chunk in self._deltas[item_id]
        )

    def _track(self, item_id: str) -> list[str]:
        """Return the chunk list for ``item_id``, registering it if new."""
        if item_id not in self._deltas:
            self._deltas[item_id] = []
            self._order.append(item_id)
        return self._deltas[item_id]
