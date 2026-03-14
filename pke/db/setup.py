"""Qdrant collection setup and management."""

from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

from pke.config import settings


_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    """Get a singleton Qdrant client instance."""
    global _client
    if _client is None:
        _client = QdrantClient(url=settings.qdrant_url)
    return _client


def ensure_collection(client: QdrantClient | None = None) -> None:
    """Create the PKE collection if it doesn't exist."""
    client = client or get_client()
    collections = [c.name for c in client.get_collections().collections]

    if settings.qdrant_collection in collections:
        return

    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=VectorParams(
            size=settings.qdrant_vector_size,
            distance=Distance.COSINE,
        ),
    )

    # Create payload indexes for filtering
    for field, schema_type in [
        ("source_type", PayloadSchemaType.KEYWORD),
        ("filepath", PayloadSchemaType.KEYWORD),
        ("date", PayloadSchemaType.KEYWORD),
        ("author", PayloadSchemaType.KEYWORD),
        ("url", PayloadSchemaType.KEYWORD),
        ("tags", PayloadSchemaType.KEYWORD),
    ]:
        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name=field,
            field_schema=schema_type,
        )


if __name__ == "__main__":
    ensure_collection()
    print(f"Collection '{settings.qdrant_collection}' ready.")
