"""
Provider Gateway for Groq's LLM chat completions API (llm category). Only
file allowed to call this vendor's API directly.
"""

import json

import httpx

from app.core.config import settings

_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
_MODEL = "llama-3.1-8b-instant"
MODEL_VERSION = f"groq/{_MODEL}"

_SUMMARY_SYSTEM_PROMPT = (
    "You summarize voicemail and call transcripts for a business communications "
    "platform. Write a concise 1-3 sentence summary capturing the caller's intent, "
    "any action requested, and urgency if apparent. Do not invent details not in "
    "the transcript."
)

_QUALIFICATION_SYSTEM_PROMPT = (
    "Extract caller qualification data from a business phone receptionist transcript. "
    'Return ONLY a JSON object with these keys: "name" (string or null), "company" '
    '(string or null), "reason" (string summarizing why they called, or null), '
    '"urgency" (one of "low", "medium", "high"), "callback_preference" (string or '
    "null, e.g. a phone number or \"email\" if mentioned). Never invent information "
    "not present in the transcript. Never make commitments, quote prices, or give "
    "legal/financial/medical advice — you are only extracting structured data."
)


class LLMError(Exception):
    """Raised instead of letting an httpx/vendor-specific exception escape this module."""


def summarize_transcript(transcript: str) -> str:
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
            },
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"Groq summarization request failed: {e}") from e

    return response.json()["choices"][0]["message"]["content"]


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
