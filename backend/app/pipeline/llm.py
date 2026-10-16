"""LLM extraction for the hard cases (OpenCode Zen or any OpenAI-compatible endpoint).

The LLM only *proposes* structured facts. It never writes status: decide.py does.
"""
import json
import logging
import re

from openai import AsyncOpenAI

from ..config import settings
from ..telemetry import model_errors, span
from .types import INTENTS, Interpretation, TaskContext

log = logging.getLogger("llm")

SYSTEM = """You read a student's reply about one task in a group project and extract facts as JSON.
Return ONLY a JSON object with these keys:
  intent: one of completed | progress | blocked | question | off_topic
  confidence: number 0-1
  blocker_text: short phrase naming what they wait for, or null
  blocker_type: external | teammate | knowledge | resource | personal | null
  blocked_by: name of the teammate who can unblock them, or null
  progress_pct: integer 0-100 or null
  eta: short phrase or null
  proof_url: link, PR or commit mentioned, or null
Rules:
- If anything is still needed from someone or something else, intent is "blocked", even if they also say done/connected.
- "almost", "will finish tonight", "just testing" mean progress, not completed.
- Replies may be in English, Hindi or Hinglish, with emoji."""

FEW_SHOT = [
    ("The API is connected, but I am waiting for test credentials.",
     {"intent": "blocked", "confidence": 0.95, "blocker_text": "test credentials", "blocker_type": "external",
      "blocked_by": None, "progress_pct": 70, "eta": None, "proof_url": None}),
    ("almost done, just testing", {"intent": "progress", "confidence": 0.9, "blocker_text": None,
      "blocker_type": None, "blocked_by": None, "progress_pct": 85, "eta": None, "proof_url": None}),
    ("kal tak ho jayega", {"intent": "progress", "confidence": 0.85, "blocker_text": None, "blocker_type": None,
      "blocked_by": None, "progress_pct": None, "eta": "tomorrow", "proof_url": None}),
]

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=20)
    return _client


def _tuning() -> dict:
    """OpenRouter only. Extracting JSON needs no deliberation, and a reasoning model that deliberates can spend its
    whole token budget thinking and return nothing (measured: ~1 in 5 meeting summaries). Switching reasoning off
    parsed 10/10 in ~300 tokens versus ~1,200 for the default."""
    return {"extra_body": {"reasoning": {"enabled": False}}} if "openrouter.ai" in settings.llm_base_url else {}


async def _create(**kw):
    tune = _tuning()
    try:
        return await _get_client().chat.completions.create(**kw, **tune)
    except Exception:
        if not tune:
            raise
        log.info("model rejected the reasoning option; retrying without it")
        return await _get_client().chat.completions.create(**kw)


def _parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    data = json.loads(m.group(0))
    if data.get("intent") not in INTENTS:
        raise ValueError(f"bad intent {data.get('intent')!r}")
    return data


async def extract(text: str, ctx: TaskContext, corrections: list[tuple[str, str]] | None = None) -> Interpretation | None:
    if not settings.llm_enabled:
        return None
    messages = [{"role": "system", "content": SYSTEM}]
    shots = list(FEW_SHOT) + [(t, {"intent": s}) for t, s in (corrections or [])[-5:]]
    for t, out in shots:
        messages.append({"role": "user", "content": f"Task: (example)\nReply: {t}"})
        messages.append({"role": "assistant", "content": json.dumps(out)})
    messages.append({"role": "user", "content": (
        f"Task: {ctx.title}\nCurrent status: {ctx.status}\nOwner: {ctx.owner}\n"
        f"Depends on: {', '.join(ctx.depends_on) or 'nothing'}\nTeam: {', '.join(ctx.team)}\n"
        f"Earlier messages: {ctx.recent[-3:]}\nReply: {text}"
    )})
    for model in [settings.llm_model, settings.llm_fallback_model]:
        if not model:
            continue
        try:
            resp = await _create(model=model, messages=messages, temperature=0)
            data = _parse(resp.choices[0].message.content or "")
            return Interpretation(
                intent=data["intent"], confidence=float(data.get("confidence") or 0.5),
                blocker_text=data.get("blocker_text"), blocker_type=data.get("blocker_type"),
                blocked_by=data.get("blocked_by"), progress_pct=data.get("progress_pct"),
                eta=data.get("eta"), proof_url=data.get("proof_url"), engine="llm",
                trace=[f"llm({model}): intent={data['intent']} conf={data.get('confidence')} blocker={data.get('blocker_text')!r}"],
            )
        except Exception as e:
            log.warning("LLM %s failed: %s", model, e)
            model_errors.add(1, {"provider": "llm"})
    return Interpretation(intent="question", confidence=0.0, engine="llm", trace=["llm: all models failed"])


async def complete_json(system: str, user: str, max_tokens: int = 4000) -> dict | None:
    """Ask the LLM for a JSON object (meeting summaries). None if no LLM or all models fail.
    The budget is generous because reasoning models spend tokens thinking before they answer; a tight cap
    made the answer disappear on some runs."""
    if not settings.llm_enabled:
        return None
    for model in [settings.llm_model, settings.llm_fallback_model]:
        if not model:
            continue
        with span("llm.complete_json", model=model):
            try:
                resp = await _create(
                    model=model, temperature=0.2, max_tokens=max_tokens,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
                raw = resp.choices[0].message.content or ""
                m = re.search(r"\{.*\}", raw, re.S)
                if m:
                    return json.loads(m.group(0))
                log.warning("LLM %s returned no JSON (finish_reason=%s, %d chars); raise max_tokens or try another model",
                            model, resp.choices[0].finish_reason, len(raw))
                model_errors.add(1, {"provider": "llm"})
            except Exception as e:
                log.warning("LLM %s failed: %s", model, e)
                model_errors.add(1, {"provider": "llm"})
    return None
