"""DevDocs HTML ingestion pipeline — indexes curated doc sets into separate Qdrant collections."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from pke.chunk import chunk_markdown
from pke.config import settings
from pke.db.setup import get_client
from pke.embed import embed_batch
from pke.sync.state import SyncState

logger = logging.getLogger(__name__)

# Default collection → docset mapping
_DEFAULT_COLLECTIONS: dict[str, list[str]] = {
    "pke-docs-core": ["rust", "typescript", "node", "kubernetes", "docker", "css", "dom", "bash"],
    "pke-docs-gamedev": ["bevy", "tauri", "react", "react_native"],
}


def _get_collections_map() -> dict[str, list[str]]:
    """Load collection mapping from env or defaults."""
    raw = settings.devdocs_collections
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid PKE_DEVDOCS_COLLECTIONS JSON, using defaults")
    return _DEFAULT_COLLECTIONS.copy()


def _file_hash(path: Path) -> str:
    """Compute SHA256 hash of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _html_to_markdown(html: str) -> str:
    """Convert DevDocs HTML to clean markdown text.

    DevDocs pages are well-structured with headings, code blocks, and paragraphs.
    We convert to a markdown-like format that chunks well.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove attribution divs and script/style
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    for div in soup.find_all("div", class_="_attribution"):
        div.decompose()

    lines: list[str] = []

    for element in soup.descendants:
        if not isinstance(element, Tag):
            continue

        if element.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(element.name[1])
            text = element.get_text(strip=True)
            if text:
                lines.append(f"\n{'#' * level} {text}\n")
        elif element.name == "pre":
            code = element.get_text()
            lang = ""
            code_tag = element.find("code")
            if code_tag and isinstance(code_tag, Tag):
                classes = code_tag.get("class", [])
                if isinstance(classes, list):
                    for cls in classes:
                        if isinstance(cls, str) and cls.startswith("language-"):
                            lang = cls.replace("language-", "")
                            break
                        # DevDocs uses data-language attr
                lang = lang or (code_tag.get("data-language", "") if isinstance(code_tag, Tag) else "")
            lines.append(f"\n```{lang}\n{code.strip()}\n```\n")
        elif element.name == "p" and not element.find_parent("pre"):
            text = element.get_text(strip=True)
            if text:
                lines.append(f"\n{text}\n")
        elif element.name == "li" and not element.find_parent("pre"):
            text = element.get_text(strip=True)
            if text:
                lines.append(f"- {text}")

    result = "\n".join(lines)
    # Collapse excessive newlines
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _ensure_collection(collection_name: str) -> None:
    """Create a Qdrant collection if it doesn't exist."""
    client = get_client()
    existing = [c.name for c in client.get_collections().collections]
    if collection_name in existing:
        return

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(
            size=settings.qdrant_vector_size,
            distance=Distance.COSINE,
        ),
    )
    for field, schema_type in [
        ("source_type", PayloadSchemaType.KEYWORD),
        ("filepath", PayloadSchemaType.KEYWORD),
        ("docset", PayloadSchemaType.KEYWORD),
        ("collection", PayloadSchemaType.KEYWORD),
    ]:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=schema_type,
        )
    logger.info("Created Qdrant collection: %s", collection_name)


def _docsets_for_collection(collection_name: str) -> list[str]:
    """Get the list of docsets for a target collection."""
    collections_map = _get_collections_map()
    if collection_name not in collections_map:
        raise ValueError(
            f"Unknown collection '{collection_name}'. "
            f"Available: {list(collections_map.keys())}"
        )
    return collections_map[collection_name]


def ingest_devdocs(
    target: str | None = None,
    full: bool = False,
) -> dict:
    """Ingest DevDocs HTML files into a specific Qdrant collection.

    Args:
        target: Collection name (e.g. 'pke-docs-core'). If None, indexes all collections.
        full: If True, re-ingest everything ignoring sync state.

    Returns:
        Summary dict with counts.
    """
    docs_root = Path(settings.devdocs_path)
    if not docs_root.exists():
        raise FileNotFoundError(f"DevDocs path not found: {docs_root}")

    collections_map = _get_collections_map()

    # Determine which collections to index
    if target:
        if target not in collections_map:
            return {"error": f"Unknown collection '{target}'. Available: {list(collections_map.keys())}"}
        targets = {target: collections_map[target]}
    else:
        targets = collections_map

    total_stats: dict[str, int] = {"scanned": 0, "ingested": 0, "skipped": 0, "deleted": 0}
    collection_stats: dict[str, dict] = {}

    for collection_name, docsets in targets.items():
        _ensure_collection(collection_name)
        stats = _ingest_collection(docs_root, collection_name, docsets, full=full)
        collection_stats[collection_name] = stats
        for k in total_stats:
            total_stats[k] += stats.get(k, 0)

    return {**total_stats, "collections": collection_stats}


def _ingest_collection(
    docs_root: Path,
    collection_name: str,
    docsets: list[str],
    full: bool = False,
) -> dict:
    """Ingest a set of docsets into a single Qdrant collection."""
    sync = SyncState()
    client = get_client()
    source_key = f"devdocs:{collection_name}"

    if full:
        sync.clear(source_key)

    stored_hashes = sync.get_all(source_key)
    stats = {"scanned": 0, "ingested": 0, "skipped": 0, "deleted": 0}

    all_files: list[tuple[str, Path, str]] = []  # (docset, path, rel_key)

    for docset in docsets:
        docset_path = docs_root / docset
        if not docset_path.exists():
            logger.warning("Docset not found: %s", docset_path)
            continue

        html_files = list(docset_path.rglob("*.html"))
        for html_file in html_files:
            rel_path = str(html_file.relative_to(docs_root))
            all_files.append((docset, html_file, rel_path))

    stats["scanned"] = len(all_files)

    # Detect deleted files
    current_keys = {rel_key for _, _, rel_key in all_files}
    for stored_key in list(stored_hashes.keys()):
        if stored_key not in current_keys:
            client.delete(
                collection_name=collection_name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(key="source_type", match=MatchValue(value="devdocs")),
                            FieldCondition(key="filepath", match=MatchValue(value=stored_key)),
                        ]
                    )
                ),
            )
            sync.delete(source_key, stored_key)
            stats["deleted"] += 1

    # Ingest files in batches
    batch_points: list[PointStruct] = []
    batch_keys: list[tuple[str, str]] = []  # (rel_key, file_hash)
    BATCH_SIZE = 50

    for docset, html_file, rel_key in all_files:
        file_h = _file_hash(html_file)

        if stored_hashes.get(rel_key) == file_h:
            stats["skipped"] += 1
            continue

        try:
            html_content = html_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            logger.warning("Failed to read %s", html_file)
            stats["skipped"] += 1
            continue

        markdown = _html_to_markdown(html_content)
        if not markdown or len(markdown) < 20:
            stats["skipped"] += 1
            continue

        meta = {
            "source_type": "devdocs",
            "docset": docset,
            "collection": collection_name,
            "filepath": rel_key,
        }

        chunks = chunk_markdown(
            markdown,
            source=f"devdocs:{collection_name}:{rel_key}",
            base_metadata=meta,
        )
        if not chunks:
            stats["skipped"] += 1
            continue

        # Delete old chunks for this file
        client.delete(
            collection_name=collection_name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(key="source_type", match=MatchValue(value="devdocs")),
                        FieldCondition(key="filepath", match=MatchValue(value=rel_key)),
                    ]
                )
            ),
        )

        vectors = embed_batch([c.text for c in chunks])
        for chunk, vector in zip(chunks, vectors):
            batch_points.append(
                PointStruct(
                    id=chunk.id,
                    vector=vector,
                    payload={**chunk.metadata, "text": chunk.text},
                )
            )

        batch_keys.append((rel_key, file_h))
        stats["ingested"] += 1

        # Flush batch
        if len(batch_points) >= BATCH_SIZE:
            client.upsert(collection_name=collection_name, points=batch_points)
            for key, h in batch_keys:
                sync.set_cursor(source_key, key, h)
            batch_points.clear()
            batch_keys.clear()

    # Flush remaining
    if batch_points:
        client.upsert(collection_name=collection_name, points=batch_points)
        for key, h in batch_keys:
            sync.set_cursor(source_key, key, h)

    return stats


def get_devdocs_stats() -> dict[str, dict]:
    """Get stats for all devdocs collections."""
    client = get_client()
    collections_map = _get_collections_map()
    existing = {c.name for c in client.get_collections().collections}
    sync = SyncState()

    result = {}
    for collection_name, docsets in collections_map.items():
        if collection_name not in existing:
            result[collection_name] = {"exists": False, "docsets": docsets, "count": 0}
            continue

        count_result = client.count(collection_name=collection_name, exact=True)
        cursors = sync.get_all(f"devdocs:{collection_name}")

        result[collection_name] = {
            "exists": True,
            "docsets": docsets,
            "count": count_result.count,
            "synced_files": len(cursors),
        }

    return result
