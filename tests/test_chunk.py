"""Tests for the chunking module."""

from pke.chunk import chunk_chat_messages, chunk_code, chunk_markdown


class TestChunkMarkdown:
    def test_basic_markdown(self):
        text = "# Hello\n\nSome content here.\n\n## Section 2\n\nMore content."
        chunks = chunk_markdown(text, source="test:doc")
        assert len(chunks) >= 1
        assert all(c.id for c in chunks)
        assert all(c.text for c in chunks)

    def test_metadata_preserved(self):
        text = "# Title\n\nBody text."
        meta = {"source_type": "obsidian", "filepath": "test.md"}
        chunks = chunk_markdown(text, source="test:doc", base_metadata=meta)
        assert chunks[0].metadata["source_type"] == "obsidian"
        assert chunks[0].metadata["filepath"] == "test.md"

    def test_deterministic_ids(self):
        text = "# Title\n\nSome content."
        c1 = chunk_markdown(text, source="test:doc")
        c2 = chunk_markdown(text, source="test:doc")
        assert [c.id for c in c1] == [c.id for c in c2]

    def test_different_source_different_ids(self):
        text = "# Title\n\nSome content."
        c1 = chunk_markdown(text, source="test:doc1")
        c2 = chunk_markdown(text, source="test:doc2")
        assert c1[0].id != c2[0].id

    def test_long_text_splits(self):
        text = "# Title\n\n" + ("word " * 2000)
        chunks = chunk_markdown(text, source="test:long", chunk_size=500)
        assert len(chunks) > 1

    def test_empty_text(self):
        chunks = chunk_markdown("", source="test:empty")
        assert chunks == []


class TestChunkCode:
    def test_python_code(self):
        code = '''
def hello():
    print("hello")

def world():
    print("world")

class Foo:
    def bar(self):
        return 42
'''
        chunks = chunk_code(code, source="test:code.py")
        assert len(chunks) >= 1
        assert all(c.text.strip() for c in chunks)


class TestChunkChat:
    def test_basic_windowing(self):
        messages = [
            {"author": "alice", "content": f"Message {i}", "timestamp": f"2024-01-01T{i:02d}:00:00"}
            for i in range(20)
        ]
        chunks = chunk_chat_messages(messages, source="test:chat", window_size=7, overlap=2)
        assert len(chunks) >= 3
        # Check authors tracked
        assert "alice" in chunks[0].metadata["authors"]

    def test_empty_messages(self):
        chunks = chunk_chat_messages([], source="test:empty")
        assert chunks == []

    def test_small_window(self):
        messages = [{"author": "bob", "content": "Hi"}]
        chunks = chunk_chat_messages(messages, source="test:small", window_size=5)
        assert len(chunks) == 1

    def test_overlap(self):
        messages = [
            {"author": "a", "content": f"msg{i}"} for i in range(10)
        ]
        chunks = chunk_chat_messages(messages, source="test:overlap", window_size=5, overlap=2)
        # With window=5, overlap=2, step=3: positions 0,3,6,9 -> 4 windows
        assert len(chunks) == 4
