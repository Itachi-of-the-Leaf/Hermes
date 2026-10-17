"""Contract tests for PRD extraction and safe work assignment planning."""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app import github, prd
from app.channels import telegram as tg
from app.db import engine as db_engine
from app.models import Member, PRDIngestion, Task

TEAM = [
    Member(id=1, key="aanya", name="Aanya", role="lead"),
    Member(id=2, key="kabir", name="Kabir", role="member"),
    Member(id=3, key="meera", name="Meera", role="member"),
]
TASKS = [Task(id="T1", title="Create API", owner_id=2, status="Pending", aliases="create api")]
TODAY = date(2026, 10, 17)


def item(title="Implement endpoint", owner="Kabir", **overrides):
    return {"title": title, "owner": owner, "confidence": 0.96, "evidence": "Implement the endpoint", **overrides}


def test_prd_text_is_bounded_and_extension_filter_is_explicit():
    assert prd.MAX_SOURCE_BYTES == 1024 * 1024
    assert prd.accepted_filename("requirements.md")
    assert prd.accepted_filename("requirements.TXT")
    assert not prd.accepted_filename("requirements.pdf")
    assert not prd.accepted_filename("payload.md.exe")


def test_explicit_high_confidence_owner_creates_an_auto_assignable_candidate():
    result = prd.validate_items([item()], TEAM, TASKS, TODAY)
    assert len(result.auto_assign) == 1
    assert (result.auto_assign[0].title, result.auto_assign[0].owner_key) == ("Implement endpoint", "kabir")
    assert result.needs_review == []


def test_unknown_or_ambiguous_owner_is_held_for_lead_not_guessed():
    result = prd.validate_items([item(owner="K")], TEAM, TASKS, TODAY)
    assert result.auto_assign == []
    assert len(result.needs_review) == 1


def test_low_confidence_and_unassigned_work_need_lead_review():
    result = prd.validate_items([item(confidence=0.89), item("Write docs", owner=None)], TEAM, TASKS, TODAY)
    assert result.auto_assign == []
    assert len(result.needs_review) == 2


def test_duplicate_task_is_not_auto_created():
    result = prd.validate_items([item(title="Create API")], TEAM, TASKS, TODAY)
    assert result.auto_assign == []
    assert len(result.needs_review) == 1


def test_missing_or_cyclic_dependency_is_held_for_review():
    result = prd.validate_items([
        item(title="Implement endpoint", after=["unknown task"]),
        item(title="Write tests", after=["Write tests"]),
    ], TEAM, TASKS, TODAY)
    assert result.auto_assign == []
    assert len(result.needs_review) == 2


def test_malformed_model_payload_produces_no_tasks():
    assert prd.validate_items(None, TEAM, TASKS, TODAY).auto_assign == []
    assert prd.validate_items({"items": "invalid"}, TEAM, TASKS, TODAY).auto_assign == []


def test_github_pr_reference_requires_allowlisted_owner_repo_and_pull_path():
    allow = {"example-org/project"}
    assert prd.parse_pull_request_url("https://github.com/example-org/project/pull/42", allow) == ("example-org", "project", 42)
    for url in (
        "https://github.com/attacker/project/pull/42",
        "https://github.com/example-org/project/issues/42",
        "https://evil.example/example-org/project/pull/42",
        "https://github.com/example-org/project/pull/0",
    ):
        assert prd.parse_pull_request_url(url, allow) is None


def test_dependencies_resolve_even_when_prerequisite_is_later_in_prd():
    result = prd.validate_items([
        item("API tests", owner="Meera", after=["API build"]),
        item("API build", owner="Kabir"),
    ], TEAM, TASKS, TODAY)
    assert [c.title for c in result.auto_assign] == ["API build", "API tests"]
    assert result.auto_assign[1].after == ("new:0",)
    resolved = prd.resolve_auto_dependencies(result.auto_assign, TASKS)
    assert resolved is not None
    assert resolved[0][1] == []
    assert resolved[1][1] == ["new:0"]


def test_dependency_cycle_is_held_for_review():
    result = prd.validate_items([
        item("API one", owner="Kabir", after=["API two"]),
        item("API two", owner="Meera", after=["API one"]),
    ], TEAM, TASKS, TODAY)
    assert result.auto_assign == []
    assert len(result.needs_review) == 2


def test_duplicate_dependency_target_is_held_as_ambiguous():
    result = prd.validate_items([
        item("API tests", owner="Meera", after=["API build"]),
        item("API build", owner="Kabir"),
        item("API build", owner="Kabir", evidence="A second separate API build"),
    ], TEAM, TASKS, TODAY)
    assert result.auto_assign == []
    assert any("ambiguous" in ";".join(row["reasons"]) for row in result.needs_review)


def test_invalid_explicit_date_and_fractional_estimate_are_not_auto_assigned():
    bad_date = prd.validate_items([item(due="not-a-date")], TEAM, TASKS, TODAY)
    fractional_estimate = prd.validate_items([item(estimate_minutes=2.5)], TEAM, TASKS, TODAY)
    assert bad_date.auto_assign == []
    assert fractional_estimate.auto_assign == []


def test_prd_download_rejects_git_lfs_pointer():
    from types import SimpleNamespace

    from app.prd import _decode_prd_response
    response = SimpleNamespace(content=b"version https://git-lfs.github.com/spec/v1\nsize 99999999\n")
    with pytest.raises(ValueError, match="Git LFS"):
        _decode_prd_response(response)


def test_malformed_model_confidence_is_held_not_raised():
    result = prd.validate_items([item(confidence="high")], TEAM, TASKS, TODAY)
    assert result.auto_assign == []
    assert len(result.needs_review) == 1


def test_ingestion_requires_lead_and_deduplicates_before_second_model_call(monkeypatch):
    async def fake_extract(text, members, tasks, today):
        return prd.Validation(auto_assign=[prd.Candidate("Implement endpoint", "kabir", None, None, (), 0.96, "Implement endpoint")])

    async def attempt(lead_key):
        with Session(db_engine) as s:
            lead = s.exec(select(Member).where(Member.key == lead_key)).first()
            return await prd.ingest_async(s, lead, "a valid PRD body", source_ref="telegram:unique-prd", source_name="prd.md", today=TODAY)

    monkeypatch.setattr(prd, "extract", fake_extract)
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        client.post("/api/reset")
    with Session(db_engine) as s:
        member = s.exec(select(Member).where(Member.key == "kabir")).first()
        with pytest.raises(PermissionError):
            asyncio.run(prd.ingest_async(s, member, "a valid PRD body", source_ref="telegram:bad-role", source_name="prd.md", today=TODAY))
    first = asyncio.run(attempt("aanya"))
    assert first["duplicate"] is False and first["created"][0]["owner"] == "Kabir"
    second = asyncio.run(attempt("aanya"))
    assert second == {"duplicate": True, "created": [], "needs_review": []}
    with Session(db_engine) as s:
        assert len(s.exec(select(PRDIngestion).where(PRDIngestion.source_ref == "telegram:unique-prd")).all()) == 1


def test_model_failure_leaves_no_task_writes(monkeypatch):
    async def fail(text, members, tasks, today):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(prd, "extract", fail)
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        client.post("/api/reset")
    with Session(db_engine) as s:
        lead = s.exec(select(Member).where(Member.key == "aanya")).first()
        before = len(s.exec(select(Task)).all())
        with pytest.raises(RuntimeError, match="model unavailable"):
            asyncio.run(prd.ingest_async(s, lead, "another valid PRD body", source_ref="telegram:failed-prd", source_name="prd.txt", today=TODAY))
        assert len(s.exec(select(Task)).all()) == before
        assert not s.exec(select(PRDIngestion).where(PRDIngestion.source_ref == "telegram:failed-prd")).first()


def test_dependency_target_not_auto_assignable_holds_dependent_item():
    result = prd.validate_items([
        {"title": "Foundation", "owner": None, "confidence": 0.70, "evidence": "Foundation"},
        item("Feature", owner="Kabir", after=["Foundation"]),
    ], TEAM, TASKS, TODAY)
    assert result.auto_assign == []
    assert any(x["title"] == "Feature" for x in result.needs_review)


def test_telegram_prd_command_validates_auth_and_reports_created_tasks(monkeypatch):
    async def fake_ingest(s, lead, text, *, source_ref, source_name):
        assert lead.role == "lead"
        return {"duplicate": False, "created": [{"id": "T99", "title": "Implement endpoint", "owner": "Kabir"}], "needs_review": []}

    monkeypatch.setattr(prd, "ingest_async", fake_ingest)
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as client:
        client.post("/api/reset")
    class Message:
        document = SimpleNamespace(file_name="plan.md", file_size=14, file_id="file-1")
        text = "/prd"
        caption = "/prd"
        chat = SimpleNamespace(type="group")
        message_id = 991
        def __init__(self):
            self.replies = []
        async def reply_text(self, text, **kwargs):
            self.replies.append(text)
        async def reply_html(self, text, **kwargs):
            self.replies.append(text)
    msg = Message()
    doc = SimpleNamespace(get_file=lambda: None)
    class RemoteFile:
        async def download_as_bytearray(self):
            return bytearray(b"valid prd text")
    async def get_file():
        return RemoteFile()
    doc.get_file = get_file
    update = SimpleNamespace(effective_user=SimpleNamespace(id=100), effective_chat=SimpleNamespace(type="group", id=-1001),
                             effective_message=msg, message=msg)
    class Bot:
        async def get_file(self, file_id):
            return SimpleNamespace(download_as_bytearray=lambda: None)
    async def download_as_bytearray():
        return bytearray(b"valid prd text")
    Bot.get_file = lambda self, file_id: get_file_object()
    async def get_file_object():
        return SimpleNamespace(download_as_bytearray=download_as_bytearray)
    with Session(db_engine) as s:
        lead = s.exec(select(Member).where(Member.key == "aanya")).first()
        lead.tg_user_id = 100
        s.add(lead)
        s.commit()
    ctx = SimpleNamespace(bot=Bot())
    asyncio.run(tg.prd_cmd(update, ctx))
    assert any("T99" in text and "Kabir" in text for text in msg.replies)


def test_read_only_github_pr_provider_uses_get_only_and_summarizes_findings(monkeypatch):
    assert prd.parse_pull_request_url("https://github.com/example-org/project/pull/42", {"example-org/project"})
    methods = []
    class Response:
        def __init__(self, payload): self._payload = payload
        def raise_for_status(self): pass
        def json(self): return self._payload
        @property
        def text(self): return "bounded diff"
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            methods.append("GET")
            if url.endswith("/pulls/42"):
                return Response({"title": "Example PR", "body": "", "html_url": url, "head": {"sha": "abc", "ref": "feature"}, "base": {"repo": {"full_name": "example-org/project"}, "ref": "main"}, "user": {"login": "dev"}})
            if url.endswith("/files"):
                return Response([{"filename": "src/app.py", "status": "modified", "additions": 1, "deletions": 0, "patch": "@@ -1 +1 @@"}])
            return Response({"check_runs": []})
        async def post(self, *args, **kwargs):
            methods.append("POST")
            raise AssertionError("write not allowed")
    monkeypatch.setattr(github.httpx, "AsyncClient", Client)
    report = asyncio.run(github.review_pr("https://github.com/example-org/project/pull/42", {"example-org/project"}, token=""))
    assert report["title"] == "Example PR" and report["files"][0]["filename"] == "src/app.py"
    assert methods and set(methods) == {"GET"}
