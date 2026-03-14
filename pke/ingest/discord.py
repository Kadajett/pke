"""Discord ingestion pipeline — indexes channel message history."""

from __future__ import annotations

import httpx

from pke.chunk import chunk_chat_messages
from pke.config import settings
from pke.db.setup import get_client
from pke.embed import embed_batch
from pke.sync.state import SyncState


def _discord_headers() -> dict:
    """Get Discord API headers."""
    token = settings.discord_bot_token
    if not token:
        raise ValueError("PKE_DISCORD_BOT_TOKEN not set.")
    return {"Authorization": f"Bot {token}"}


def _fetch_messages(channel_id: str, after: str | None = None, limit: int = 100) -> list[dict]:
    """Fetch messages from a Discord channel."""
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    params: dict = {"limit": limit}
    if after:
        params["after"] = after

    all_messages: list[dict] = []
    with httpx.Client(headers=_discord_headers(), timeout=30) as client:
        while True:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            all_messages.extend(batch)
            if len(batch) < limit:
                break
            # Discord returns newest first; we want oldest first
            params["after"] = batch[-1]["id"]

    # Sort oldest first
    all_messages.sort(key=lambda m: m["id"])
    return all_messages


def ingest_discord(channel_id: str | None = None, full: bool = False) -> dict:
    """Ingest messages from Discord channels.

    Args:
        channel_id: Specific channel ID. If None, uses all configured channels.
        full: If True, re-ingest everything.

    Returns:
        Summary dict with counts.
    """
    channels = [channel_id] if channel_id else settings.discord_channel_ids
    if not channels:
        return {"error": "No channels configured. Set PKE_DISCORD_CHANNEL_IDS or pass --channel."}

    sync = SyncState()
    qdrant = get_client()
    collection = settings.qdrant_collection
    stats = {"channels": len(channels), "chunks_ingested": 0}

    for ch_id in channels:
        after = None if full else sync.get_cursor("discord", ch_id)

        raw_messages = _fetch_messages(ch_id, after=after)
        if not raw_messages:
            continue

        # Convert to chunker format
        messages = [
            {
                "author": m["author"].get("username", "unknown"),
                "content": m.get("content", ""),
                "timestamp": m.get("timestamp", ""),
            }
            for m in raw_messages
            if m.get("content", "").strip()  # skip empty/embed-only messages
        ]

        if not messages:
            continue

        meta = {
            "source_type": "discord",
            "channel_id": ch_id,
            "thread_id": raw_messages[0].get("thread", {}).get("id", ""),
        }

        chunks = chunk_chat_messages(
            messages, source=f"discord:{ch_id}", base_metadata=meta
        )

        if chunks:
            vectors = embed_batch([c.text for c in chunks])
            from qdrant_client.models import PointStruct

            qdrant.upsert(
                collection_name=collection,
                points=[
                    PointStruct(id=c.id, vector=v, payload={**c.metadata, "text": c.text})
                    for c, v in zip(chunks, vectors)
                ],
            )
            stats["chunks_ingested"] += len(chunks)

        # Store the latest message ID as cursor
        latest_id = raw_messages[-1]["id"]
        sync.set_cursor("discord", ch_id, latest_id)

    return stats
