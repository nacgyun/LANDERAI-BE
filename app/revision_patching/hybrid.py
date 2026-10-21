"""PATCH-first revision generation with one classified retry and FULL fallback."""
from dataclasses import dataclass, field
from html import unescape
import json
import re
import time
from typing import Any, Callable

from pydantic import ValidationError

from app.config.settings import settings
from app.revision_patching.engine import ENGINE_VERSION, PatchError, apply_patch_plan
from app.revision_patching.prompt import PROMPT_VERSION, messages
from app.revision_patching.retry_policy import FULL_THRESHOLD, POLICY_VERSION, retry_feedback
from app.revision_patching.schema import PatchPlan
from app.schemas.landing_page import LandingPageGenerationResponse
from app.services.openai_service import get_openai_client, revise_landing_page


@dataclass
class RevisionGenerationResult:
    title: str
    html: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _title_from_html(source_html: str) -> str:
    match = re.search(r"<title\b[^>]*>(.*?)</title\s*>", source_html, re.I | re.S)
    if not match:
        return "Landing Page"
    title = re.sub(r"\s+", " ", unescape(match.group(1))).strip()
    return title[:200] or "Landing Page"


def _usage(response) -> tuple[int, int]:
    usage = response.usage
    return (usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0)


def _full_fallback(source_html: str, revision_prompt: str) -> tuple[LandingPageGenerationResponse, int, int]:
    return revise_landing_page(
        source_html=source_html,
        revision_prompt=revision_prompt,
        max_attempts=1,
    )


def revise_landing_page_hybrid(
    *,
    source_html: str,
    revision_prompt: str,
    client=None,
    applier: Callable = apply_patch_plan,
    full_generator: Callable = _full_fallback,
) -> tuple[RevisionGenerationResult, int, int]:
    """Generate and atomically apply PATCH, falling back from the original HTML.

    API and browser infrastructure errors are raised for SQS retry. Deterministic
    planning/application failures use the classified retry and fallback policy.
    """
    if not settings.REVISION_PATCH_ENABLED:
        generated, input_tokens, output_tokens = full_generator(source_html, revision_prompt)
        return (
            RevisionGenerationResult(
                title=generated.title,
                html=generated.html,
                metadata={
                    "initial_route": "FULL",
                    "final_route": "FULL",
                    "fallback_used": False,
                    "fallback_reason": "patch_disabled",
                    "patch_attempts": 0,
                    "full_attempts": 1,
                },
            ),
            input_tokens,
            output_tokens,
        )

    client = client or get_openai_client()
    started = time.perf_counter()
    total_input_tokens = 0
    total_output_tokens = 0
    attempts: list[dict[str, Any]] = []
    feedback = None
    fallback_reason = None

    for attempt_number in range(1, 3):
        sent = messages(source_html, revision_prompt)
        if feedback is not None:
            sent.extend([
                {"role": "assistant", "content": attempts[-1].get("raw_output") or "{}"},
                {
                    "role": "user",
                    "content": (
                        "Replan the ENTIRE PATCH against the ORIGINAL HTML. No partial changes "
                        "were committed. Fix this error without dropping requirements: "
                        + json.dumps(feedback, ensure_ascii=False)
                    ),
                },
            ])

        api_started = time.perf_counter()
        response = client.chat.completions.create(
            model=settings.OPENAI_LANDING_PAGE_MODEL,
            messages=sent,
            response_format={"type": "json_object"},
        )
        input_tokens, output_tokens = _usage(response)
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        choice = response.choices[0]
        raw_output = choice.message.content or ""
        detail: dict[str, Any] = {
            "attempt": attempt_number,
            "response_id": response.id,
            "model_used": response.model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "api_ms": round((time.perf_counter() - api_started) * 1000, 1),
            "raw_output": raw_output,
        }
        attempts.append(detail)

        if choice.finish_reason != "stop" or getattr(choice.message, "refusal", None):
            raise RuntimeError("PATCH model response was incomplete or refused")

        try:
            try:
                raw_plan = json.loads(raw_output)
            except (ValueError, TypeError):
                raw_plan = None
            if isinstance(raw_plan, dict) and isinstance(raw_plan.get("patches"), list):
                detail["patch_count"] = len(raw_plan["patches"])
                if detail["patch_count"] >= FULL_THRESHOLD:
                    raise PatchError("patch_count_threshold", f"PATCH count {detail['patch_count']} >= {FULL_THRESHOLD}")

            plan = PatchPlan.model_validate_json(raw_output)
            apply_started = time.perf_counter()
            applied = applier(
                source_html,
                plan,
                chromium_path=settings.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
            )
            detail["application_ms"] = round((time.perf_counter() - apply_started) * 1000, 1)
            return (
                RevisionGenerationResult(
                    title=_title_from_html(applied["html"]),
                    html=applied["html"],
                    metadata={
                        "initial_route": "PATCH",
                        "final_route": "PATCH",
                        "fallback_reason": None,
                        "fallback_used": False,
                        "patch_attempts": attempt_number,
                        "full_attempts": 0,
                        "patch_count": len(plan.patches),
                        "prompt_version": PROMPT_VERSION,
                        "engine_version": ENGINE_VERSION,
                        "retry_policy_version": POLICY_VERSION,
                        "generation_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                ),
                total_input_tokens,
                total_output_tokens,
            )
        except (ValidationError, PatchError) as exc:
            code = exc.code if isinstance(exc, PatchError) else "invalid_patch_schema"
            detail.update(failure_code=code, error=str(exc))
            feedback = retry_feedback(code, str(exc))
            if code == "patch_count_threshold" or feedback is None or attempt_number == 2:
                fallback_reason = code
                break

    full_started = time.perf_counter()
    generated, full_input_tokens, full_output_tokens = full_generator(source_html, revision_prompt)
    total_input_tokens += full_input_tokens
    total_output_tokens += full_output_tokens
    return (
        RevisionGenerationResult(
            title=generated.title,
            html=generated.html,
            metadata={
                "initial_route": "PATCH",
                "final_route": "FULL",
                "fallback_reason": fallback_reason,
                "fallback_used": True,
                "patch_attempts": len(attempts),
                "full_attempts": 1,
                "patch_count": attempts[-1].get("patch_count") if attempts else None,
                "prompt_version": PROMPT_VERSION,
                "engine_version": ENGINE_VERSION,
                "retry_policy_version": POLICY_VERSION,
                "full_generation_ms": round((time.perf_counter() - full_started) * 1000, 1),
                "generation_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        ),
        total_input_tokens,
        total_output_tokens,
    )
