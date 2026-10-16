"""Jev (TypeSafe's System One decision model) via OpenRouter's Decisions API.

Jev answers typed questions about a `state` and returns probabilities, never text:
  - choice -> {"choice": "...", "confidence": 0.67, "probabilities": {...}}
  - noul   -> {"noul": 0.96}            (probability of yes)
  - score  -> {"score": 1.99, "confidence": 0.99, "probabilities": {...}}
Docs: https://openrouter.ai/docs/guides/community/jev-tutorial
"""
import logging

import httpx

from ..config import settings
from ..telemetry import model_cost, model_errors, span
from .types import Interpretation, TaskContext

log = logging.getLogger("jev")

PROGRESS_SCALE = ["Not started", "Just started", "About half done", "Mostly done", "Finished"]
PROGRESS_PCT = [0, 20, 50, 80, 100]

QUESTIONS = {
    "intent": {
        "type": "choice",
        "instructions": "What is the task owner telling us about this task in their reply?",
        "criteria": {
            "completed": "The task is finished: nothing is left to do and they are not waiting on anyone or anything.",
            "progress": "Work has started or is under way, or they expect to finish later, and nothing is stopping them.",
            "blocked": "They cannot finish because they are waiting on someone or something, or lack access, "
                       "credentials, information, time or health. Partial progress plus a dependency is blocked.",
            "question": "They ask a question or ask which task is meant, without giving a status.",
            "off_topic": "The message is not about this task's status.",
        },
    },
    "waiting": {
        "type": "noul",
        "instructions": "Is the owner waiting on a person, a resource or an external dependency before the task can be finished?",
        "criteria": {
            "true": "Something outside their control must happen first, e.g. credentials, access, someone else's work.",
            "false": "Nothing external is needed for them to finish.",
        },
    },
    "remaining": {
        "type": "noul",
        "instructions": "Does any work on this task still remain, according to the reply?",
        "criteria": {
            "true": "They say or imply that something is left: testing, fixes, 'almost', 'tonight', 'will', 'but'.",
            "false": "They say it is fully done.",
        },
    },
    "progress": {
        "type": "score",
        "instructions": "How far along is the task according to the reply?",
        "criteria": PROGRESS_SCALE,
    },
}


async def decide(state: dict, questions: dict, timeout: float = 10) -> dict | None:
    """One Jev request: typed questions about `state`. Returns the `answers` map, or None."""
    if not settings.jev_enabled:
        return None
    body = {"model": settings.jev_model, "state": state, "questions": questions}
    with span("jev.decide", questions=len(questions), model=settings.jev_model) as sp:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    settings.jev_url,
                    headers={"Authorization": f"Bearer {settings.openrouter_key}", "Content-Type": "application/json"},
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:  # network, auth, schema
            log.warning("Jev call failed: %s", e)
            model_errors.add(1, {"provider": "jev"})
            sp.set_attribute("error", str(e)[:200])
            raise
        cost = (data.get("usage") or {}).get("cost")
        if cost:
            model_cost.add(float(cost), {"provider": "jev"})
            sp.set_attribute("cost_usd", float(cost))
        return data["answers"]


async def classify(text: str, ctx: TaskContext) -> Interpretation | None:
    if not settings.jev_enabled:
        return None
    state = {
        "task": ctx.title,
        "current_status": ctx.status,
        "owner": ctx.owner,
        "depends_on": ctx.depends_on,
        "earlier_messages": ctx.recent[-3:],
        "reply": text,
    }
    try:
        a = await decide(state, QUESTIONS)
    except Exception as e:
        return Interpretation(intent="question", confidence=0.0, engine="jev", trace=[f"jev: error {e!s:.80}"])

    intent = a["intent"]["choice"]
    conf = float(a["intent"].get("confidence", 0))
    waiting = float(a.get("waiting", {}).get("noul", 0))
    remaining = float(a.get("remaining", {}).get("noul", 0))
    score = a.get("progress", {}).get("score")
    pct = None
    if score is not None:
        idx = max(0, min(len(PROGRESS_PCT) - 1, round(float(score))))
        pct = PROGRESS_PCT[idx]
    probs = a["intent"].get("probabilities", {})
    top = ", ".join(f"{k} {v:.2f}" for k, v in sorted(probs.items(), key=lambda kv: -kv[1])[:3])
    return Interpretation(
        intent=intent, confidence=conf, waiting_on_something=waiting, work_remaining=remaining,
        progress_pct=pct, engine="jev",
        trace=[f"jev: intent={intent} conf={conf:.2f} [{top}] waiting={waiting:.2f} remaining={remaining:.2f}"],
    )
