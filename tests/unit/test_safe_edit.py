"""Tests for the `safe_edit` helper.

Telegram rejects an edit whose text + markup match what is already on
screen. That is a normal consequence of a double tap, not an error, but
if it escapes it reaches the bot's error handler and is reported as an
"Unhandled exception in bot".
"""
from __future__ import annotations

import pytest
from telegram.error import BadRequest

from app.handlers.helpers import safe_edit


class _FakeQuery:
    """Minimal stand-in for `telegram.CallbackQuery`."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    async def edit_message_text(self, text: str, **kwargs) -> None:
        self.calls.append((text, kwargs))
        if self.exc is not None:
            raise self.exc


@pytest.mark.asyncio
async def test_message_not_modified_is_swallowed() -> None:
    query = _FakeQuery(
        BadRequest(
            "Message is not modified: specified new message content and "
            "reply markup are exactly the same as a current content and "
            "reply markup of the message"
        )
    )

    # Must not raise.
    await safe_edit(query, "🤖 Analysing payment...")

    assert query.calls == [("🤖 Analysing payment...", {})]


@pytest.mark.asyncio
async def test_other_bad_requests_still_raise() -> None:
    query = _FakeQuery(BadRequest("Message to edit not found"))

    with pytest.raises(BadRequest):
        await safe_edit(query, "hello")


@pytest.mark.asyncio
async def test_kwargs_are_forwarded() -> None:
    query = _FakeQuery()

    await safe_edit(query, "hi", parse_mode="Markdown", reply_markup=None)

    assert query.calls == [("hi", {"parse_mode": "Markdown", "reply_markup": None})]
