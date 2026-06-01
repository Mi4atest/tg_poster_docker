"""Middleware that buffers messages of the same media_group_id and dispatches
them to the handler as a single sorted list under ``data["album"]``.

Without this middleware Telegram delivers album items as separate messages
in arbitrary order (depending on how aiogram schedules tasks), which is why
users previously had to use the "Send without grouping" workaround to
preserve photo order.

How it works:
    1. The first message of a new ``media_group_id`` opens a buffer and waits
       ``latency`` seconds for the rest of the group to arrive.
    2. Every subsequent message with the same ``media_group_id`` is appended
       to the buffer and silently swallowed (the dispatcher chain is NOT
       continued for it).
    3. When the timer fires, the buffer is sorted by ``message_id`` (Telegram
       guarantees increasing ``message_id`` in the order the user sent the
       photos) and the handler is invoked once with ``data["album"]`` set to
       the full sorted list. The first message of the group is passed as the
       ``event`` so existing state/filter logic keeps working.

Handlers that want to receive the album just declare an extra keyword
argument named ``album`` (with a default of ``None`` so single-message paths
still work):

    async def my_handler(message: Message, state: FSMContext,
                         album: list[Message] | None = None):
        ...

Messages without a ``media_group_id`` pass through untouched.
"""

import asyncio
from typing import Any, Awaitable, Callable, Dict, List

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject


class AlbumMiddleware(BaseMiddleware):
    """Collect messages of the same media group and dispatch them once."""

    def __init__(self, latency: float = 0.7) -> None:
        self.latency = latency
        self._album_data: Dict[str, List[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.media_group_id:
            return await handler(event, data)

        group_id = event.media_group_id

        # If the buffer already exists, this is a follow-up message: just
        # append and stop the dispatcher chain — the original call will
        # process the whole album.
        if group_id in self._album_data:
            self._album_data[group_id].append(event)
            return None

        # First message of a new group — open the buffer and wait for siblings.
        self._album_data[group_id] = [event]
        try:
            await asyncio.sleep(self.latency)
            album = self._album_data.pop(group_id, [event])
            album.sort(key=lambda m: m.message_id)
            data["album"] = album
            return await handler(event, data)
        finally:
            # Make sure we never leak a stale buffer if anything went wrong.
            self._album_data.pop(group_id, None)
