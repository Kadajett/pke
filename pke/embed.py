"""Embedding module — calls Ollama to embed text via nomic-embed-text."""

from __future__ import annotations

import ollama as ollama_client

from pke.config import settings


def embed_text(text: str) -> list[float]:
    """Embed a single text string, returning a vector."""
    client = ollama_client.Client(host=settings.ollama_url)
    response = client.embed(model=settings.ollama_embed_model, input=text)
    return response["embeddings"][0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts, returning a list of vectors."""
    if not texts:
        return []
    client = ollama_client.Client(host=settings.ollama_url)
    response = client.embed(model=settings.ollama_embed_model, input=texts)
    return response["embeddings"]
