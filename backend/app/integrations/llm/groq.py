"""
Provider Gateway for Groq's LLM chat completions API (llm category). Only
file allowed to call this vendor's API directly.
"""

import json

import httpx

from app.core.config import settings

_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
_MODELS_URL = "https://api.groq.com/openai/v1/models"
_MODEL = "llama-3.1-8b-instant"
MODEL_VERSION = f"groq/{_MODEL}"


def health_check() -> dict:
    """Real reachability check - lists available models, the cheapest
    authenticated call Groq offers (no completion tokens spent)."""
    if not settings.groq_api_key:
        return {"configured": False, "ok": False, "detail": None}
    try:
        response = httpx.get(
            _MODELS_URL, headers={"Authorization": f"Bearer {settings.groq_api_key}"}, timeout=10.0
        )
        response.raise_for_status()
        return {"configured": True, "ok": True, "detail": None}
    except httpx.HTTPError as e:
        return {"configured": True, "ok": False, "detail": str(e)}

_SUMMARY_SYSTEM_PROMPT = (
    "You analyze voicemail and call transcripts for a business communications "
    'platform. Return ONLY a JSON object with these keys: "summary" (a concise '
    '1-3 sentence summary of the caller\'s intent), "language" (the ISO 639-1 code '
    'of the transcript\'s language, e.g. "en", or null if you cannot tell), '
    '"urgency" (one of "low", "medium", "high"), "action_items" (a JSON array of '
    "short strings, one per concrete action requested or implied - empty array if "
    'none), "suggested_follow_up" (one sentence suggesting what the business should '
    'do next, or null if nothing beyond acknowledging the message). Never invent '
    "details not present in the transcript."
)

_QUALIFICATION_SYSTEM_PROMPT = (
    "Extract caller qualification data from a business phone receptionist transcript. "
    'Return ONLY a JSON object with these keys: "name" (string or null), "company" '
    '(string or null), "reason" (short string, a few words, summarizing why they '
    'called, or null), "summary" (a single, complete, natural-language sentence '
    "narrating the call for a business owner reading a call log — e.g. \"Jordan Lee "
    "from Acme Corp called about a delayed shipment and asked for a callback today.\" "
    'Always a full sentence, never a fragment, or null if there is nothing to summarize), '
    '"urgency" (one of "low", "medium", "high"), "callback_preference" (string or '
    "null, e.g. a phone number or \"email\" if mentioned). Never invent information "
    "not present in the transcript. Never make commitments, quote prices, or give "
    "legal/financial/medical advice — you are only extracting structured data."
)


class LLMError(Exception):
    """Raised instead of letting an httpx/vendor-specific exception escape this module."""


def extract_conversation_summary(transcript: str) -> dict:
    """Structured call/voicemail intelligence (Architecture doc §2.3 Phase 1
    AI: "language detection... AI-generated action extraction"; Roadmap §2:
    "summary, language detection, suggested follow-up"). Returns a dict with
    summary/language/urgency/action_items/suggested_follow_up - same
    structured-JSON pattern as extract_receptionist_qualification, rather
    than one prose blob."""
    if not settings.groq_api_key:
        raise LLMError("Groq API key is not configured")

    try:
        response = httpx.post(
            _CHAT_COMPLETIONS_URL,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": _MODEL,
                "messages": [
                    {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": transcript},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"Groq summarization request failed: {e}") from e

    content = response.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"Groq returned non-JSON summary output: {content!r}") from e


def extract_receptionist_qualification(transcript: str) -> dict:
    if not settings.groq_api_key:
        raise LLMError("Groq API key is not configured")

    try:
        response = httpx.post(
            _CHAT_COMPLETIONS_URL,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": _MODEL,
                "messages": [
                    {"role": "system", "content": _QUALIFICATION_SYSTEM_PROMPT},
                    {"role": "user", "content": transcript},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"Groq qualification extraction failed: {e}") from e

    content = response.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"Groq returned non-JSON qualification output: {content!r}") from e
