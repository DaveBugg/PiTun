"""Speech to text for the chat's mic button.

Modes (setting `voice_mode`):
- browser  — the browser's own speech recognition; nothing reaches the box.
- provider — `/audio/transcriptions` of the chat provider (OpenAI, or a local
             OpenAI-compatible server that also serves speech).
- server   — a separate OpenAI-compatible whisper server in the LAN
             (`stt_url`, no key), e.g. on the machine that runs the model.
- off
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.assistant.settings import AssistantSettings

VOICE_MODES = ("browser", "provider", "server", "off")
MAX_AUDIO_BYTES = 10_000_000
DEFAULT_STT_MODEL = {"openai": "whisper-1", "local": "whisper-1"}
# Providers that have no speech endpoint.
NO_STT = ("openrouter", "anthropic")


class VoiceError(Exception):
    pass


def target(s: AssistantSettings) -> tuple[str, str, Optional[str], str]:
    """-> (base url, api key, proxy, model) for the transcription call."""
    if s.voice_mode == "server":
        if not s.stt_url:
            raise VoiceError("set the speech server URL in Settings -> Assistant")
        return s.stt_url.rstrip("/"), "", None, s.stt_model or "whisper-1"
    if s.voice_mode == "provider":
        if s.provider in NO_STT:
            raise VoiceError(f"{s.provider} has no speech-to-text; use the browser or a speech server")
        if s.provider != "local" and not s.api_key:
            raise VoiceError("no API key saved for this provider")
        proxy = None
        if s.provider != "local" and s.egress == "active":
            proxy = s.web_proxy
        return s.base_url, s.api_key, proxy, s.stt_model or DEFAULT_STT_MODEL.get(s.provider, "whisper-1")
    raise VoiceError("speech-to-text on the box is off (voice mode is browser or off)")


async def transcribe(s: AssistantSettings, audio: bytes, filename: str, content_type: str,
                     language: str = "") -> str:
    if not audio:
        raise VoiceError("empty recording")
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceError("recording too long")
    base, key, proxy, model = target(s)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = {"model": model, "response_format": "json"}
    lang = (language or s.voice_lang or "").strip().lower()
    if lang and lang != "auto":
        data["language"] = lang[:5]
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0), proxy=proxy) as c:
        try:
            r = await c.post(f"{base}/audio/transcriptions", headers=headers, data=data,
                             files={"file": (filename or "speech.webm", audio, content_type or "audio/webm")})
        except httpx.HTTPError as exc:
            raise VoiceError(f"speech server unreachable: {type(exc).__name__}") from None
    if r.status_code >= 400:
        detail = ""
        try:
            body = r.json()
            err = body.get("error")
            detail = (err.get("message") if isinstance(err, dict) else err) or body.get("detail") or ""
        except ValueError:
            detail = r.text[:200]
        raise VoiceError(f"HTTP {r.status_code}" + (f": {str(detail)[:200]}" if detail else ""))
    try:
        body = r.json()
        text = body.get("text") if isinstance(body, dict) else None
    except ValueError:
        text = r.text
    return (text or "").strip()
