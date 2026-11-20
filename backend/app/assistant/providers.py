"""Model providers behind one small interface.

Internal message format (what is stored and replayed):
  {"role": "user", "content": str, "images": [{"media_type", "data" (base64)}]}  # images optional
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict}],
   "raw": {"provider", "model", "content": [...]}}   # provider-native blocks, optional
  {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}

OpenRouter, OpenAI and local servers speak the OpenAI chat-completions API and
share one adapter; Anthropic goes through its official SDK.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from app.assistant.settings import AssistantSettings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(180.0, connect=15.0)
_REFERER = "https://github.com/DaveBugg/PiTun"


class ProviderError(Exception):
    """Safe to show: never contains the API key."""


@dataclass
class ModelTurn:
    text: str = ""
    tool_calls: List[dict] = field(default_factory=list)
    raw: Optional[dict] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    stop: str = ""


def _proxy(s: AssistantSettings) -> Optional[str]:
    if s.provider == "local" or s.egress != "active":
        return None
    from app.config import settings as app_settings
    return f"socks5://127.0.0.1:{app_settings.socks_port}"


def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {"_raw": v}
    except ValueError:
        return {"_invalid_json": str(raw)[:2000]}


def _http_error(exc: Exception) -> ProviderError:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        try:
            body = exc.response.json()
            msg = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        except ValueError:
            msg = None
        hint = {401: "the API key was rejected", 402: "the account is out of credit",
                403: "access denied (the provider may block this country: try another egress)",
                404: "model or endpoint not found", 429: "rate limited, try again shortly"}.get(code, "")
        return ProviderError(f"HTTP {code}" + (f": {hint}" if hint else "") + (f" ({str(msg)[:200]})" if msg else ""))
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError("the model did not answer in time")
    if isinstance(exc, httpx.ConnectError):
        return ProviderError("could not connect to the provider")
    return ProviderError(f"{type(exc).__name__}: {str(exc)[:200]}")


# ── OpenAI-compatible (OpenRouter, OpenAI, local) ────────────────────────────

class OpenAICompatProvider:
    def __init__(self, s: AssistantSettings):
        if not s.base_url:
            raise ProviderError("set the server URL for the local model")
        if s.provider != "local" and not s.api_key:
            raise ProviderError("no API key saved for this provider")
        self.s = s
        headers = {"Content-Type": "application/json"}
        if s.api_key:
            headers["Authorization"] = f"Bearer {s.api_key}"
        if s.provider == "openrouter":
            headers["HTTP-Referer"] = _REFERER
            headers["X-Title"] = "PiTun"
        self.client = httpx.AsyncClient(base_url=s.base_url, headers=headers,
                                        timeout=_TIMEOUT, proxy=_proxy(s))

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _messages(system: str, messages: List[dict]) -> List[dict]:
        out: List[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                if m.get("images"):
                    parts: List[dict] = [{"type": "text", "text": m.get("content") or ""}]
                    for im in m["images"]:
                        parts.append({"type": "image_url", "image_url": {
                            "url": f"data:{im.get('media_type') or 'image/png'};base64,{im.get('data') or ''}"}})
                    out.append({"role": "user", "content": parts})
                else:
                    out.append({"role": "user", "content": m.get("content") or ""})
            elif m["role"] == "assistant":
                msg: Dict[str, Any] = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [{
                        "id": tc["id"], "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments") or {})},
                    } for tc in m["tool_calls"]]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m.get("content") or ""})
        return out

    async def chat(self, system: str, messages: List[dict], tools: List[dict]) -> ModelTurn:
        body: Dict[str, Any] = {
            "model": self.s.model,
            "messages": self._messages(system, messages),
        }
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
            body["tool_choice"] = "auto"
        if self.s.provider == "openrouter":
            body["usage"] = {"include": True}
        if self.s.provider == "local":
            body["temperature"] = 0
        try:
            r = await self.client.post("/chat/completions", json=body)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise _http_error(exc) from None
        if isinstance(data.get("error"), dict):
            raise ProviderError(str(data["error"].get("message") or data["error"])[:300])
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        turn = ModelTurn(
            text=msg.get("content") or "",
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cost=float(usage.get("cost") or 0.0),
            stop=choice.get("finish_reason") or "",
        )
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            turn.tool_calls.append({
                "id": tc.get("id") or f"call_{i}",
                "name": fn.get("name") or "",
                "arguments": _parse_args(fn.get("arguments")),
            })
        return turn

    async def complete_json(self, system: str, messages: List[dict], schema: dict) -> Optional[ModelTurn]:
        """One tool-less call whose answer must be JSON matching `schema`
        (json_schema where the server supports it, else json_object). None
        when the server refuses both: the caller falls back to tool calls."""
        formats = [{"type": "json_schema", "json_schema": {"name": "answer", "strict": False, "schema": schema}},
                   {"type": "json_object"}]
        for fmt in formats:
            body: Dict[str, Any] = {"model": self.s.model, "messages": self._messages(system, messages),
                                    "response_format": fmt, "temperature": 0}
            try:
                r = await self.client.post("/chat/completions", json=body)
            except httpx.HTTPError as exc:
                raise _http_error(exc) from None
            if r.status_code in (400, 404, 415, 422, 501):
                continue
            try:
                r.raise_for_status()
                data = r.json()
            except Exception as exc:  # noqa: BLE001
                raise _http_error(exc) from None
            choice = (data.get("choices") or [{}])[0]
            usage = data.get("usage") or {}
            return ModelTurn(text=(choice.get("message") or {}).get("content") or "",
                             input_tokens=int(usage.get("prompt_tokens") or 0),
                             output_tokens=int(usage.get("completion_tokens") or 0),
                             cost=float(usage.get("cost") or 0.0), stop=choice.get("finish_reason") or "")
        return None

    async def list_models(self) -> List[dict]:
        try:
            r = await self.client.get("/models")
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise _http_error(exc) from None
        out = []
        for m in data.get("data") or data.get("models") or []:
            mid = m.get("id") or m.get("name")
            if not mid:
                continue
            item = {"id": mid, "name": m.get("name") or mid}
            if self.s.provider == "openrouter":
                params = m.get("supported_parameters") or []
                mods = (m.get("architecture") or {}).get("input_modalities") or []
                pricing = m.get("pricing") or {}
                item.update(
                    context=m.get("context_length"),
                    tools="tools" in params,
                    vision="image" in mods,
                    prompt_price=_per_million(pricing.get("prompt")),
                    completion_price=_per_million(pricing.get("completion")),
                )
            out.append(item)
        return sorted(out, key=lambda x: x["id"])


def _per_million(v) -> Optional[float]:
    try:
        return round(float(v) * 1_000_000, 4)
    except (TypeError, ValueError):
        return None


# ── Anthropic (official SDK) ─────────────────────────────────────────────────

# Models where the server-side refusal fallback is enabled by default.
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-fable-5-2", "claude-fable-5-1",
                    "claude-opus-5", "claude-sonnet-5-5")
_EFFORT_PREFIXES = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable",
                    "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6")
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


class AnthropicProvider:
    def __init__(self, s: AssistantSettings):
        import anthropic
        if not s.api_key:
            raise ProviderError("no API key saved for Anthropic")
        self.s = s
        self.model = s.model or DEFAULT_ANTHROPIC_MODEL
        proxy = _proxy(s)
        kwargs: Dict[str, Any] = {"api_key": s.api_key, "max_retries": 2, "timeout": 180.0}
        if s.base_urls.get("anthropic"):
            kwargs["base_url"] = s.base_urls["anthropic"].removesuffix("/v1")
        if proxy:
            kwargs["http_client"] = anthropic.DefaultAsyncHttpxClient(proxy=proxy)
        self.client = anthropic.AsyncAnthropic(**kwargs)

    async def close(self) -> None:
        await self.client.close()

    def _messages(self, messages: List[dict]) -> List[dict]:
        out: List[dict] = []
        pending_results: List[dict] = []

        def flush():
            if pending_results:
                out.append({"role": "user", "content": list(pending_results)})
                pending_results.clear()

        for m in messages:
            if m["role"] == "tool":
                pending_results.append({
                    "type": "tool_result", "tool_use_id": m["tool_call_id"],
                    "content": m.get("content") or "", "is_error": bool(m.get("is_error")),
                })
                continue
            flush()
            if m["role"] == "user":
                if m.get("images"):
                    blocks = [{"type": "image", "source": {
                        "type": "base64", "media_type": im.get("media_type") or "image/png",
                        "data": im.get("data") or ""}} for im in m["images"]]
                    blocks.append({"type": "text", "text": m.get("content") or "(screenshot)"})
                    out.append({"role": "user", "content": blocks})
                else:
                    out.append({"role": "user", "content": m.get("content") or ""})
            elif m["role"] == "assistant":
                raw = m.get("raw") or {}
                if raw.get("provider") == "anthropic" and raw.get("model") == self.model and raw.get("content"):
                    # Thinking blocks must go back unchanged on the same model.
                    out.append({"role": "assistant", "content": raw["content"]})
                    continue
                blocks: List[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"],
                                   "input": tc.get("arguments") or {}})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no reply)"}]})
        flush()
        return out

    async def chat(self, system: str, messages: List[dict], tools: List[dict]) -> ModelTurn:
        import anthropic
        params: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": self._messages(messages),
        }
        if tools:
            params["tools"] = [{"name": t["name"], "description": t["description"],
                                "input_schema": t["parameters"]} for t in tools]
        if self.model.startswith(_EFFORT_PREFIXES):
            params["output_config"] = {"effort": "medium"}
        try:
            if self.model.startswith(_FALLBACK_MODELS):
                resp = await self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
            else:
                resp = await self.client.messages.create(**params)
        except anthropic.AuthenticationError:
            raise ProviderError("the API key was rejected") from None
        except anthropic.PermissionDeniedError:
            raise ProviderError("access denied (the provider may block this country: try another egress)") from None
        except anthropic.NotFoundError:
            raise ProviderError(f"model '{self.model}' not found") from None
        except anthropic.RateLimitError:
            raise ProviderError("rate limited, try again shortly") from None
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"HTTP {exc.status_code}: {str(exc.message)[:200]}") from None
        except anthropic.APIConnectionError:
            raise ProviderError("could not connect to the provider") from None

        turn = ModelTurn(stop=resp.stop_reason or "")
        if resp.stop_reason == "refusal":
            turn.text = "The model declined this request."
        keep = []
        for b in resp.content:
            if b.type == "text":
                turn.text += b.text
            elif b.type == "tool_use":
                turn.tool_calls.append({"id": b.id, "name": b.name, "arguments": _parse_args(b.input)})
            if b.type in ("text", "tool_use", "thinking", "redacted_thinking"):
                keep.append(b.to_dict())
        turn.raw = {"provider": "anthropic", "model": self.model, "content": keep}
        u = resp.usage
        turn.input_tokens = int((u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
                                + (getattr(u, "cache_creation_input_tokens", 0) or 0))
        turn.output_tokens = int(u.output_tokens or 0)
        return turn

    async def list_models(self) -> List[dict]:
        import anthropic
        try:
            out = []
            async for m in self.client.models.list():
                out.append({"id": m.id, "name": getattr(m, "display_name", None) or m.id,
                            "tools": True, "vision": True,
                            "context": getattr(m, "max_input_tokens", None)})
            return out
        except anthropic.AuthenticationError:
            raise ProviderError("the API key was rejected") from None
        except anthropic.APIError as exc:
            raise ProviderError(f"{type(exc).__name__}: {str(exc)[:200]}") from None


def make_provider(s: AssistantSettings):
    if s.provider == "anthropic":
        return AnthropicProvider(s)
    if not s.model:
        raise ProviderError("pick a model first")
    return OpenAICompatProvider(s)


async def probe(s: AssistantSettings) -> dict:
    """Reachability + whether the model really calls tools."""
    try:
        p = make_provider(s)
    except ProviderError as exc:
        return {"ok": False, "error": str(exc), "tool_calling": False}
    try:
        tool = {"name": "report_number", "description": "Report a number.",
                "parameters": {"type": "object", "properties": {"n": {"type": "integer"}},
                               "required": ["n"]}}
        turn = await p.chat(
            "You are a test harness. Always answer by calling the provided tool.",
            [{"role": "user", "content": "Call report_number with n = 42."}],
            [tool],
        )
        called = any(tc["name"] == "report_number" for tc in turn.tool_calls)
        return {"ok": True, "tool_calling": called, "error": None,
                "note": None if called else "the model answered but did not call the tool; "
                                             "it will struggle with actions"}
    except ProviderError as exc:
        return {"ok": False, "error": str(exc), "tool_calling": False}
    finally:
        await p.close()
