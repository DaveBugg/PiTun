"""NodeCircle CRUD + manual rotation trigger."""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Node, NodeCircle
from app.schemas import NodeCircleCreate, NodeCircleRead, NodeCircleUpdate

router = APIRouter(prefix="/nodecircle", tags=["nodecircle"])


@router.get("", response_model=List[NodeCircleRead])
async def list_circles(session: AsyncSession = Depends(get_session)):
    circles = list((await session.exec(select(NodeCircle))).all())

    # Collect every current node_id across all circles, then fetch node names
    # in a single query. Previously this was N+1: one `session.get(Node, ...)`
    # per circle — visible on dashboards with many circles.
    current_ids: set[int] = set()
    circle_payloads = []
    for c in circles:
        data = NodeCircleRead.model_validate(c).model_dump()
        node_ids = data.get("node_ids", [])
        idx = data.get("current_index", 0)
        cur_id: Optional[int] = None
        if node_ids and idx < len(node_ids):
            cur_id = node_ids[idx]
            current_ids.add(cur_id)
        circle_payloads.append((data, cur_id))

    name_by_id: dict[int, str] = {}
    if current_ids:
        rows = (await session.exec(
            select(Node.id, Node.name).where(Node.id.in_(current_ids))
        )).all()
        # .exec() on a multi-column select returns Row objects here
        for row in rows:
            nid, nname = (row[0], row[1]) if not hasattr(row, "id") else (row.id, row.name)
            name_by_id[nid] = nname

    result = []
    for data, cur_id in circle_payloads:
        if cur_id is not None:
            data["current_node_name"] = name_by_id.get(cur_id)
        result.append(NodeCircleRead(**data))
    return result


@router.post("", response_model=NodeCircleRead, status_code=201)
async def create_circle(data: NodeCircleCreate, session: AsyncSession = Depends(get_session)):
    circle = NodeCircle(**data.model_dump(exclude={"node_ids"}))
    circle.node_ids = json.dumps(data.node_ids)
    session.add(circle)
    await session.commit()
    await session.refresh(circle)
    return NodeCircleRead.model_validate(circle)


@router.get("/{circle_id}", response_model=NodeCircleRead)
async def get_circle(circle_id: int, session: AsyncSession = Depends(get_session)):
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")
    return NodeCircleRead.model_validate(circle)


@router.patch("/{circle_id}", response_model=NodeCircleRead)
async def update_circle(circle_id: int, data: NodeCircleUpdate, session: AsyncSession = Depends(get_session)):
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")
    patch = data.model_dump(exclude_unset=True)
    if "node_ids" in patch and patch["node_ids"] is not None:
        patch["node_ids"] = json.dumps(patch["node_ids"])
    for k, v in patch.items():
        setattr(circle, k, v)
    session.add(circle)
    await session.commit()
    await session.refresh(circle)
    return NodeCircleRead.model_validate(circle)


@router.delete("/{circle_id}", status_code=204)
async def delete_circle(circle_id: int, session: AsyncSession = Depends(get_session)):
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")
    await session.delete(circle)
    await session.commit()
    from app.core.circle_scheduler import circle_scheduler
    circle_scheduler._next_rotate.pop(circle_id, None)


@router.post("/{circle_id}/rotate", response_model=NodeCircleRead)
async def rotate_now(circle_id: int, session: AsyncSession = Depends(get_session)):
    """Manually trigger rotation to the next node."""
    from app.core.circle_scheduler import circle_scheduler
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")
    if not circle.enabled:
        raise HTTPException(400, "Cannot rotate a disabled circle")
    await circle_scheduler.rotate_circle(circle_id)
    circle = await session.get(NodeCircle, circle_id)
    await session.refresh(circle)
    return NodeCircleRead.model_validate(circle)
