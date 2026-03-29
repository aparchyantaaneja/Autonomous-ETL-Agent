"""Anthropic Claude LLM client with retry logic, structured output, and cost tracking."""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic
import structlog
from pydantic import BaseModel, ValidationError

logger = structlog.get_logger(__name__)


class LLMResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    latency_ms: float


class ClaudeClient:
    """Thin wrapper around the Anthropic SDK.

    Supports:
    - Automatic retry with exponential back-off
    - Structured JSON output validated against a Pydantic model
    - Per-call token usage logging for cost tracking
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 8192,
        max_retries: int = 3,
        initial_backoff_s: float = 2.0,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.initial_backoff_s = initial_backoff_s
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> LLMResponse:
        """Send a chat completion request and return the raw text response."""
        last_exc: Exception | None = None
        backoff = self.initial_backoff_s

        for attempt in range(1, self.max_retries + 1):
            try:
                return self._call(system_prompt, user_prompt, temperature)
            except anthropic.RateLimitError as exc:
                last_exc = exc
                logger.warning(
                    "rate_limit_hit",
                    attempt=attempt,
                    retry_in_s=backoff,
                )
            except anthropic.APIStatusError as exc:
                # 5xx — retryable; 4xx (except 429) — not retryable
                if exc.status_code < 500:
                    raise
                last_exc = exc
                logger.warning(
                    "api_error_retrying",
                    attempt=attempt,
                    status_code=exc.status_code,
                    retry_in_s=backoff,
                )

            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

        raise RuntimeError(
            f"LLM call failed after {self.max_retries} attempts"
        ) from last_exc

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[BaseModel],
        temperature: float = 0.1,
    ) -> BaseModel:
        """Complete a request and parse the response as a Pydantic model.

        Retries once with an amended prompt if JSON parsing fails.
        """
        response = self.complete(system_prompt, user_prompt, temperature)
        raw = response.content.strip()

        # Strip markdown code fences if the model added them
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0].strip()

        try:
            data = json.loads(raw)
            return output_model.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "json_parse_failed_retrying",
                error=str(exc),
                raw_preview=raw[:200],
            )
            # Ask the model to fix its own output
            fix_prompt = (
                f"The following output is not valid JSON or does not match the schema.\n\n"
                f"Output:\n{raw}\n\n"
                f"Error: {exc}\n\n"
                f"Return only the corrected JSON, no explanation."
            )
            response2 = self.complete(system_prompt, fix_prompt, temperature=0.0)
            raw2 = response2.content.strip()
            if raw2.startswith("```"):
                raw2 = raw2.split("```", 2)[1]
                if raw2.startswith("json"):
                    raw2 = raw2[4:]
                raw2 = raw2.rsplit("```", 1)[0].strip()
            data2 = json.loads(raw2)
            return output_model.model_validate(data2)

    @property
    def total_tokens_used(self) -> dict[str, int]:
        return {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "total": self._total_input_tokens + self._total_output_tokens,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ) -> LLMResponse:
        start = time.monotonic()
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        latency_ms = (time.monotonic() - start) * 1000

        content = message.content[0].text
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens

        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens

        logger.info(
            "llm_call_complete",
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=round(latency_ms, 1),
        )

        return LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self.model,
            latency_ms=round(latency_ms, 1),
        )


def build_claude_client(settings: Any) -> ClaudeClient:
    """Construct a ClaudeClient from application settings."""
    return ClaudeClient(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        max_retries=settings.llm_max_retries,
    )
