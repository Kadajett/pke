"""Obsidian vault ingestion pipeline."""

from __future__ import annotations

import hashlib
from pathlib import Path

import frontmatter

from pke.chunk import chunk_markdown
from pke.config import settings
from pke.db.setup import get_client
from pke.embed import embed_batch
from pke.sync.state import SyncState


def _file_hash(path: Path) -> str:
    """Compute SHA256 hash of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ingest_obsidian(vault_path: str | None = None, full: bool = False) -> dict:
    """Ingest markdown files from an Obsidian vault.

    Args:
        vault_path: Path to the vault. Defaults to config.
        full: If True, re-ingest everything ignoring sync state.

    Returns:
        Summary dict with counts.
    """
    vault = Path(vault_path or settings.obsidian_vault_path)
    if not vault.exists():
        raise FileNotFoundError(f"Vault not found: {vault}")

    sync = SyncState()
    client = get_client()
    collection = settings.qdrant_collection

    if full:
        sync.clear("obsidian")

    stored_hashes = sync.get_all("obsidian")
    md_files = list(vault.rglob("*.md"))

    stats = {"scanned": len(md_files), "ingested": 0, "skipped": 0, "deleted": 0}

    # Detect deleted files
    current_paths = {str(f.relative_to(vault)) for f in md_files}
    for stored_path in stored_hashes:
        if stored_path not in current_paths:
            # Delete chunks for removed file
            client.delete(
                collection_name=collection,
                points_selector={"filter": {
                    "must": [
                        {"key": "source_type", "match": {"value": "obsidian"}},
                        {"key": "filepath", "match": {"value": stored_path}},
                    ]
                }},
            )
            sync.delete("obsidian", stored_path)
            stats["deleted"] += 1

    for md_file in md_files:
        rel_path = str(md_file.relative_to(vault))
        file_h = _file_hash(md_file)

        # Skip unchanged files
        if stored_hashes.get(rel_path) == file_h:
            stats["skipped"] += 1
            continue

        # Parse file
        post = frontmatter.load(str(md_file))
        meta = {
            "source_type": "obsidian",
            "filepath": rel_path,
            "date": str(post.metadata.get("date", "")),
            "tags": post.metadata.get("tags", []),
        }

        # Chunk
        chunks = chunk_markdown(post.content, source=f"obsidian:{rel_path}", base_metadata=meta)
        if not chunks:
            stats["skipped"] += 1
            continue

        # Delete old chunks for this file before reinserting
        client.delete(
            collection_name=collection,
            points_selector={"filter": {
                "must": [
                    {"key": "source_type", "match": {"value": "obsidian"}},
                    {"key": "filepath", "match": {"value": rel_path}},
                ]
            }},
        )

        # Embed and upsert
        vectors = embed_batch([c.text for c in chunks])
        points = []
        for chunk, vector in zip(chunks, vectors):
            points.append({
                "id": chunk.id,
                "vector": vector,
                "payload": {**chunk.metadata, "text": chunk.text},
            })

        from qdrant_client.models import PointStruct

        client.upsert(
            collection_name=collection,
            points=[
                PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
                for p in points
            ],
        )

        sync.set_cursor("obsidian", rel_path, file_h)
        stats["ingested"] += 1

    return stats
