"""NodeCircle CRUD + manual rotation trigger."""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Node, NodeCircle
from app.core import circle_filters as cf
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
    # The node traffic really uses is `active_node_id`. A manual switch or a
    # failover changes it without touching the circle's stored position, so
    # an enabled circle that contains it reports THAT as its current node.
    from app.models import Settings as DBSettings
    row = (await session.exec(select(DBSettings).where(DBSettings.key == "active_node_id"))).first()
    try:
        active_id = int(row.value) if row and row.value else None
    except (TypeError, ValueError):
        active_id = None
    for c in circles:
        data = NodeCircleRead.model_validate(c).model_dump()
        node_ids = data.get("node_ids", [])
        idx = data.get("current_index", 0)
        if c.enabled and active_id is not None and active_id in node_ids:
            idx = node_ids.index(active_id)
            data["current_index"] = idx
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
    circle = NodeCircle(**data.model_dump(
        exclude={"node_ids", "excluded_node_ids", "excluded_countries", "subscription_ids"},
    ))
    circle.subscription_ids = json.dumps(data.subscription_ids)
    circle.node_ids = json.dumps(data.node_ids)
    circle.excluded_node_ids = json.dumps(data.excluded_node_ids)
    circle.excluded_countries = json.dumps(data.excluded_countries)
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
    old_ids = cf.parse_list(circle.node_ids)
    old_countries = set(cf.parse_list(circle.excluded_countries))
    # The followed subscriptions: the list wins; a bare `subscription_id`
    # (older clients) means a list of one, null means none.
    if patch.get("subscription_ids") is not None:
        sub_ids = [int(i) for i in dict.fromkeys(patch["subscription_ids"])]
    elif "subscription_id" in patch:
        sub_ids = [patch["subscription_id"]] if patch["subscription_id"] is not None else []
    elif "subscription_ids" in patch:
        sub_ids = []
    else:
        sub_ids = list(NodeCircleRead.model_validate(circle).subscription_ids)
    patch["subscription_ids"] = sub_ids
    patch["subscription_id"] = sub_ids[0] if sub_ids else None
    sub_id = sub_ids or None

    # Remember what the operator took out of a linked circle, or the next
    # refresh of the subscription puts it straight back.
    if sub_id is not None and patch.get("node_ids") is not None and "excluded_node_ids" not in patch:
        sub_owned = set((await session.exec(
            select(Node.id).where(Node.subscription_id.in_(sub_id))
        )).all())
        new_ids = set(patch["node_ids"])
        excluded = cf.parse_ids(circle.excluded_node_ids)
        excluded |= {i for i in old_ids if i in sub_owned and i not in new_ids}
        excluded -= new_ids
        patch["excluded_node_ids"] = sorted(excluded)

    # Newly excluded countries leave the circle now (the subscription's own
    # nodes only; ones added by hand stay).
    if patch.get("excluded_countries") is not None:
        added = set(patch["excluded_countries"]) - old_countries
        if added and sub_id is not None:
            ids_now = patch["node_ids"] if patch.get("node_ids") is not None else old_ids
            rows = (await session.exec(
                select(Node.id, Node.country, Node.name).where(Node.subscription_id.in_(sub_id))
            )).all()
            drop = {r[0] for r in rows if cf.node_country(r[1], r[2]) in added}
            if drop & set(ids_now):
                patch["node_ids"] = [i for i in ids_now if i not in drop]

    for key in ("node_ids", "excluded_node_ids", "excluded_countries", "subscription_ids"):
        if patch.get(key) is not None:
            patch[key] = json.dumps(patch[key])
        else:
            patch.pop(key, None)
    for k, v in patch.items():
        setattr(circle, k, v)
    if circle.current_index >= max(len(cf.parse_list(circle.node_ids)), 1):
        circle.current_index = 0
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
