"""The decision pipeline: rules fast path -> Jev -> LLM -> heuristic, then a deterministic gate.

Models propose; this file decides. The gate is plain code so the same reply always
produces the same status, and every override is written into the trace.
"""
from ..config import settings
from . import jev, llm
from .rules import extract_fields, fast_path, heuristic
from .types import Decision, Interpretation, TaskContext

INTENT_TO_STATUS = {"completed": "Complete", "progress": "In Progress", "blocked": "Blocked"}


async def interpret(text: str, ctx: TaskContext, corrections=None) -> Interpretation:
    trace: list[str] = []
    interp = fast_path(text)
    if interp is None:
        j = await jev.classify(text, ctx)
        if j is not None:
            trace += j.trace
        if j is not None and j.engine == "jev" and j.confidence >= settings.jev_min_confidence:
            interp = j
        else:
            if j is not None:
                trace.append(f"jev: below {settings.jev_min_confidence} or failed → escalate")
            lx = await llm.extract(text, ctx, corrections)
            if lx is not None:
                trace += lx.trace
            if lx is not None and lx.confidence > 0:
                interp = lx
            else:
                interp = heuristic(text, ctx)
                trace += interp.trace
                if j is not None and j.confidence > 0:   # keep Jev's probabilities as signals
                    interp.waiting_on_something = j.waiting_on_something
                    interp.work_remaining = j.work_remaining
    else:
        trace += interp.trace

    extract_fields(text, interp, ctx)

    # Jev returns no text, so a blocked answer needs the blocker phrase from somewhere.
    if interp.intent == "blocked" and not interp.blocker_text and interp.engine != "llm" and settings.llm_enabled:
        lx = await llm.extract(text, ctx, corrections)
        if lx and lx.blocker_text:
            interp.blocker_text, interp.blocker_type = lx.blocker_text, lx.blocker_type or interp.blocker_type
            interp.blocked_by = interp.blocked_by or lx.blocked_by
            trace.append(f"llm: filled blocker text {lx.blocker_text!r}")
    if interp.intent == "blocked" and not interp.blocker_text:
        interp.blocker_text = text.strip()[:140]

    interp.trace = trace
    return interp


def gate(interp: Interpretation, current_status: str) -> Decision:
    reasons: list[str] = []
    intent = interp.intent

    # Rule 1: any dependency means Blocked, even if the reply also says done / connected.
    if intent in ("completed", "progress") and (interp.waiting_on_something or 0) >= 0.5:
        reasons.append(f"waiting_on_something={interp.waiting_on_something:.2f} → Blocked overrides {intent}")
        intent = "blocked"

    # Rule 2: never Complete on hedged text or when work remains.
    force_review = False
    if intent == "completed" and (interp.hedged or (interp.work_remaining or 0) >= 0.5):
        reasons.append("hedge words or remaining work → In Progress, lead to confirm")
        intent, force_review = "progress", True

    interp.intent = intent
    new_status = INTENT_TO_STATUS.get(intent)
    if new_status is None:
        action = "clarify" if intent == "question" else "ignore"
        return Decision(action=action, new_status=None, interp=interp, reasons=reasons + [f"intent={intent}"])

    needed = settings.complete_confidence if new_status == "Complete" else settings.auto_apply_confidence
    if interp.confidence < needed:
        reasons.append(f"confidence {interp.confidence:.2f} < {needed} for {new_status}")
        force_review = True
    if settings.mode == "draft":
        reasons.append("draft mode: lead approves every change")
        force_review = True
    if current_status == "Complete" and new_status != "Complete":
        reasons.append("reopening a Complete task needs the lead")
        force_review = True

    return Decision(action="review" if force_review else "apply", new_status=new_status, interp=interp, reasons=reasons)
