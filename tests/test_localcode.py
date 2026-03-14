"""Tests for the local code ingestion connector."""

from __future__ import annotations

from pathlib import Path

import pytest

from pke.ingest.localcode import (
    SKIP_DIRS,
    _chunk_code_file,
    _extract_symbols,
    _list_code_files,
)
from langchain_text_splitters import Language


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a minimal fake repo structure."""
    # Python file
    (tmp_path / "main.py").write_text(
        'def hello():\n    print("hello")\n\nclass Foo:\n    pass\n'
    )
    # TypeScript file
    (tmp_path / "index.ts").write_text(
        "export function greet(name: string): string {\n  return `Hello ${name}`;\n}\n\n"
        "export class App {\n  run() {}\n}\n"
    )
    # Rust file
    (tmp_path / "lib.rs").write_text(
        "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n\n"
        "pub struct Config {\n    pub name: String,\n}\n"
    )
    # Markdown
    (tmp_path / "README.md").write_text("# My Project\n\nSome description.\n")
    # File that should be skipped (in node_modules)
    nm = tmp_path / "node_modules" / "dep"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = {}")
    # .git dir should be skipped
    gitdir = tmp_path / ".git"
    gitdir.mkdir()
    (gitdir / "config").write_text("[core]")
    return tmp_path


class TestExtractSymbols:
    def test_python_symbols(self):
        code = "def hello():\n    pass\n\nclass Foo:\n    pass\n"
        symbols = _extract_symbols(code, Language.PYTHON)
        assert "hello" in symbols
        assert "Foo" in symbols

    def test_typescript_symbols(self):
        code = "export function greet() {}\nexport class App {}\n"
        symbols = _extract_symbols(code, Language.TS)
        assert "greet" in symbols
        assert "App" in symbols

    def test_rust_symbols(self):
        code = "pub fn add() {}\npub struct Config {}\n"
        symbols = _extract_symbols(code, Language.RUST)
        assert "add" in symbols
        assert "Config" in symbols

    def test_empty_code(self):
        assert _extract_symbols("", Language.PYTHON) == []


class TestListCodeFiles:
    def test_finds_code_files(self, tmp_repo: Path):
        files = _list_code_files(tmp_repo)
        names = {f.name for f in files}
        assert "main.py" in names
        assert "index.ts" in names
        assert "lib.rs" in names
        assert "README.md" in names

    def test_skips_node_modules(self, tmp_repo: Path):
        files = _list_code_files(tmp_repo)
        rel_paths = [str(f.relative_to(tmp_repo)) for f in files]
        assert not any("node_modules" in p for p in rel_paths)

    def test_skips_git_dir(self, tmp_repo: Path):
        files = _list_code_files(tmp_repo)
        rel_paths = [str(f.relative_to(tmp_repo)) for f in files]
        assert not any(".git" in p for p in rel_paths)


class TestChunkCodeFile:
    def test_chunks_python(self, tmp_repo: Path):
        chunks = _chunk_code_file(tmp_repo / "main.py", tmp_repo, "test-repo")
        assert len(chunks) > 0
        assert all(c.metadata["source_type"] == "localcode" for c in chunks)
        assert all(c.metadata["repo"] == "test-repo" for c in chunks)
        assert all(c.metadata["language"] == "py" for c in chunks)

    def test_chunks_typescript(self, tmp_repo: Path):
        chunks = _chunk_code_file(tmp_repo / "index.ts", tmp_repo, "test-repo")
        assert len(chunks) > 0
        assert all(c.metadata["language"] == "ts" for c in chunks)

    def test_chunks_markdown(self, tmp_repo: Path):
        chunks = _chunk_code_file(tmp_repo / "README.md", tmp_repo, "test-repo")
        assert len(chunks) > 0
        assert all(c.metadata["source_type"] == "localcode" for c in chunks)

    def test_empty_file_returns_empty(self, tmp_repo: Path):
        empty = tmp_repo / "empty.py"
        empty.write_text("")
        chunks = _chunk_code_file(empty, tmp_repo, "test-repo")
        assert chunks == []


class TestSkipDirs:
    def test_expected_dirs_in_skip_set(self):
        assert "node_modules" in SKIP_DIRS
        assert ".git" in SKIP_DIRS
        assert "target" in SKIP_DIRS
        assert "__pycache__" in SKIP_DIRS
        assert ".semfora" in SKIP_DIRS
