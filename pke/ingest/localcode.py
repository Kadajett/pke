"""Local code ingestion pipeline — indexes source files from dev repos."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from langchain_text_splitters import Language
from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue, PointStruct

from pke.chunk import Chunk, _make_id, chunk_code, chunk_markdown
from pke.config import settings
from pke.db.setup import get_client
from pke.embed import embed_batch
from pke.sync.state import SyncState

# Directories to always skip
SKIP_DIRS = {
    "node_modules",
    ".git",
    "target",
    "dist",
    "build",
    "__pycache__",
    ".semfora",
    ".next",
    ".turbo",
    "vendor",
    ".venv",
    "venv",
}

# Supported file extensions → Language mapping
EXT_LANGUAGE: dict[str, Language | None] = {
    ".ts": Language.TS,
    ".tsx": Language.TS,
    ".js": Language.JS,
    ".jsx": Language.JS,
    ".py": Language.PYTHON,
    ".rs": Language.RUST,
    ".md": None,  # handled separately via chunk_markdown
}

# Regex patterns for extracting symbol names per language
_SYMBOL_PATTERNS: dict[Language, list[re.Pattern]] = {
    Language.TS: [
        re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE),
        re.compile(r"^export\s+(?:default\s+)?class\s+(\w+)", re.MULTILINE),
        re.compile(r"^export\s+(?:default\s+)?interface\s+(\w+)", re.MULTILINE),
        re.compile(r"^export\s+(?:default\s+)?(?:const|let)\s+(\w+)", re.MULTILINE),
    ],
    Language.JS: [
        re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE),
        re.compile(r"^export\s+(?:default\s+)?class\s+(\w+)", re.MULTILINE),
        re.compile(r"^export\s+(?:default\s+)?(?:const|let)\s+(\w+)", re.MULTILINE),
    ],
    Language.PYTHON: [
        re.compile(r"^(?:async\s+)?def\s+(\w+)", re.MULTILINE),
        re.compile(r"^class\s+(\w+)", re.MULTILINE),
    ],
    Language.RUST: [
        re.compile(r"^pub\s+(?:async\s+)?fn\s+(\w+)", re.MULTILINE),
        re.compile(r"^fn\s+(\w+)", re.MULTILINE),
        re.compile(r"^pub\s+struct\s+(\w+)", re.MULTILINE),
        re.compile(r"^pub\s+enum\s+(\w+)", re.MULTILINE),
        re.compile(r"^impl(?:<[^>]*>)?\s+(\w+)", re.MULTILINE),
    ],
}


def _file_hash(path: Path) -> str:
    """Compute SHA256 hash of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _extract_symbols(text: str, language: Language) -> list[str]:
    """Extract top-level symbol names from code text."""
    patterns = _SYMBOL_PATTERNS.get(language, [])
    symbols: list[str] = []
    for pat in patterns:
        symbols.extend(pat.findall(text))
    return symbols


def _get_gitignore_patterns(repo_root: Path) -> set[str]:
    """Get ignored paths using git check-ignore, returning set of relative paths."""
    # We'll use git ls-files to find tracked + untracked-but-not-ignored files instead
    return set()


def _list_code_files(repo_root: Path) -> list[Path]:
    """List code files in a repo, respecting .gitignore and skip dirs."""
    files: list[Path] = []

    # Try using git ls-files for .gitignore respect (only if repo_root is a git repo)
    try:
        # Verify this is actually a git repo root
        git_dir = repo_root / ".git"
        if git_dir.exists() and (git_dir / "HEAD").exists():
            result = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    if not line:
                        continue
                    p = repo_root / line
                    if p.suffix in EXT_LANGUAGE and p.is_file():
                        parts = set(Path(line).parts)
                        if not parts & SKIP_DIRS:
                            files.append(p)
                return files
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: walk manually
    for ext in EXT_LANGUAGE:
        for p in repo_root.rglob(f"*{ext}"):
            parts = set(p.relative_to(repo_root).parts)
            if not parts & SKIP_DIRS:
                files.append(p)

    return files


def _chunk_code_file(
    filepath: Path,
    repo_root: Path,
    repo_name: str,
) -> list[Chunk]:
    """Chunk a single code file with appropriate strategy."""
    rel_path = str(filepath.relative_to(repo_root))
    ext = filepath.suffix
    language = EXT_LANGUAGE.get(ext)

    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    if not text.strip():
        return []

    source = f"localcode:{repo_name}:{rel_path}"
    base_meta = {
        "source_type": "localcode",
        "repo": repo_name,
        "filepath": rel_path,
        "language": ext.lstrip("."),
    }

    # Markdown files use the markdown chunker
    if language is None and ext == ".md":
        return chunk_markdown(text, source=source, base_metadata=base_meta)

    # Code files — use langchain code splitter
    lang = language or Language.PYTHON  # fallback
    chunks = chunk_code(
        text,
        source=source,
        language=lang,
        base_metadata=base_meta,
        chunk_size=1200,
        chunk_overlap=100,
    )

    # Enrich chunks with symbol info
    for chunk in chunks:
        if language:
            symbols = _extract_symbols(chunk.text, language)
            if symbols:
                chunk.metadata["symbol_name"] = symbols[0] if len(symbols) == 1 else ", ".join(symbols)

        # Estimate line numbers from text position
        # (approximate — good enough for search context)
        start_idx = text.find(chunk.text[:80])
        if start_idx >= 0:
            line_start = text[:start_idx].count("\n") + 1
            line_end = line_start + chunk.text.count("\n")
            chunk.metadata["line_start"] = line_start
            chunk.metadata["line_end"] = line_end

    return chunks


def ingest_localcode(target: str | None = None, full: bool = False) -> dict:
    """Ingest source code from local repositories.

    Args:
        target: Specific repo path to index. If None, uses all configured repos.
        full: If True, re-ingest everything ignoring sync state.

    Returns:
        Summary dict with counts.
    """
    if target:
        repo_paths = [Path(target)]
    else:
        repo_paths = [Path(r) for r in settings.local_repos_list]

    if not repo_paths:
        return {"error": "No repos configured. Set PKE_LOCAL_REPOS."}

    sync = SyncState()
    client = get_client()
    collection = settings.qdrant_collection
    stats = {"repos": 0, "files_scanned": 0, "files_ingested": 0, "files_skipped": 0, "chunks": 0}

    for repo_path in repo_paths:
        if not repo_path.exists():
            continue

        repo_name = repo_path.name
        stats["repos"] += 1

        if full:
            # Clear existing data for this repo
            client.delete(
                collection_name=collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(key="source_type", match=MatchValue(value="localcode")),
                            FieldCondition(key="repo", match=MatchValue(value=repo_name)),
                        ]
                    )
                ),
            )
            # Clear sync state for this repo's files
            stored = sync.get_all("localcode")
            for key in stored:
                if key.startswith(f"{repo_name}:"):
                    sync.delete("localcode", key)

        stored_hashes = sync.get_all("localcode")
        code_files = _list_code_files(repo_path)
        stats["files_scanned"] += len(code_files)

        # Track current files for deletion detection
        current_keys: set[str] = set()

        for filepath in code_files:
            rel_path = str(filepath.relative_to(repo_path))
            sync_key = f"{repo_name}:{rel_path}"
            current_keys.add(sync_key)

            file_h = _file_hash(filepath)

            # Skip unchanged files
            if not full and stored_hashes.get(sync_key) == file_h:
                stats["files_skipped"] += 1
                continue

            chunks = _chunk_code_file(filepath, repo_path, repo_name)
            if not chunks:
                stats["files_skipped"] += 1
                continue

            # Delete old chunks for this file
            client.delete(
                collection_name=collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(key="source_type", match=MatchValue(value="localcode")),
                            FieldCondition(key="repo", match=MatchValue(value=repo_name)),
                            FieldCondition(key="filepath", match=MatchValue(value=rel_path)),
                        ]
                    )
                ),
            )

            # Embed and upsert
            vectors = embed_batch([c.text for c in chunks])
            client.upsert(
                collection_name=collection,
                points=[
                    PointStruct(id=c.id, vector=v, payload={**c.metadata, "text": c.text})
                    for c, v in zip(chunks, vectors)
                ],
            )

            sync.set_cursor("localcode", sync_key, file_h)
            stats["files_ingested"] += 1
            stats["chunks"] += len(chunks)

        # Detect deleted files
        for key in stored_hashes:
            if key.startswith(f"{repo_name}:") and key not in current_keys:
                rel = key[len(f"{repo_name}:"):]
                client.delete(
                    collection_name=collection,
                    points_selector=FilterSelector(
                        filter=Filter(
                            must=[
                                FieldCondition(key="source_type", match=MatchValue(value="localcode")),
                                FieldCondition(key="repo", match=MatchValue(value=repo_name)),
                                FieldCondition(key="filepath", match=MatchValue(value=rel)),
                            ]
                        )
                    ),
                )
                sync.delete("localcode", key)

    return stats
