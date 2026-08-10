"""Secondary LLM vendor (OpenAI) behind llm_failover_enabled. Real API calls,
not a mock - but NOT tested against a live account, since no real OpenAI
credentials exist yet. Wire OPENAI_API_KEY in .env and flip
LLM_FAILOVER_ENABLED=true to activate. Callers in groq.py never change,
since it dispatches to this module by function name only.

Groq's chat completions API is itself OpenAI-compatible (groq.py's
_CHAT_COMPLETIONS_URL is literally an /openai/v1/... path against Groq's
host), so this secondary reuses the exact same request/response shape and
prompts against the real OpenAI endpoint - the cleanest cross-vendor match
of any category in this codebase.
"""

import json

import httpx

from app.core.config import settings
from app.integrations.llm.groq import LLMError

_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
_MODEL = "gpt-4o-mini"

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


def _require_credentials() -> None:
    if not settings.openai_api_key:
        raise LLMError("Secondary LLM provider (OpenAI) is not configured - set OPENAI_API_KEY")


def _complete(system_prompt: str, transcript: str, error_label: str) -> dict:
    _require_credentials()
    try:
        response = httpx.post(
            _CHAT_COMPLETIONS_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": _MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": transcript},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"OpenAI {error_label} request failed: {e}") from e

    content = response.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"OpenAI returned non-JSON {error_label} output: {content!r}") from e


def extract_conversation_summary(transcript: str) -> dict:
    return _complete(_SUMMARY_SYSTEM_PROMPT, transcript, "summarization")


def extract_receptionist_qualification(transcript: str) -> dict:
    return _complete(_QUALIFICATION_SYSTEM_PROMPT, transcript, "qualification extraction")
