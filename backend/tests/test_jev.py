"""Jev integration with a mocked HTTP response in the documented Decisions API shape."""
import asyncio

import httpx

from app.config import settings
from app.pipeline.decide import gate, interpret
from app.pipeline.types import TaskContext

CTX = TaskContext(task_id="T2", title="Connect the payment API", status="Pending", owner="Kabir")

FAKE = {
    "answers": {
        # Jev leans "progress" on the surface words, but says they are waiting on something:
        "intent": {"type": "choice", "choice": "progress", "confidence": 0.71,
                   "probabilities": {"progress": 0.71, "blocked": 0.27, "completed": 0.02}},
        "waiting": {"type": "noul", "noul": 0.93},
        "remaining": {"type": "noul", "noul": 0.88},
        "progress": {"type": "score", "score": 2.9, "confidence": 0.8, "probabilities": {}},
    },
    "usage": {"input_tokens": 500, "output_tokens": 60, "cost": 0.00002},
}


class FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
        return FAKE


def test_jev_waiting_overrides_to_blocked(monkeypatch):
    sent = {}

    async def fake_post(self, url, headers=None, json=None):
        sent.update(url=url, body=json, headers=headers)
        return FakeResp()

    monkeypatch.setattr(settings, "openrouter_key", "test-key")
    monkeypatch.setattr(type(settings), "jev_enabled", property(lambda self: True))
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    i = asyncio.run(interpret("connected, but waiting for test credentials", CTX))
    d = gate(i, "Pending")

    assert sent["body"]["model"] == settings.jev_model
    assert set(sent["body"]["questions"]) == {"intent", "waiting", "remaining", "progress"}
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    assert i.engine == "jev" and i.progress_pct == 80
    assert d.new_status == "Blocked"                     # the deterministic rule, not the model, decided
    assert any("overrides" in r for r in d.reasons)
    assert i.blocker_text == "test credentials"           # Jev gives no text; the extractor filled it
