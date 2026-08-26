"""LLM calls via the OpenAI-compatible chat completions endpoint.

Two calls per run: the briefing (primary model) and alert formatting (fast
model). System prompts are editable files in prompts/. The LLM being down is
an expected condition, not an error: callers get None / the mechanical path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import OpenAI

from skywatch import console
from skywatch.alerts import mechanical_alerts, parse_llm_alerts
from skywatch.config import Config
from skywatch.features.enso import EnsoStatus
from skywatch.features.thresholds import ThresholdEvent
from skywatch.features.vortex import VortexStatus
from skywatch.models import Alert

BRIEFING_UNAVAILABLE = (
    "# Briefing unavailable\n\n"
    "The LLM at {base_url} could not be reached or did not answer. "
    "The digest, alerts and dashboard for this run were produced normally; "
    "re-run `skywatch brief` once the model is back.\n\n"
    "Error: {error}\n"
)


def _prompts_dir() -> Path:
    from skywatch.config import project_root

    return project_root() / "prompts"


def load_prompt(name: str) -> str:
    return (_prompts_dir() / name).read_text()


def _think_body(cfg: Config) -> dict[str, Any]:
    """Ollama's think switch. Omitted entirely when thinking is enabled so
    non-Ollama servers never see an unknown parameter by default."""
    return {} if cfg.llm.thinking else {"think": False}


def _client(cfg: Config) -> OpenAI:
    return OpenAI(
        base_url=cfg.llm.base_url,
        api_key=cfg.llm.effective_api_key,
        timeout=cfg.llm.timeout_seconds,
        max_retries=1,
    )


def write_briefing(cfg: Config, digest: dict[str, Any]) -> tuple[str, bool]:
    """The briefing call. Returns (markdown, llm_ok)."""
    system = load_prompt("briefing.system.md")
    user = (
        "Here is today's digest. Write the briefing.\n\n```json\n"
        + json.dumps(digest, indent=1, ensure_ascii=False)
        + "\n```"
    )
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            console.log().info(
                "Briefing via %s (%s), attempt %d", cfg.llm.model, cfg.llm.base_url, attempt
            )
            resp = _client(cfg).chat.completions.create(
                model=cfg.llm.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=cfg.llm.temperature,
                max_tokens=cfg.llm.max_tokens,
                extra_body=_think_body(cfg),
            )
            choice = resp.choices[0]
            text = (choice.message.content or "").strip()
            if not text:
                # Two known live failure modes: a request racing a 142GB model
                # load returns an empty 200 (retry heals it), and a reasoning
                # model that ignores think:false can burn the budget thinking.
                if choice.finish_reason == "length":
                    raise ValueError(
                        "model spent the whole max_tokens budget thinking and "
                        "returned no content — raise llm.max_tokens in config.yaml"
                    )
                raise ValueError("empty completion (model may still be loading)")
            return _strip_reasoning(text), True
        except Exception as exc:  # any failure -> degraded but complete run
            last_exc = exc
            console.log().warning("Briefing attempt %d failed: %s", attempt, exc)
    return BRIEFING_UNAVAILABLE.format(base_url=cfg.llm.base_url, error=last_exc), False


def write_alerts(
    cfg: Config,
    digest: dict[str, Any],
    events: list[ThresholdEvent],
    enso: EnsoStatus,
    vortex: VortexStatus,
) -> tuple[list[Alert], str]:
    """The alerts call (fast model). Returns (alerts, mode) where mode is
    'llm' or 'mechanical'. Schema-validated; one retry; mechanical fallback."""
    fallback = mechanical_alerts(events, enso, vortex)
    if not fallback:
        return [], "mechanical"

    system = load_prompt("alerts.system.md")
    # The LLM gets the SAME fact set the mechanical path renders: threshold
    # events plus draft driver alerts. Its job is wording and day-grouping,
    # nothing more; the sanity check below holds it to that.
    user = json.dumps(
        {
            "threshold_events": [e.model_dump(mode="json") for e in events],
            "draft_alerts": [a.model_dump(mode="json") for a in fallback],
            "context": {"location": digest.get("meta", {}).get("location")},
        },
        indent=1,
        ensure_ascii=False,
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    for attempt in (1, 2):
        try:
            console.log().info(
                "Alerts via %s (attempt %d)", cfg.llm.fast_model, attempt
            )
            resp = _client(cfg).chat.completions.create(  # type: ignore[call-overload]
                model=cfg.llm.fast_model,
                messages=messages,
                temperature=0.0,
                max_tokens=cfg.llm.max_tokens,
                response_format={"type": "json_object"},
                extra_body=_think_body(cfg),
            )
            text = _strip_reasoning((resp.choices[0].message.content or "").strip())
            alerts = parse_llm_alerts(text)
            if _alerts_sane(alerts, fallback):
                return alerts, "llm"
            raise ValueError("LLM alerts failed sanity check against the facts")
        except Exception as exc:
            console.log().warning("Alert formatting attempt %d failed: %s", attempt, exc)
            if attempt == 1:
                messages.append({"role": "user", "content":
                    f"Your previous output was invalid ({exc}). "
                    "Return ONLY the JSON object, matching the schema exactly."})
    console.log().warning("Falling back to mechanical alerts")
    return fallback, "mechanical"


def _alerts_sane(alerts: list[Alert], fallback: list[Alert]) -> bool:
    """The LLM formats facts; it must not invent or drop whole stories."""
    fact_cats = {a.category for a in fallback}
    got_cats = {a.category for a in alerts}
    return got_cats == fact_cats


def _strip_reasoning(text: str) -> str:
    """Remove <think>...</think> blocks reasoning models may emit."""
    if "<think>" in text and "</think>" in text:
        head, _, tail = text.partition("<think>")
        _, _, after = tail.partition("</think>")
        text = (head + after).strip()
    return text
