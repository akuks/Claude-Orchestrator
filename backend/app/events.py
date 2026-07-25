"""In-memory pub/sub broker for live task event streaming.

One asyncio.Queue is handed to each websocket subscriber. The worker publishes
normalized events here; durable history lives in the DB (task_events) so a
reconnecting client can replay what it missed.
"""

import asyncio
from collections import defaultdict


class Broker:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[task_id].add(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(task_id)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._subs.pop(task_id, None)

    async def publish(self, task_id: str, event: dict) -> None:
        for q in list(self._subs.get(task_id, ())):
            await q.put(event)


broker = Broker()
