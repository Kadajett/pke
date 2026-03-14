"""Tests for sync state module."""

import tempfile
from pathlib import Path

from pke.sync.state import SyncState


def test_get_set_cursor():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        s = SyncState(db_path=db_path)

        assert s.get_cursor("obsidian", "test.md") is None
        s.set_cursor("obsidian", "test.md", "abc123")
        assert s.get_cursor("obsidian", "test.md") == "abc123"

        # Update
        s.set_cursor("obsidian", "test.md", "def456")
        assert s.get_cursor("obsidian", "test.md") == "def456"


def test_get_all():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        s = SyncState(db_path=db_path)

        s.set_cursor("obsidian", "a.md", "hash1")
        s.set_cursor("obsidian", "b.md", "hash2")
        s.set_cursor("github", "repo1", "cursor1")

        all_obsidian = s.get_all("obsidian")
        assert len(all_obsidian) == 2
        assert all_obsidian["a.md"] == "hash1"

        all_github = s.get_all("github")
        assert len(all_github) == 1


def test_delete_and_clear():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        s = SyncState(db_path=db_path)

        s.set_cursor("obsidian", "a.md", "h1")
        s.set_cursor("obsidian", "b.md", "h2")

        s.delete("obsidian", "a.md")
        assert s.get_cursor("obsidian", "a.md") is None
        assert s.get_cursor("obsidian", "b.md") == "h2"

        s.clear("obsidian")
        assert s.get_all("obsidian") == {}
