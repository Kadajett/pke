"""BabyBuddy ingestion pipeline — indexes feeding, sleep, diaper, and other baby data."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

import httpx
from qdrant_client.models import PointStruct

from pke.chunk import Chunk, _make_id
from pke.config import settings
from pke.db.setup import get_client
from pke.embed import embed_batch
from pke.sync.state import SyncState

log = logging.getLogger(__name__)

# BabyBuddy API endpoints we ingest
_ENDPOINTS = {
    "feedings": "/api/feedings/",
    "sleep": "/api/sleep/",
    "changes": "/api/changes/",
    "tummy-times": "/api/tummy-times/",
    "notes": "/api/notes/",
    "weight": "/api/weight/",
}


def _bb_headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if settings.babybuddy_api_key:
        headers["Authorization"] = f"Token {settings.babybuddy_api_key}"
    if settings.babybuddy_host:
        headers["Host"] = settings.babybuddy_host
    return headers


def _fetch_all(endpoint: str, since: str | None = None) -> list[dict]:
    """Fetch all records from a BabyBuddy endpoint with pagination."""
    base = settings.babybuddy_url.rstrip('/')
    # Endpoints already have /api/ prefix, base might too — normalize
    if base.endswith('/api'):
        base = base[:-4]
    url = f"{base}{endpoint}"
    ordering = "-start" if endpoint != "/api/weight/" else "-date"
    params: dict = {"limit": 100, "offset": 0}
    if since:
        # BabyBuddy filters by date fields; use ordering + client-side filter
        params["ordering"] = ordering

    all_records: list[dict] = []
    with httpx.Client(headers=_bb_headers(), timeout=30) as client:
        while url:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            results = data.get("results", data) if isinstance(data, dict) else data
            if not isinstance(results, list):
                break

            for record in results:
                # Client-side incremental filter
                record_ts = _get_record_timestamp(record)
                if since and record_ts and record_ts <= since:
                    # Past our cursor — stop (records are ordered desc)
                    url = None
                    break
                all_records.append(record)

            # Follow pagination — preserve ordering param in case next URL omits it
            if isinstance(data, dict) and data.get("next"):
                url = data["next"]
                params = {"ordering": ordering} if since else {}
            else:
                url = None

    return all_records


def _get_record_timestamp(record: dict) -> str | None:
    """Extract the most relevant timestamp from a record."""
    for field in ("start", "date", "time", "created"):
        if field in record and record[field]:
            return record[field]
    return None


def _format_feeding(record: dict) -> str:
    """Format a feeding record into readable text."""
    parts = [f"Feeding: {record.get('type', 'unknown')} feed"]
    if record.get("method"):
        parts.append(f"method: {record['method']}")
    if record.get("amount"):
        parts.append(f"amount: {record['amount']}oz")
    if record.get("start") and record.get("end"):
        parts.append(f"from {record['start']} to {record['end']}")
    if record.get("duration"):
        parts.append(f"duration: {record['duration']}")
    if record.get("notes"):
        parts.append(f"notes: {record['notes']}")
    return " | ".join(parts)


def _format_sleep(record: dict) -> str:
    parts = [f"Sleep: {'nap' if record.get('nap') else 'night sleep'}"]
    if record.get("start") and record.get("end"):
        parts.append(f"from {record['start']} to {record['end']}")
    if record.get("duration"):
        parts.append(f"duration: {record['duration']}")
    if record.get("notes"):
        parts.append(f"notes: {record['notes']}")
    return " | ".join(parts)


def _format_change(record: dict) -> str:
    parts = ["Diaper change:"]
    tags = []
    if record.get("wet"):
        tags.append("wet")
    if record.get("solid"):
        tags.append("solid")
    if record.get("color"):
        tags.append(f"color: {record['color']}")
    parts.append(", ".join(tags) if tags else "dry")
    if record.get("time"):
        parts.append(f"at {record['time']}")
    if record.get("notes"):
        parts.append(f"notes: {record['notes']}")
    return " | ".join(parts)


def _format_tummy_time(record: dict) -> str:
    parts = ["Tummy time"]
    if record.get("start") and record.get("end"):
        parts.append(f"from {record['start']} to {record['end']}")
    if record.get("duration"):
        parts.append(f"duration: {record['duration']}")
    if record.get("milestone"):
        parts.append(f"milestone: {record['milestone']}")
    return " | ".join(parts)


def _format_weight(record: dict) -> str:
    parts = [f"Weight: {record.get('weight', '?')}"]
    if record.get("date"):
        parts.append(f"on {record['date']}")
    if record.get("notes"):
        parts.append(f"notes: {record['notes']}")
    return " | ".join(parts)


def _format_note(record: dict) -> str:
    text = record.get("note", "")
    date = record.get("time", record.get("date", ""))
    return f"Note ({date}): {text}"


_FORMATTERS = {
    "feedings": _format_feeding,
    "sleep": _format_sleep,
    "changes": _format_change,
    "tummy-times": _format_tummy_time,
    "weight": _format_weight,
    "notes": _format_note,
}


def _extract_date(record: dict) -> str:
    """Extract date (YYYY-MM-DD) from a record."""
    for field in ("start", "date", "time"):
        val = record.get(field)
        if val:
            return val[:10]  # Take YYYY-MM-DD portion
    return "unknown"


def _build_daily_summaries(
    records_by_type: dict[str, list[dict]],
) -> list[Chunk]:
    """Aggregate records into daily summary chunks."""
    # Group all records by date
    by_date: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))

    for record_type, records in records_by_type.items():
        for record in records:
            date = _extract_date(record)
            by_date[date][record_type].append(record)

    chunks: list[Chunk] = []
    for date in sorted(by_date.keys()):
        day_data = by_date[date]
        lines = [f"# Daily Summary for Theo — {date}\n"]

        for record_type, records in sorted(day_data.items()):
            formatter = _FORMATTERS.get(record_type, str)
            count = len(records)
            label = "entry" if count == 1 else "entries"
            lines.append(f"\n## {record_type.replace('-', ' ').title()} ({count} {label})")
            for record in records:
                lines.append(f"- {formatter(record)}")

        text = "\n".join(lines)
        source = f"babybuddy:daily:{date}"
        chunk_id = _make_id(source, 0, text)
        chunks.append(
            Chunk(
                id=chunk_id,
                text=text,
                metadata={
                    "source_type": "babybuddy",
                    "record_type": "daily_summary",
                    "date": date,
                    "child_name": "Theo",
                    "source": source,
                },
            )
        )

    return chunks


def _build_individual_chunks(
    records_by_type: dict[str, list[dict]],
) -> list[Chunk]:
    """Create individual chunks for notable entries (notes, weight milestones)."""
    chunks: list[Chunk] = []

    # Notes get individual chunks
    for record in records_by_type.get("notes", []):
        text = _format_note(record)
        date = _extract_date(record)
        source = f"babybuddy:note:{record.get('id', date)}"
        chunks.append(
            Chunk(
                id=_make_id(source, 0, text),
                text=text,
                metadata={
                    "source_type": "babybuddy",
                    "record_type": "note",
                    "date": date,
                    "child_name": "Theo",
                    "source": source,
                },
            )
        )

    # Weight measurements get individual chunks (milestones)
    for record in records_by_type.get("weight", []):
        text = _format_weight(record)
        date = _extract_date(record)
        source = f"babybuddy:weight:{record.get('id', date)}"
        chunks.append(
            Chunk(
                id=_make_id(source, 0, text),
                text=text,
                metadata={
                    "source_type": "babybuddy",
                    "record_type": "weight",
                    "date": date,
                    "child_name": "Theo",
                    "source": source,
                },
            )
        )

    return chunks


def ingest_babybuddy(full: bool = False) -> dict:
    """Ingest baby data from BabyBuddy into PKE.

    Args:
        full: If True, re-ingest everything (ignore sync cursor).

    Returns:
        Summary dict with counts.
    """
    if not settings.babybuddy_url:
        return {"error": "BabyBuddy not configured. Set PKE_BABYBUDDY_URL."}

    if not settings.babybuddy_api_key:
        log.warning("PKE_BABYBUDDY_API_KEY not set — requests will be unauthenticated")
        return {"error": "BabyBuddy API key not configured. Set PKE_BABYBUDDY_API_KEY."}

    sync = SyncState()
    client = get_client()
    collection = settings.qdrant_collection

    since = None if full else sync.get_cursor("babybuddy", "last_sync")

    # Fetch all record types
    records_by_type: dict[str, list[dict]] = {}
    stats: dict[str, int] = {"records_fetched": 0}

    for record_type, endpoint in _ENDPOINTS.items():
        try:
            records = _fetch_all(endpoint, since=since)
            records_by_type[record_type] = records
            stats[f"{record_type}_fetched"] = len(records)
            stats["records_fetched"] += len(records)
        except httpx.HTTPError as exc:
            stats[f"{record_type}_error"] = str(exc)
            records_by_type[record_type] = []

    if stats["records_fetched"] == 0:
        stats["message"] = "No new records to ingest."
        return stats

    # Build chunks
    daily_chunks = _build_daily_summaries(records_by_type)
    individual_chunks = _build_individual_chunks(records_by_type)
    all_chunks = daily_chunks + individual_chunks

    stats["daily_summaries"] = len(daily_chunks)
    stats["individual_chunks"] = len(individual_chunks)
    stats["total_chunks"] = len(all_chunks)

    if not all_chunks:
        return stats

    # Embed and upsert in batches
    batch_size = 32
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i : i + batch_size]
        vectors = embed_batch([c.text for c in batch])
        client.upsert(
            collection_name=collection,
            points=[
                PointStruct(id=c.id, vector=v, payload={**c.metadata, "text": c.text})
                for c, v in zip(batch, vectors)
            ],
        )

    # Update sync cursor
    now = datetime.now(UTC).isoformat()
    sync.set_cursor("babybuddy", "last_sync", now)

    stats["status"] = "ok"
    return stats
