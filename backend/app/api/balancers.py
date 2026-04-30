"""Balancer groups CRUD API."""
import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import BalancerGroup, RoutingRule
from app.schemas import BalancerGroupCreate, BalancerGroupRead, BalancerGroupUpdate

router = APIRouter(prefix="/balancers", tags=["balancers"])


@router.get("", response_model=List[BalancerGroupRead])
async def list_balancers(session: AsyncSession = Depends(get_session)):
    return list((await session.exec(select(BalancerGroup))).all())


@router.post("", response_model=BalancerGroupRead, status_code=201)
async def create_balancer(body: BalancerGroupCreate, session: AsyncSession = Depends(get_session)):
    bg = BalancerGroup(
        name=body.name,
        enabled=body.enabled,
        node_ids=json.dumps(body.node_ids),
        strategy=body.strategy,
    )
    session.add(bg)
    await session.commit()
    await session.refresh(bg)
    return bg


@router.get("/{bg_id}", response_model=BalancerGroupRead)
async def get_balancer(bg_id: int, session: AsyncSession = Depends(get_session)):
    bg = await session.get(BalancerGroup, bg_id)
    if not bg:
        raise HTTPException(404, "Balancer group not found")
    return bg


@router.patch("/{bg_id}", response_model=BalancerGroupRead)
async def update_balancer(bg_id: int, body: BalancerGroupUpdate, session: AsyncSession = Depends(get_session)):
    bg = await session.get(BalancerGroup, bg_id)
    if not bg:
        raise HTTPException(404, "Balancer group not found")

    patches = body.model_dump(exclude_unset=True)
    for key, value in patches.items():
        if key == "node_ids":
            setattr(bg, key, json.dumps(value))
        else:
            setattr(bg, key, value)

    session.add(bg)
    await session.commit()
    await session.refresh(bg)
    return bg


@router.delete("/{bg_id}", status_code=204)
async def delete_balancer(bg_id: int, session: AsyncSession = Depends(get_session)):
    bg = await session.get(BalancerGroup, bg_id)
    if not bg:
        raise HTTPException(404, "Balancer group not found")
    await session.delete(bg)

    orphan_rules = (await session.exec(
        select(RoutingRule).where(RoutingRule.action == f"balancer:{bg_id}")
    )).all()
    for r in orphan_rules:
        await session.delete(r)

    await session.commit()
