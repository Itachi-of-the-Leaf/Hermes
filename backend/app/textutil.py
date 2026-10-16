"""Small text checks shared by the chat pipeline and the meeting pipeline."""
import re

_QUESTION_LEAD = re.compile(r"^\s*(?:did|do|does|can|could|would|should|is|are|was|were|has|have|will|what|who|when|where|why|how|which)\b", re.I)
_PROMISE = re.compile(r"\b(i'?ll|i will|let me|i can|i'?m going to|will do|can do)\b", re.I)
# "needs X done first", "once X is done", "after X is finished": a condition about the future, not a claim that X is done
CONDITION = re.compile(r"\b(needs?|need to be|once|after|until|when|before|first|then|wait|waiting|should be|must be|has to be|have to be|"
                       r"to be|will be|if)\b", re.I)


def looks_like_question(text: str) -> bool:
    """Chat and call transcripts often carry no punctuation, so a question mark cannot be relied on: also recognise an
    interrogative opening ("Did anyone catch the cricket score"), unless it is really a promise ("Will do")."""
    t = (text or "").strip()
    if t.endswith("?"):
        return True
    return bool(_QUESTION_LEAD.match(t)) and len(t.split()) >= 4 and not _PROMISE.search(t)


def states_a_condition(text: str) -> bool:
    return bool(CONDITION.search(text or ""))
