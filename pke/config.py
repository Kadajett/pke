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
    github_repos: str = ""  # comma-separated: "Kadajett/pke,Kadajett/other"
    github_token: str = ""

    # DevDocs
    devdocs_path: str = "/bulk-storage/localDocs/devDocs/public/docs/"
    devdocs_collections: str = ""  # JSON map, empty = use defaults

    # Discord
    discord_bot_token: str = ""
    discord_channel_ids: str = ""  # comma-separated

    @property
    def github_repos_list(self) -> list[str]:
        return [r.strip() for r in self.github_repos.split(",") if r.strip()]

    @property
    def discord_channel_ids_list(self) -> list[str]:
        return [c.strip() for c in self.discord_channel_ids.split(",") if c.strip()]

    # Sync state DB
    sync_db_path: str = "data/sync_state.db"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    model_config = {"env_prefix": "PKE_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
