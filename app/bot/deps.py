"""Shared bot plumbing: authorization filter and per-handler helpers."""

from __future__ import annotations

from collections.abc import Callable  # noqa: F401  (kept for typing helpers)

from aiogram.filters import BaseFilter, Filter
from aiogram.types import CallbackQuery, Message

from app.bot import texts  # noqa: F401  (re-exported for handlers)
from app.runtime import get_runtime
from app.services.auth_service import (
    authorize,  # noqa: F401  (re-exported for handlers)
)


class AuthorizedFilter(BaseFilter):
    """Route only to Telegram users on the allow-list.

    Applied to every action, not just ``/start`` (baseline "Авторизация").
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        from_user = event.from_user
        if from_user is None:
            return False
        settings = get_runtime().settings
        return from_user.id in settings.allowed_telegram_ids


# A filter that matches unauthorised users (inverse of the allow-list).
def unauthorized_filter() -> Filter:
    async def check(event: Message | CallbackQuery) -> bool:
        from_user = event.from_user
        if from_user is None:
            return False
        return from_user.id not in get_runtime().settings.allowed_telegram_ids

    return check


def private_chat_id(event: Message | CallbackQuery) -> int:
    if isinstance(event, CallbackQuery):
        assert event.from_user is not None
        return event.from_user.id
    assert event.chat is not None
    return event.chat.id


def display_name(event: Message | CallbackQuery) -> str | None:
    user = event.from_user
    if user is None:
        return None
    return user.full_name or user.username
