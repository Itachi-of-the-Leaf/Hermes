"""Read-only GitHub PR metadata, changed files and check retrieval."""
from __future__ import annotations

from urllib.parse import quote

import httpx

from .prd import GITHUB_API, parse_pull_request_url

MAX_FILES = 50
MAX_PATCH_CHARS = 120_000
MAX_BODY_CHARS = 5_000


async def review_pr(url: str, allowed_repositories: set[str], token: str = "") -> dict:
    parsed = parse_pull_request_url(url, allowed_repositories)
    if not parsed:
        raise ValueError("PR URL is not a valid pull request in an allowlisted repository")
    owner, repo, number = parsed
    base = f"{GITHUB_API}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=False, headers=headers) as client:
        meta_response = await client.get(f"{base}/pulls/{number}")
        meta_response.raise_for_status()
        meta = meta_response.json()
        files_response = await client.get(f"{base}/pulls/{number}/files", params={"per_page": MAX_FILES})
        files_response.raise_for_status()
        files = files_response.json()
        check_response = await client.get(f"{base}/commits/{meta['head']['sha']}/check-runs", params={"per_page": 100})
        check_response.raise_for_status()
    actual_repo = ((meta.get("base") or {}).get("repo") or {}).get("full_name", "")
    if actual_repo.casefold() != f"{owner}/{repo}".casefold():
        raise ValueError("GitHub returned a pull request for an unexpected repository")
    bounded_files, remaining = [], MAX_PATCH_CHARS
    for item in files[:MAX_FILES]:
        patch = str(item.get("patch") or "")[:remaining]
        remaining -= len(patch)
        bounded_files.append({"filename": str(item.get("filename", ""))[:500],
                              "status": str(item.get("status", ""))[:40],
                              "additions": int(item.get("additions", 0)),
                              "deletions": int(item.get("deletions", 0)), "patch": patch})
    checks = [{"name": str(c.get("name", ""))[:200], "status": c.get("status"), "conclusion": c.get("conclusion")}
              for c in ((check_response.json().get("check_runs") or [])[:100])]
    return {"title": str(meta.get("title", ""))[:300], "body": str(meta.get("body") or "")[:MAX_BODY_CHARS],
            "url": meta.get("html_url"), "number": number, "repository": f"{owner}/{repo}",
            "author": (meta.get("user") or {}).get("login"), "state": meta.get("state"),
            "draft": bool(meta.get("draft")), "head_sha": meta["head"]["sha"], "head_branch": meta["head"]["ref"],
            "base_branch": meta["base"]["ref"], "files": bounded_files, "checks": checks,
            "files_truncated": len(files) >= MAX_FILES or remaining <= 0}
