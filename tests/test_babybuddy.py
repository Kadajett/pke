"""Tests for BabyBuddy connector chunking and formatting logic."""

from __future__ import annotations

from pke.ingest.babybuddy import (
    _build_daily_summaries,
    _build_individual_chunks,
    _format_change,
    _format_feeding,
    _format_sleep,
    _format_tummy_time,
    _format_weight,
)


def test_format_feeding_basic():
    record = {"type": "breast milk", "method": "left breast", "start": "2026-03-10T08:00:00", "end": "2026-03-10T08:15:00", "duration": "00:15:00"}
    result = _format_feeding(record)
    assert "breast milk" in result
    assert "left breast" in result
    assert "00:15:00" in result


def test_format_feeding_with_amount():
    record = {"type": "formula", "amount": 4.0}
    result = _format_feeding(record)
    assert "formula" in result
    assert "4.0oz" in result


def test_format_sleep_nap():
    record = {"nap": True, "start": "2026-03-10T13:00:00", "end": "2026-03-10T14:30:00", "duration": "01:30:00"}
    result = _format_sleep(record)
    assert "nap" in result
    assert "01:30:00" in result


def test_format_sleep_night():
    record = {"nap": False, "start": "2026-03-10T20:00:00", "end": "2026-03-11T06:00:00"}
    result = _format_sleep(record)
    assert "night sleep" in result


def test_format_change_wet():
    record = {"wet": True, "solid": False, "time": "2026-03-10T10:00:00"}
    result = _format_change(record)
    assert "wet" in result
    assert "solid" not in result.split("wet")[0]  # solid not tagged


def test_format_change_solid_with_color():
    record = {"wet": False, "solid": True, "color": "yellow"}
    result = _format_change(record)
    assert "solid" in result
    assert "yellow" in result


def test_format_tummy_time():
    record = {"start": "2026-03-10T09:00:00", "end": "2026-03-10T09:05:00", "duration": "00:05:00"}
    result = _format_tummy_time(record)
    assert "Tummy time" in result
    assert "00:05:00" in result


def test_format_weight():
    record = {"weight": 3.5, "date": "2026-03-10"}
    result = _format_weight(record)
    assert "3.5" in result
    assert "2026-03-10" in result


def test_build_daily_summaries():
    records = {
        "feedings": [
            {"type": "breast milk", "start": "2026-03-10T08:00:00", "end": "2026-03-10T08:15:00"},
            {"type": "formula", "amount": 2.0, "start": "2026-03-10T12:00:00", "end": "2026-03-10T12:10:00"},
        ],
        "sleep": [
            {"nap": True, "start": "2026-03-10T13:00:00", "end": "2026-03-10T14:00:00"},
        ],
        "changes": [],
        "tummy-times": [],
        "notes": [],
        "weight": [],
    }
    chunks = _build_daily_summaries(records)
    assert len(chunks) == 1
    assert chunks[0].metadata["date"] == "2026-03-10"
    assert chunks[0].metadata["source_type"] == "babybuddy"
    assert chunks[0].metadata["record_type"] == "daily_summary"
    assert "2 entries" in chunks[0].text  # feedings count
    assert "1 entries" in chunks[0].text  # sleep count


def test_build_daily_summaries_multiple_days():
    records = {
        "feedings": [
            {"type": "formula", "start": "2026-03-10T08:00:00"},
            {"type": "formula", "start": "2026-03-11T08:00:00"},
        ],
        "sleep": [],
        "changes": [],
        "tummy-times": [],
        "notes": [],
        "weight": [],
    }
    chunks = _build_daily_summaries(records)
    assert len(chunks) == 2
    dates = [c.metadata["date"] for c in chunks]
    assert "2026-03-10" in dates
    assert "2026-03-11" in dates


def test_build_individual_chunks_notes():
    records = {
        "feedings": [],
        "sleep": [],
        "changes": [],
        "tummy-times": [],
        "notes": [{"note": "Theo smiled for the first time!", "time": "2026-03-14T10:00:00", "id": 1}],
        "weight": [{"weight": 3.8, "date": "2026-03-14", "id": 1}],
    }
    chunks = _build_individual_chunks(records)
    assert len(chunks) == 2
    types = {c.metadata["record_type"] for c in chunks}
    assert types == {"note", "weight"}
