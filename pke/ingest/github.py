"""GitHub ingestion pipeline — indexes issues, PRs, and comments."""

from __future__ import annotations

import httpx

from pke.chunk import chunk_markdown
from pke.config import settings
from pke.db.setup import get_client
from pke.embed import embed_batch
from pke.sync.state import SyncState


def _gh_headers() -> dict:
    """Get GitHub API headers."""
    import os

    token = settings.github_token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_issues(repo: str, since: str | None = None) -> list[dict]:
    """Fetch issues and PRs from a GitHub repo."""
    url = f"https://api.github.com/repos/{repo}/issues"
    params: dict = {"state": "all", "per_page": 100, "sort": "updated", "direction": "asc"}
    if since:
        params["since"] = since

    all_issues: list[dict] = []
    with httpx.Client(headers=_gh_headers(), timeout=30) as client:
        while url:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            all_issues.extend(resp.json())
            # Follow pagination
            url = resp.links.get("next", {}).get("url")
            params = {}  # params already in the next URL

    return all_issues


def _fetch_comments(repo: str, issue_number: int) -> list[dict]:
    """Fetch comments for an issue/PR."""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    with httpx.Client(headers=_gh_headers(), timeout=30) as client:
        resp = client.get(url, params={"per_page": 100})
        resp.raise_for_status()
        return resp.json()


def ingest_github(repo: str | None = None, full: bool = False) -> dict:
    """Ingest issues and PRs from GitHub repositories.

    Args:
        repo: Specific repo (owner/repo). If None, uses all configured repos.
        full: If True, re-ingest everything.

    Returns:
        Summary dict with counts.
    """
    repos = [repo] if repo else settings.github_repos_list
    if not repos:
        return {"error": "No repos configured. Set PKE_GITHUB_REPOS or pass --repo."}

    sync = SyncState()
    client = get_client()
    collection = settings.qdrant_collection
    stats = {"repos": len(repos), "issues_ingested": 0, "comments_ingested": 0}

    for r in repos:
        since = None if full else sync.get_cursor("github", r)

        issues = _fetch_issues(r, since=since)
        latest_updated = since

        for issue in issues:
            number = issue["number"]
            updated = issue["updated_at"]
            is_pr = "pull_request" in issue
            kind = "pr" if is_pr else "issue"

            # Index issue body
            body = issue.get("body") or ""
            if body.strip():
                meta = {
                    "source_type": "github",
                    "repo": r,
                    "issue_number": number,
                    "kind": kind,
                    "author": issue["user"]["login"],
                    "date": issue["created_at"],
                    "state": issue["state"],
                    "url": issue["html_url"],
                    "title": issue["title"],
                }
                source = f"github:{r}:{kind}:{number}"
                chunks = chunk_markdown(body, source=source, base_metadata=meta)

                if chunks:
                    vectors = embed_batch([c.text for c in chunks])
                    from qdrant_client.models import PointStruct

                    client.upsert(
                        collection_name=collection,
                        points=[
                            PointStruct(id=c.id, vector=v, payload={**c.metadata, "text": c.text})
                            for c, v in zip(chunks, vectors)
                        ],
                    )
                    stats["issues_ingested"] += 1

            # Index comments
            comments = _fetch_comments(r, number)
            for comment in comments:
                cbody = comment.get("body") or ""
                if not cbody.strip():
                    continue
                cmeta = {
                    "source_type": "github",
                    "repo": r,
                    "issue_number": number,
                    "kind": f"{kind}_comment",
                    "author": comment["user"]["login"],
                    "date": comment["created_at"],
                    "url": comment["html_url"],
                }
                csource = f"github:{r}:{kind}:{number}:comment:{comment['id']}"
                cchunks = chunk_markdown(cbody, source=csource, base_metadata=cmeta)
                if cchunks:
                    vectors = embed_batch([c.text for c in cchunks])
                    from qdrant_client.models import PointStruct

                    client.upsert(
                        collection_name=collection,
                        points=[
                            PointStruct(
                                id=c.id, vector=v, payload={**c.metadata, "text": c.text}
                            )
                            for c, v in zip(cchunks, vectors)
                        ],
                    )
                    stats["comments_ingested"] += 1

            if updated and (not latest_updated or updated > latest_updated):
                latest_updated = updated

        if latest_updated:
            sync.set_cursor("github", r, latest_updated)

    return stats
