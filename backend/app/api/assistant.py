"""AI assistant: settings, models, conversations, streamed turns, plans,
facts, local model discovery."""
from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.assistant import settings as asettings
from app.core.auth import get_current_user
from app.database import get_session
from app.models import AssistantConversation, AssistantFact, AssistantMessage, AssistantPlan, User

router = APIRouter(prefix="/assistant", tags=["assistant"])


# ── settings ──────────────────────────────────────────────────────────────────

def _settings_view(s: asettings.AssistantSettings) -> dict:
    return {
        "enabled": s.enabled,
        "provider": s.provider,
        "model": s.model,
        "base_url": s.base_urls.get(s.provider, ""),
        "default_base_url": asettings.DEFAULT_BASE_URLS[s.provider],
        "egress": s.egress,
        "small_mode": s.small_mode,
        "small_active": s.small,
        "web_search": s.web_search,
        "voice_mode": s.voice_mode,
        "stt_url": s.stt_url,
        "stt_model": s.stt_model,
        "voice_lang": s.voice_lang,
        "monthly_token_limit": s.monthly_token_limit,
        "has_key": {p: bool(s.keys.get(p)) for p in asettings.PROVIDERS},
        "usage": s.month_usage(),
        "providers": list(asettings.PROVIDERS),
    }


class SettingsPatch(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    egress: Optional[str] = None
    small_mode: Optional[str] = None
    web_search: Optional[bool] = None
    voice_mode: Optional[str] = None
    stt_url: Optional[str] = None
    stt_model: Optional[str] = None
    voice_lang: Optional[str] = None
    monthly_token_limit: Optional[int] = None
    # For the provider being saved; "" removes the stored key.
    api_key: Optional[str] = None


@router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_session)):
    return _settings_view(await asettings.load(session))


@router.patch("/settings")
async def patch_settings(body: SettingsPatch, session: AsyncSession = Depends(get_session)):
    s = await asettings.load(session)
    if body.provider is not None:
        if body.provider not in asettings.PROVIDERS:
            raise HTTPException(400, f"provider must be one of {', '.join(asettings.PROVIDERS)}")
        if body.provider != s.provider and body.model is None:
            s.model = ""  # model ids differ between providers
        s.provider = body.provider
    if body.model is not None:
        s.model = body.model.strip()
    if body.base_url is not None:
        url = body.base_url.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(400, "the server URL must start with http:// or https://")
        if url:
            s.base_urls[s.provider] = url
        else:
            s.base_urls.pop(s.provider, None)
    if body.egress is not None:
        if body.egress not in asettings.EGRESS:
            raise HTTPException(400, "egress must be 'active' or 'direct'")
        s.egress = body.egress
    if body.small_mode is not None:
        if body.small_mode not in asettings.SMALL_MODES:
            raise HTTPException(400, "small_mode must be auto, on or off")
        s.small_mode = body.small_mode
    if body.web_search is not None:
        s.web_search = body.web_search
    if body.voice_mode is not None:
        from app.assistant.voice import VOICE_MODES
        if body.voice_mode not in VOICE_MODES:
            raise HTTPException(400, f"voice_mode must be one of {', '.join(VOICE_MODES)}")
        s.voice_mode = body.voice_mode
    if body.stt_url is not None:
        url = body.stt_url.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(400, "the speech server URL must start with http:// or https://")
        s.stt_url = url
    if body.stt_model is not None:
        s.stt_model = body.stt_model.strip()[:100]
    if body.voice_lang is not None:
        s.voice_lang = body.voice_lang.strip().lower()[:5]
    if body.monthly_token_limit is not None:
        s.monthly_token_limit = max(0, int(body.monthly_token_limit))
    if body.api_key is not None:
        key = body.api_key.strip()
        if key:
            s.keys[s.provider] = key
        else:
            s.keys.pop(s.provider, None)
    if body.enabled is not None:
        s.enabled = body.enabled
    await asettings.save(session, s)
    return _settings_view(s)


@router.post("/test")
async def test_connection(session: AsyncSession = Depends(get_session)):
    from app.assistant.providers import probe
    return await probe(await asettings.load(session))


@router.get("/models")
async def list_models(session: AsyncSession = Depends(get_session)):
    from app.assistant.providers import ProviderError, make_provider
    s = await asettings.load(session)
    if s.provider != "anthropic" and not s.model:
        s.model = "_listing"  # make_provider wants a model; listing does not use it
    try:
        p = make_provider(s)
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from None
    try:
        return await p.list_models()
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        await p.close()


@router.post("/transcribe")
async def transcribe(audio: UploadFile = File(...), language: str = Form(""),
                     session: AsyncSession = Depends(get_session)):
    """Speech to text for the mic button -> {"text": ...}. The text goes into
    the input box; nothing is sent to the chat from here."""
    from app.assistant import voice
    await _require_enabled(session)
    data = await audio.read(voice.MAX_AUDIO_BYTES + 1)
    try:
        text = await voice.transcribe(await asettings.load(session), data, audio.filename or "speech.webm",
                                      audio.content_type or "audio/webm", language)
    except voice.VoiceError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"text": text}


@router.get("/discover")
async def discover_local(session: AsyncSession = Depends(get_session)):
    """OpenAI-compatible model servers found in the LAN."""
    from app.assistant.discover import discover
    return await discover(session)


# ── facts ─────────────────────────────────────────────────────────────────────

class FactBody(BaseModel):
    text: str


@router.get("/facts")
async def list_facts(session: AsyncSession = Depends(get_session)):
    from app.assistant.memory import list_facts as _list
    return [{"id": f.id, "text": f.text, "created_at": f.created_at} for f in await _list(session)]


@router.post("/facts", status_code=201)
async def add_fact(body: FactBody, session: AsyncSession = Depends(get_session)):
    from app.assistant.memory import add_fact as _add
    fact, why = await _add(session, body.text)
    if fact is None:
        raise HTTPException(400, why)
    return {"id": fact.id, "text": fact.text, "created_at": fact.created_at}


@router.delete("/facts/{fact_id}", status_code=204)
async def delete_fact(fact_id: int, session: AsyncSession = Depends(get_session)):
    from app.assistant.memory import delete_fact as _del
    if not await _del(session, fact_id):
        raise HTTPException(404, "Fact not found")


# ── conversations ─────────────────────────────────────────────────────────────

async def _require_enabled(session: AsyncSession) -> None:
    if not (await asettings.load(session)).enabled:
        raise HTTPException(409, "The assistant is turned off (Settings -> Assistant)")


@router.get("/conversations")
async def list_conversations(session: AsyncSession = Depends(get_session)):
    rows = (await session.exec(select(AssistantConversation)
                               .order_by(AssistantConversation.updated_at.desc()))).all()
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in rows]


@router.post("/conversations", status_code=201)
async def create_conversation(session: AsyncSession = Depends(get_session)):
    await _require_enabled(session)
    c = AssistantConversation()
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return {"id": c.id, "title": c.title, "updated_at": c.updated_at}


@router.get("/conversations/{cid}")
async def get_conversation(cid: int, session: AsyncSession = Depends(get_session)):
    from app.assistant.agent import plan_view
    from app.assistant.redact import Redactor
    conv = await session.get(AssistantConversation, cid)
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    red = Redactor(json.loads(conv.redaction_map or "{}"))
    rows = (await session.exec(select(AssistantMessage).where(AssistantMessage.conversation_id == cid)
                               .order_by(AssistantMessage.id))).all()
    plans = {p.id: plan_view(p) for p in (await session.exec(
        select(AssistantPlan).where(AssistantPlan.conversation_id == cid))).all()}
    from app.assistant.agent import _short_args
    items = []
    for r in rows:
        m = json.loads(r.content)
        if m["role"] == "user":
            item = {"kind": "note" if m.get("note") else "user", "text": red.reveal(m.get("content") or "")}
            if m.get("images"):
                item["images"] = [f"data:{im.get('media_type')};base64,{im.get('data')}" for im in m["images"]]
            items.append(item)
            if m.get("plan_id") in plans:  # pasted server credentials -> plan made by the box
                items.append({"kind": "plan", "plan": plans[m["plan_id"]]})
        elif m["role"] == "assistant":
            if (m.get("content") or "").strip():
                items.append({"kind": "assistant", "text": red.reveal(m["content"])})
            for tc in m.get("tool_calls") or []:
                items.append({"kind": "tool", "name": tc.get("name"),
                              "summary": red.reveal(_short_args(tc.get("name") or "", tc.get("arguments") or {}))})
        elif m["role"] == "tool" and m.get("plan_id") in plans:
            items.append({"kind": "plan", "plan": plans[m["plan_id"]]})
    return {"id": conv.id, "title": conv.title, "items": items}


@router.delete("/conversations/{cid}", status_code=204)
async def delete_conversation(cid: int, session: AsyncSession = Depends(get_session)):
    conv = await session.get(AssistantConversation, cid)
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    for model in (AssistantMessage, AssistantPlan):
        for row in (await session.exec(select(model).where(model.conversation_id == cid))).all():
            await session.delete(row)
    await session.delete(conv)
    await session.commit()


def _stream(gen):
    async def body():
        async for ev in gen:
            yield json.dumps(ev, ensure_ascii=False, default=str) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")


class ImageIn(BaseModel):
    media_type: str
    data: str  # base64, no data: prefix


class SendBody(BaseModel):
    text: str = ""
    images: List[ImageIn] = []


@router.post("/conversations/{cid}/messages")
async def send_message(cid: int, body: SendBody, session: AsyncSession = Depends(get_session),
                       user: User = Depends(get_current_user)):
    """Streams NDJSON events: message, tool, tool_done, plan, usage, error, done."""
    from app.assistant.agent import run_turn
    await _require_enabled(session)
    if await session.get(AssistantConversation, cid) is None:
        raise HTTPException(404, "Conversation not found")
    from app.assistant.agent import MAX_IMAGE_BYTES, MAX_IMAGES
    text = (body.text or "").strip()
    if not text and not body.images:
        raise HTTPException(400, "Empty message")
    if len(body.images) > MAX_IMAGES:
        raise HTTPException(400, f"At most {MAX_IMAGES} screenshots per message")
    images = []
    for im in body.images:
        if im.media_type not in _IMAGE_TYPES:
            raise HTTPException(400, "Screenshots must be PNG, JPEG, WebP or GIF")
        if len(im.data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Screenshot too large")
        images.append({"media_type": im.media_type, "data": im.data})
    return _stream(run_turn(cid, user.username, user_text=text[:20000] or "(screenshot)",
                            images=images or None))


class MacroBody(BaseModel):
    name: str
    args: dict = {}


@router.get("/macros")
async def list_macros():
    """Macro specs (name, description, JSON-schema parameters) for the forms."""
    from app.assistant.macros import MACRO_SPECS
    return MACRO_SPECS


@router.post("/conversations/{cid}/macro")
async def run_macro_form(cid: int, body: MacroBody, session: AsyncSession = Depends(get_session),
                         user: User = Depends(get_current_user)):
    """Run a macro from a form (no model call to pick it). Streams like a
    message; the model only phrases questions and reports afterwards."""
    from app.assistant.agent import run_form, run_turn
    from app.assistant.macros import MACRO_NAMES
    await _require_enabled(session)
    if body.name not in MACRO_NAMES:
        raise HTTPException(400, f"Unknown macro {body.name!r}")
    if await session.get(AssistantConversation, cid) is None:
        raise HTTPException(404, "Conversation not found")

    async def gen():
        reply = False
        async for ev in run_form(cid, user.username, body.name, body.args):
            if ev["type"] == "_reply":
                reply = ev["needed"]
            else:
                yield ev
        if reply:
            async for ev in run_turn(cid, user.username):
                yield ev
        else:
            yield {"type": "done"}
    return _stream(gen())


# ── plans ─────────────────────────────────────────────────────────────────────

@router.post("/plans/{plan_id}/{decision}")
async def decide_plan(plan_id: int, decision: str, session: AsyncSession = Depends(get_session),
                      user: User = Depends(get_current_user)):
    """approve -> run the plan; reject -> drop it. Then the model is told and
    answers; the response streams like a message: a `plan` event first.
    undo -> revert an executed plan (no model turn)."""
    from app.assistant.agent import execute_plan, follow_jobs, run_turn, store_rejection, undo_plan
    if decision not in ("approve", "reject", "undo"):
        raise HTTPException(404, "Unknown decision")
    await _require_enabled(session)
    plan = await session.get(AssistantPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "Plan not found")
    if decision == "undo":
        if plan.status != "executed":
            raise HTTPException(409, f"Only an executed plan can be undone (this one is {plan.status})")
        view = await undo_plan(plan_id, user.username)
        if "undo_ok" not in view:
            raise HTTPException(409, "Nothing in this plan can be undone")
        return view
    if plan.status != "pending":
        raise HTTPException(409, f"Plan is already {plan.status}")
    cid = plan.conversation_id
    view = await execute_plan(plan_id, user.username, approve=(decision == "approve"))

    async def gen():
        yield {"type": "plan", "plan": view}
        if decision == "approve" and view["status"] in ("executed", "failed"):
            async for ev in follow_jobs(view):
                yield ev
            async for ev in run_turn(cid, user.username, note_plan=view):
                yield ev
        else:
            # The model is told, but does not need to answer a rejection.
            await store_rejection(cid, plan_id)
            yield {"type": "done"}
    return _stream(gen())
