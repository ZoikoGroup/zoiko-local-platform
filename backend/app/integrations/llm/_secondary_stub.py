"""Stand-in for a second LLM vendor (e.g. OpenAI) behind llm_failover_enabled.
No real second-vendor account exists yet - every function raises a clearly
labeled error instead of silently no-opping or inventing output.
"""

from app.integrations.llm.groq import LLMError

_NOT_CONFIGURED = (
    "secondary LLM provider not configured - set LLM_SECONDARY_* credentials "
    "once a second vendor account exists"
)


def extract_conversation_summary(transcript: str) -> dict:
    raise LLMError(_NOT_CONFIGURED)


def extract_receptionist_qualification(transcript: str) -> dict:
    raise LLMError(_NOT_CONFIGURED)
