"""Getting a transcript into the system.

1. Recall.ai meeting bot: joins a Teams (or Zoom / Google Meet) call from its link as a
   visible participant, streams `transcript.data` webhooks in real time, and keeps the
   full transcript for after the call. No Azure media infrastructure needed.
2. Upload: a .vtt file (what Teams' "Download transcript" or the Graph transcripts API
   returns) or plain "Speaker: text" lines. The reliable fallback for demos.
"""
import re
from dataclasses import dataclass

import httpx

from ..config import settings
from ..telemetry import span


@dataclass
class Line:
    speaker: str
    text: str
    start_s: float = 0.0
    end_s: float | None = None
    email: str | None = None


class RecallClient:
    def __init__(self):
        self.base = f"https://{settings.recall_region}.recall.ai/api/v1"
        self.headers = {"Authorization": f"Token {settings.recall_api_key}", "Content-Type": "application/json",
                        "accept": "application/json"}

    def webhook_url(self, meeting_id: int) -> str:
        return (f"{settings.public_base_url.rstrip('/')}/api/meetings/webhooks/recall"
                f"?token={settings.webhook_token}&meeting_id={meeting_id}")

    async def create_bot(self, meeting_url: str, meeting_id: int) -> str:
        body = {
            "meeting_url": meeting_url,
            "bot_name": settings.recall_bot_name,
            "metadata": {"meeting_id": str(meeting_id)},
            "recording_config": {
                "transcript": {
                    "provider": {"recallai_streaming": {"mode": "prioritize_low_latency", "language_code": "en"}},
                    "diarization": {"use_separate_streams_when_available": True},
                },
                "realtime_endpoints": [
                    {"type": "webhook", "url": self.webhook_url(meeting_id), "events": ["transcript.data"]},
                ],
            },
        }
        with span("recall.create_bot", meeting_id=meeting_id):
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{self.base}/bot/", headers=self.headers, json=body)
                r.raise_for_status()
                return r.json()["id"]

    async def get_bot(self, bot_id: str) -> dict:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{self.base}/bot/{bot_id}/", headers=self.headers)
            r.raise_for_status()
            return r.json()

    async def leave(self, bot_id: str) -> None:
        async with httpx.AsyncClient(timeout=20) as c:
            await c.post(f"{self.base}/bot/{bot_id}/leave_call/", headers=self.headers)

    @staticmethod
    def status_of(bot: dict) -> str:
        changes = bot.get("status_changes") or []
        return changes[-1].get("code", "unknown") if changes else "unknown"

    async def transcript(self, bot: dict) -> list[Line]:
        """Final transcript after the call (fallback if real-time webhooks were missed)."""
        url = None
        for rec in bot.get("recordings") or []:
            url = (((rec.get("media_shortcuts") or {}).get("transcript") or {}).get("data") or {}).get("download_url")
            if url:
                break
        if not url:
            return []
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(url)
            r.raise_for_status()
            return [from_recall_segment(seg) for seg in r.json() if seg.get("words")]


def from_recall_segment(seg: dict) -> Line:
    """Recall segment {participant, words[{text, start_timestamp{relative}}]} -> Line."""
    p = seg.get("participant") or {}
    words = seg.get("words") or []
    start = (words[0].get("start_timestamp") or {}).get("relative", 0.0) if words else 0.0
    end = (words[-1].get("end_timestamp") or {}).get("relative") if words else None
    return Line(speaker=p.get("name") or f"Speaker {p.get('id', '?')}",
                text=" ".join(w.get("text", "") for w in words).strip(),
                start_s=float(start or 0), end_s=float(end) if end is not None else None, email=p.get("email"))


_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})")
_VOICE = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>|$)", re.S)


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_transcript(raw: str) -> list[Line]:
    """WebVTT (Teams/Graph format, `<v Name>text</v>`) or plain `Name: text` lines."""
    raw = raw.replace("\r\n", "\n").strip()
    lines: list[Line] = []
    if raw.startswith("WEBVTT") or _TS.search(raw):
        for block in re.split(r"\n\s*\n", raw):
            ts = _TS.search(block)
            if not ts:
                continue
            body = block[ts.end():].strip()
            g = ts.groups()
            v = _VOICE.search(body)
            speaker, text = (v.group(1).strip(), v.group(2).strip()) if v else ("Unknown", body)
            if not text:
                continue
            if lines and lines[-1].speaker == speaker and _secs(*g[:4]) - (lines[-1].end_s or 0) < 0.5:
                lines[-1].text += " " + text                     # Teams splits sentences into many cues
                lines[-1].end_s = _secs(*g[4:])
            else:
                lines.append(Line(speaker, text, _secs(*g[:4]), _secs(*g[4:])))
        return lines
    t = 0.0
    for row in raw.split("\n"):
        m = re.match(r"^\s*(?:\[?(\d{1,2}):(\d{2})\]?\s*)?([^:]{1,40}):\s*(.+)$", row)
        if m:
            if m.group(1):
                t = int(m.group(1)) * 60 + int(m.group(2))
            lines.append(Line(m.group(3).strip(), m.group(4).strip(), t))
            t += 10
    return lines
