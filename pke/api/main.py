"""PKE FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Query
from pydantic import BaseModel

from fastapi.responses import JSONResponse

from qdrant_client.models import Filter, FieldCondition, MatchValue, Range

from pke.config import settings
from pke.db.setup import ensure_collection, get_client
from pke.embed import EmbeddingError, embed_text


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure Qdrant collection exists on startup."""
    ensure_collection()
    yield


app = FastAPI(title="PKE — Personal Knowledge Engine", version="0.1.0", lifespan=lifespan)


class SearchResult(BaseModel):
    score: float
    text: str
    source_type: str
    metadata: dict


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    count: int


class IngestRequest(BaseModel):
    source: str  # "obsidian", "github", "discord"
    target: str | None = None  # vault path, repo, or channel ID
    full: bool = False


class IngestResponse(BaseModel):
    source: str
    stats: dict


class SourceInfo(BaseModel):
    source_type: str
    count: int
    last_sync: str | None = None


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}


@app.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., description="Search query text"),
    source_type: str | None = Query(None, description="Filter by source type"),
    collection: str | None = Query(None, description="Qdrant collection (default: pke, or pke-docs-core, pke-docs-gamedev, 'all')"),
    date_from: str | None = Query(None, description="Filter by date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="Filter by date (YYYY-MM-DD)"),
    limit: int = Query(10, ge=1, le=100, description="Max results"),
):
    """Search the knowledge base.

    When collection='all', searches pke + all devdocs collections and merges by score.
    """
    try:
        vector = embed_text(q)
    except EmbeddingError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    client = get_client()

    # Build filter conditions
    must_conditions: list[FieldCondition] = []
    if source_type:
        must_conditions.append(FieldCondition(key="source_type", match=MatchValue(value=source_type)))
    if date_from:
        must_conditions.append(FieldCondition(key="date", range=Range(gte=date_from)))
    if date_to:
        must_conditions.append(FieldCondition(key="date", range=Range(lte=date_to)))

    query_filter = Filter(must=must_conditions) if must_conditions else None

    # Determine which collections to search
    if collection == "all":
        from pke.ingest.devdocs import _get_collections_map

        existing = {c.name for c in client.get_collections().collections}
        target_collections = [settings.qdrant_collection]
        for cname in _get_collections_map():
            if cname in existing:
                target_collections.append(cname)
    elif collection and collection != settings.qdrant_collection:
        target_collections = [collection]
    else:
        target_collections = [settings.qdrant_collection]

    all_results = []
    for coll in target_collections:
        try:
            hits = client.query_points(
                collection_name=coll,
                query=vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            ).points
            all_results.extend(hits)
        except Exception:
            continue  # collection may not exist

    # Sort by score descending, take top `limit`
    all_results.sort(key=lambda r: r.score, reverse=True)
    all_results = all_results[:limit]

    return SearchResponse(
        query=q,
        results=[
            SearchResult(
                score=r.score,
                text=r.payload.get("text", ""),
                source_type=r.payload.get("source_type", "unknown"),
                metadata={k: v for k, v in r.payload.items() if k != "text"},
            )
            for r in all_results
        ],
        count=len(all_results),
    )


@app.post("/ingest", response_model=IngestResponse)
async def ingest(req: IngestRequest):
    """Trigger ingestion for a source."""
    if req.source == "obsidian":
        from pke.ingest.obsidian import ingest_obsidian

        stats = ingest_obsidian(vault_path=req.target, full=req.full)
    elif req.source == "github":
        from pke.ingest.github import ingest_github

        stats = ingest_github(repo=req.target, full=req.full)
    elif req.source == "discord":
        from pke.ingest.discord import ingest_discord

        stats = ingest_discord(channel_id=req.target, full=req.full)
    elif req.source == "devdocs":
        from pke.ingest.devdocs import ingest_devdocs

        stats = ingest_devdocs(target=req.target, full=req.full)
    else:
        return IngestResponse(source=req.source, stats={"error": f"Unknown source: {req.source}"})

    return IngestResponse(source=req.source, stats=stats)


@app.get("/sources")
async def sources():
    """List indexed sources with counts, last sync timestamps, and devdocs collection stats."""
    client = get_client()
    from pke.sync.state import SyncState

    sync = SyncState()

    source_types = ["obsidian", "github", "discord"]
    result: list[dict] = []

    for st in source_types:
        count_result = client.count(
            collection_name=settings.qdrant_collection,
            count_filter={"must": [{"key": "source_type", "match": {"value": st}}]},
            exact=True,
        )

        cursors = sync.get_all(st)
        last_sync = max(cursors.values()) if cursors else None

        result.append({"source_type": st, "count": count_result.count, "last_sync": last_sync})

    # Add devdocs collection stats
    from pke.ingest.devdocs import get_devdocs_stats

    devdocs_stats = get_devdocs_stats()
    for collection_name, info in devdocs_stats.items():
        result.append({
            "source_type": "devdocs",
            "collection": collection_name,
            "docsets": info["docsets"],
            "count": info["count"],
            "synced_files": info.get("synced_files", 0),
            "exists": info["exists"],
        })

    return result
