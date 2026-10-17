"""Read-only GitHub API provider tests with no live credentials."""
import asyncio

import pytest

from app import github, prd


def test_prd_github_file_url_and_pr_url_are_strictly_allowlisted():
    allowed = {"example-org/project"}
    assert prd.parse_prd_file_url("https://github.com/example-org/project/blob/main/PRD.md", allowed) == (
        "example-org", "project", "main", "PRD.md")
    assert prd.parse_prd_file_url("https://github.com/attacker/project/blob/main/PRD.md", allowed) is None
    assert prd.parse_prd_file_url("http://github.com/example-org/project/blob/main/PRD.md", allowed) is None
    assert prd.parse_prd_file_url("https://github.com/example-org/project/blob/main/PRD.pdf", allowed) is None
    assert prd.parse_pull_request_url("https://github.com/example-org/project/pull/42", allowed) == (
        "example-org", "project", 42)
    assert prd.parse_pull_request_url("https://github.com/attacker/project/pull/42", allowed) is None


def test_prd_file_fetch_uses_github_api_get_and_enforces_size(monkeypatch):
    methods = []

    class Response:
        content = b"PRD body"
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs["follow_redirects"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def get(self, url, **kwargs):
            methods.append(("GET", url))
            assert "/repos/example-org/project/contents/PRD.md" in url
            assert kwargs["params"] == {"ref": "main"}
            return Response()

    monkeypatch.setattr(prd.httpx, "AsyncClient", Client)
    assert asyncio.run(prd.fetch_github_text("example-org", "project", "main", "PRD.md")) == "PRD body"
    assert len(methods) == 1 and methods[0][0] == "GET"

    class Huge(Response):
        content = b"x" * (prd.MAX_SOURCE_BYTES + 1)
    class ClientHuge(Client):
        async def get(self, url, **kwargs):
            return Huge()
    monkeypatch.setattr(prd.httpx, "AsyncClient", ClientHuge)
    with pytest.raises(ValueError, match="1 MiB"):
        asyncio.run(prd.fetch_github_text("example-org", "project", "main", "PRD.md"))


def test_pr_review_uses_get_only_and_returns_bounded_metadata(monkeypatch):
    methods = []

    class Response:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self.payload

    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs["follow_redirects"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def get(self, url, **kwargs):
            methods.append("GET")
            if url.endswith("/pulls/42"):
                return Response({"title": "Example PR", "body": "body", "html_url": url,
                                 "head": {"sha": "abc", "ref": "feature"},
                                 "base": {"repo": {"full_name": "example-org/project"}, "ref": "main"},
                                 "user": {"login": "dev"}})
            if url.endswith("/files"):
                return Response([{"filename": "src/app.py", "status": "modified", "additions": 1,
                                  "deletions": 0, "patch": "@@ -1 +1 @@"}])
            return Response({"check_runs": [{"name": "CI", "status": "completed", "conclusion": "success"}]})
        async def post(self, *args, **kwargs):
            methods.append("POST")
            raise AssertionError("write endpoint must not be used")
        async def put(self, *args, **kwargs):
            raise AssertionError("write endpoint must not be used")

    monkeypatch.setattr(github.httpx, "AsyncClient", Client)
    result = asyncio.run(github.review_pr("https://github.com/example-org/project/pull/42", {"example-org/project"}))
    assert result["title"] == "Example PR"
    assert result["files"][0]["filename"] == "src/app.py"
    assert result["checks"][0]["conclusion"] == "success"
    assert methods == ["GET", "GET", "GET"]


def test_non_allowlisted_pr_fails_before_network(monkeypatch):
    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network must not be reached")
    monkeypatch.setattr(github.httpx, "AsyncClient", ForbiddenClient)
    with pytest.raises(ValueError):
        asyncio.run(github.review_pr("https://github.com/outside/secret/pull/2", {"example-org/project"}))
