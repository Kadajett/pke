# PKE — Personal Knowledge Engine

Vector DB-backed RAG system for indexing your entire digital life. Powered by **Qdrant** (vector search), **Ollama** (local embeddings via `nomic-embed-text`), and **FastAPI**.

## Architecture

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Obsidian  │    │   GitHub    │    │   Discord   │
│   Vault     │    │   API       │    │   API       │
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │
       └──────────┬───────┴──────────────────┘
                  │
           ┌──────▼──────┐
           │  Chunking   │  (markdown-aware, code-aware, chat windowing)
           └──────┬──────┘
                  │
           ┌──────▼──────┐
           │   Ollama    │  (nomic-embed-text, 768-dim)
           └──────┬──────┘
                  │
           ┌──────▼──────┐
           │   Qdrant    │  (HNSW index, payload filtering)
           └──────┬──────┘
                  │
           ┌──────▼──────┐
           │  FastAPI    │  (/search, /ingest, /sources)
           └─────────────┘
```

## Quick Start

### Docker Compose (recommended)

```bash
# Copy env file and adjust paths
cp .env.example .env

# Start all services (Qdrant + Ollama + PKE API)
docker compose up -d

# Pull the embedding model (first time only)
docker compose exec ollama ollama pull nomic-embed-text

# Ingest your Obsidian vault
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"source": "obsidian"}'

# Search
curl "http://localhost:8000/search?q=meeting+notes&limit=5"
```

### Local Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Start Qdrant and Ollama separately, then:
python -m pke.cli.main setup    # Create Qdrant collection
python -m pke.cli.main serve    # Start API server

# Ingest
python -m pke.cli.main ingest obsidian
python -m pke.cli.main ingest github --target Kadajett/pke
python -m pke.cli.main ingest discord --target CHANNEL_ID
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/search?q=...` | Semantic search with optional filters |
| POST | `/ingest` | Trigger ingestion pipeline |
| GET | `/sources` | List indexed sources with counts |

### Search Parameters

- `q` (required): Search query text
- `source_type`: Filter by `obsidian`, `github`, or `discord`
- `date_from` / `date_to`: Date range filter (YYYY-MM-DD)
- `limit`: Max results (default 10, max 100)

## CLI Commands

```bash
pke ingest obsidian [--target /path/to/vault] [--full]
pke ingest github [--target owner/repo] [--full]
pke ingest discord [--target channel_id] [--full]
pke setup          # Initialize Qdrant collection
pke serve          # Start FastAPI server
```

## Configuration

All settings via environment variables (prefix `PKE_`). See `.env.example` for the full list.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

## Hardware Requirements

- **GPU**: NVIDIA GPU recommended for Ollama (RTX 3050 6GB works well)
- **RAM**: 4GB+ for Qdrant
- **Disk**: Depends on indexed content volume
