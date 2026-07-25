from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..database import SessionLocal
from ..events import broker
from ..models import TaskEvent

router = APIRouter(tags=["stream"])


@router.websocket("/tasks/{task_id}/stream")
async def stream_task(websocket: WebSocket, task_id: str):
    await websocket.accept()
    # Client may pass ?last_seq=N to resume without replaying what it has.
    try:
        last_seq = int(websocket.query_params.get("last_seq", 0))
    except ValueError:
        last_seq = 0

    # Subscribe BEFORE replaying history so no live event is missed; dedupe by
    # seq against the high-water mark we've already sent.
    q = broker.subscribe(task_id)
    sent = last_seq
    try:
        async with SessionLocal() as s:
            rows = (
                await s.execute(
                    select(TaskEvent)
                    .where(TaskEvent.task_id == task_id, TaskEvent.seq > last_seq)
                    .order_by(TaskEvent.seq)
                )
            ).scalars().all()
            for e in rows:
                await websocket.send_json(
                    {"seq": e.seq, "type": e.type, "payload": e.payload}
                )
                sent = max(sent, e.seq)

        while True:
            event = await q.get()
            seq = event.get("seq", 0)
            if seq and seq <= sent:
                continue
            await websocket.send_json(event)
            if seq:
                sent = seq
    except WebSocketDisconnect:
        pass
    finally:
        broker.unsubscribe(task_id, q)
