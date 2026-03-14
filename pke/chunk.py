"""Content-type-aware chunking module with deterministic IDs for upsert idempotency."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from langchain_text_splitters import (
    Language,
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)


@dataclass
class Chunk:
    """A text chunk with a deterministic ID and metadata."""

    id: str
    text: str
    metadata: dict


def _make_id(source: str, position: int, text: str) -> str:
    """Create a deterministic chunk ID from source, position, and content."""
    raw = f"{source}::{position}::{text[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def chunk_markdown(
    text: str,
    source: str,
    base_metadata: dict | None = None,
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
) -> list[Chunk]:
    """Split markdown text into heading-aware chunks.

    First splits by headers to preserve structure, then splits large sections
    into ~500-token chunks (~2000 chars).
    """
    headers = [
        ("#", "h1"),
        ("##", "h2"),
        ("###", "h3"),
    ]
    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers)
    header_docs = header_splitter.split_text(text)

    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    chunks: list[Chunk] = []
    for i, doc in enumerate(header_docs):
        sub_texts = char_splitter.split_text(doc.page_content)
        for j, sub_text in enumerate(sub_texts):
            pos = i * 1000 + j
            meta = {**(base_metadata or {}), **doc.metadata}
            chunks.append(Chunk(
                id=_make_id(source, pos, sub_text),
                text=sub_text,
                metadata=meta,
            ))
    return chunks


def chunk_code(
    text: str,
    source: str,
    language: Language = Language.PYTHON,
    base_metadata: dict | None = None,
    chunk_size: int = 1200,
    chunk_overlap: int = 100,
) -> list[Chunk]:
    """Split code into language-aware chunks (~300 tokens)."""
    splitter = RecursiveCharacterTextSplitter.from_language(
        language=language,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    texts = splitter.split_text(text)
    return [
        Chunk(
            id=_make_id(source, i, t),
            text=t,
            metadata={**(base_metadata or {})},
        )
        for i, t in enumerate(texts)
    ]


def chunk_chat_messages(
    messages: list[dict],
    source: str,
    base_metadata: dict | None = None,
    window_size: int = 7,
    overlap: int = 2,
) -> list[Chunk]:
    """Window chat messages into conversation chunks.

    Each message should have 'author' and 'content' keys, optionally 'timestamp'.
    """
    if not messages:
        return []

    chunks: list[Chunk] = []
    step = max(1, window_size - overlap)

    for i in range(0, len(messages), step):
        window = messages[i : i + window_size]
        lines = []
        authors = set()
        for msg in window:
            author = msg.get("author", "unknown")
            authors.add(author)
            ts = msg.get("timestamp", "")
            prefix = f"[{ts}] {author}" if ts else author
            lines.append(f"{prefix}: {msg.get('content', '')}")

        text = "\n".join(lines)
        meta = {
            **(base_metadata or {}),
            "authors": list(authors),
            "message_count": len(window),
        }
        chunks.append(Chunk(
            id=_make_id(source, i, text),
            text=text,
            metadata=meta,
        ))

    return chunks
