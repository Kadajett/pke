"""Configuration for PKE — loaded from environment variables with sensible defaults."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """PKE application settings."""

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "pke"
    qdrant_vector_size: int = 768

    # Ollama
    ollama_url: str = "http://localhost:11434"
    ollama_embed_model: str = "nomic-embed-text"

    # Obsidian vault path
    obsidian_vault_path: str = str(Path.home() / "Documents" / "Journal")

    # GitHub
    github_repos: list[str] = []  # e.g. ["Kadajett/pke"]

    # Discord
    discord_bot_token: str = ""
    discord_channel_ids: list[str] = []

    # Sync state DB
    sync_db_path: str = "data/sync_state.db"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    model_config = {"env_prefix": "PKE_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
