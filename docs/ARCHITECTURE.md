# PKE Architecture — Personal Knowledge Engine

## Overview

PKE is a vector DB-backed RAG system that indexes Jeremy's digital life for semantic search. It runs on TheShire (single-node K8s cluster, RTX 3050 6GB VRAM) and exposes a query interface as an OpenClaw skill.

```
┌─────────────────────────────────────────────────────┐
│                   OpenClaw Skill                     │
│              (query interface + tool)                │
└──────────────────────┬──────────────────────────────┘
                       │ REST API
┌──────────────────────▼──────────────────────────────┐
│                  PKE Query Server                    │
│         (FastAPI — semantic search + rerank)         │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                    Qdrant                             │
│           (vector store — K8s pod)                   │
└──────────────────────▲──────────────────────────────┘
                       │ embeddings
┌──────────────────────┴──────────────────────────────┐
│               PKE Ingestion Pipeline                 │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│   │ Obsidian │ │  GitHub  │ │ Discord  │  ...       │
│   │ Connector│ │ Connector│ │ Connector│            │
│   └──────────┘ └──────────┘ └──────────┘           │
└─────────────────────────────────────────────────────┘
```

---

## Tech Choices

### Vector DB: **Qdrant** (self-hosted on K8s)

- **Why not pgvector:** No existing Postgres in the cluster. Adding Postgres just for vectors is heavier than a purpose-built vector DB.
- **Why not Chroma:** Chroma is great for prototyping but Qdrant has better filtering, snapshots, and production stability.
- **Why not SQLite-vec:** Too limited for multi-collection, metadata filtering, and incremental updates at scale.
- **Qdrant advantages:** Rust-based (low memory), excellent filtering on payload metadata, built-in snapshots for backup, gRPC + REST, runs well in K8s with ~512MB RAM. Free and open-source.

**Deployment:** Qdrant Helm chart on K8s, persistent volume for storage. Single replica is fine for personal use.

### Embedding Model: **`nomic-embed-text` via Ollama** (local, GPU-accelerated)

- **Model:** `nomic-embed-text` — 137M params, 768-dim embeddings, 8192 token context window
- **Why this model:** Fits easily in 6GB VRAM (uses ~500MB), strong performance on MTEB benchmarks, long context window handles full documents
- **Why local:** Free, private (no data leaves the machine), fast enough for personal scale
- **Runtime:** Ollama is already the simplest way to serve embedding models. If Ollama is already installed, it's one command. If not, it's a single binary.
- **Fallback:** If GPU is busy (media encoding etc.), can fall back to CPU or queue

### Language: **TypeScript (Node.js)**

- Matches the OpenClaw ecosystem
- `@qdrant/js-client-rest` is well-maintained
- Easy to build the OpenClaw skill integration
- FastAPI alternative considered but TS keeps it one ecosystem

### Query Interface: **OpenClaw Skill + REST API**

- **Primary:** OpenClaw skill that calls PKE's REST API — this is how the assistant uses it
- **Secondary:** REST API (Express/Fastify) for direct queries, debugging, and future UI
- **Endpoints:**
  - `POST /query` — semantic search with optional filters (source, date range, tags)
  - `POST /ingest` — trigger ingestion for a specific source
  - `GET /status` — ingestion status, collection stats
  - `GET /sources` — list configured sources and sync state

---

## Data Sources & Connectors

Each connector implements a common interface:

```typescript
interface Connector {
  name: string;
  /** Fetch new/updated documents since last sync */
  sync(since?: Date): AsyncIterable<Document>;
  /** Full re-index */
  reindex(): AsyncIterable<Document>;
}

interface Document {
  id: string;           // stable unique ID (e.g., "obsidian:path/to/note.md")
  source: string;       // "obsidian" | "github" | "discord"
  content: string;      // raw text content
  metadata: Record<string, unknown>; // source-specific metadata
  timestamp?: Date;     // when the content was created/modified
  excludeFromIndex?: boolean; // privacy flag
}
```

### 1. Obsidian Connector (Priority 1)

- **Source:** `~/Documents/Journal/` (~5MB currently)
- **Strategy:** Watch filesystem with `chokidar` for real-time sync, plus periodic full scan
- **Chunking:** Split by markdown headings (H1/H2 sections). For long sections, split at paragraph boundaries (~500 tokens per chunk). Preserve frontmatter as metadata.
- **Privacy:** Respect a `pke-exclude: true` frontmatter field. Exclude any file in a `.pkeignore` list.
- **ID scheme:** `obsidian:<relative-path>:<chunk-index>`
- **Incremental:** Track file mtime; only re-embed changed files

### 2. GitHub Connector (Priority 2)

- **Source:** All Kadajett repos via GitHub API (`gh` CLI or Octokit)
- **Content types:** README, issue bodies + comments, PR descriptions + comments, commit messages
- **Chunking:** Issues/PRs as single documents (they're usually short). READMEs chunked like markdown. Commit messages grouped by day per repo.
- **Incremental:** Track `updated_at` timestamps per repo. Use GitHub's `since` parameter.
- **ID scheme:** `github:<owner/repo>:<type>:<id>` (e.g., `github:Kadajett/pke:issue:1`)

### 3. Discord Connector (Priority 3)

- **Source:** OpenClaw Discord channels via Discord API or bot
- **Chunking:** Group messages into conversation windows (~20 messages or 10 minutes of silence as boundary). Each window = one document.
- **Incremental:** Track last synced message ID per channel
- **ID scheme:** `discord:<channel-id>:<window-start-message-id>`
- **Privacy:** Skip DMs unless explicitly opted in. Configurable channel allowlist.

---

## Chunking Strategy

Different content types need different chunking:

| Source | Strategy | Target Size | Overlap |
|--------|----------|-------------|---------|
| Obsidian markdown | Heading-based splits, then paragraph splits | ~500 tokens | 50 tokens |
| GitHub issues/PRs | Whole document (usually <500 tokens) | Full | None |
| GitHub READMEs | Heading-based, same as Obsidian | ~500 tokens | 50 tokens |
| Discord conversations | Time-windowed message groups | ~20 messages | 2 messages |
| Commit messages | Daily groups per repo | Variable | None |

All chunks store metadata: `{ source, path, timestamp, tags, url }` — enables filtered search (e.g., "search only my journal from last month").

---

## Project Structure

```
pke/
├── docs/
│   └── ARCHITECTURE.md          # this file
├── src/
│   ├── index.ts                 # entry point — starts API server + scheduler
│   ├── config.ts                # configuration (env vars, defaults)
│   ├── server/
│   │   ├── app.ts               # Express/Fastify app
│   │   └── routes/
│   │       ├── query.ts         # POST /query
│   │       ├── ingest.ts        # POST /ingest
│   │       └── status.ts        # GET /status, /sources
│   ├── embeddings/
│   │   ├── embedder.ts          # embedding interface + Ollama implementation
│   │   └── chunker.ts           # content-type-aware chunking
│   ├── vectordb/
│   │   └── qdrant.ts            # Qdrant client wrapper
│   ├── connectors/
│   │   ├── types.ts             # Connector interface, Document type
│   │   ├── obsidian.ts          # Obsidian vault connector
│   │   ├── github.ts            # GitHub connector
│   │   └── discord.ts           # Discord connector
│   ├── sync/
│   │   ├── scheduler.ts         # Cron-based sync scheduler
│   │   └── state.ts             # Sync state persistence (last sync timestamps)
│   └── skill/
│       └── pke-skill.ts         # OpenClaw skill definition
├── k8s/
│   ├── qdrant.yaml              # Qdrant deployment + PVC
│   └── pke.yaml                 # PKE server deployment
├── package.json
├── tsconfig.json
└── .pkeignore                   # files/patterns to exclude from indexing
```

---

## Deployment

### Phase 1: Local development
- Qdrant via Docker (`docker run qdrant/qdrant`)
- Ollama running locally (already likely installed)
- PKE server via `tsx` or `node`

### Phase 2: K8s deployment
- Qdrant: Helm chart or simple deployment YAML, 512MB–1GB RAM, persistent volume
- PKE server: Simple deployment, 256MB RAM, connects to Qdrant service
- Ollama: Runs on host (not in K8s) since it needs direct GPU access — PKE connects via Ollama's HTTP API on host network

### Storage estimates
- Obsidian vault (5MB text) → ~1,000 chunks → ~3MB vector storage
- GitHub (50 repos, issues, PRs) → ~5,000 chunks → ~15MB vector storage
- Discord (6 months history) → ~10,000 chunks → ~30MB vector storage
- **Total:** ~50MB initially. Qdrant can handle millions of vectors; storage is not a concern.

---

## Configuration

```yaml
# pke.config.yaml
embedding:
  provider: ollama
  model: nomic-embed-text
  endpoint: http://localhost:11434  # Ollama API
  dimensions: 768
  batchSize: 32

vectordb:
  provider: qdrant
  endpoint: http://qdrant.pke.svc.cluster.local:6333
  collection: pke-knowledge

sources:
  obsidian:
    enabled: true
    path: ~/Documents/Journal
    syncInterval: 5m        # watch + periodic
    excludePatterns:
      - "*.excalidraw"
      - ".obsidian/*"

  github:
    enabled: true
    orgs: [Kadajett]
    syncInterval: 1h
    includeTypes: [issues, prs, readmes, commits]

  discord:
    enabled: false          # enable after initial sources work
    channels: []            # allowlist
    syncInterval: 6h

privacy:
  excludeFiles: .pkeignore
  frontmatterExcludeKey: pke-exclude
  stripPatterns:            # regex patterns to redact before embedding
    - "\\b\\d{3}-\\d{2}-\\d{4}\\b"  # SSN-like
    - "\\b\\d{16}\\b"               # credit card-like

server:
  port: 3100
  host: 0.0.0.0
```

---

## Implementation Tasks (suggested order)

1. **Project scaffolding** — package.json, tsconfig, ESLint, basic Express server with health endpoint
2. **Qdrant integration** — client wrapper, collection creation, upsert/search operations
3. **Embedding service** — Ollama client, batched embedding, chunker with markdown support
4. **Obsidian connector** — file watcher, markdown parser, frontmatter extraction, sync state
5. **Query API** — `/query` endpoint with metadata filtering, result formatting
6. **OpenClaw skill** — PKE skill definition that calls the query API
7. **GitHub connector** — Octokit/gh-based fetcher for issues, PRs, READMEs
8. **Discord connector** — message history fetcher with conversation windowing
9. **K8s manifests** — Qdrant deployment, PKE deployment, persistent volumes
10. **Privacy & exclusions** — .pkeignore, frontmatter flags, content redaction

---

## Security & Privacy

- All data stays on TheShire — no external API calls for embeddings
- `.pkeignore` file for path-based exclusions
- Frontmatter `pke-exclude: true` for per-file exclusions
- Configurable regex redaction for sensitive patterns (SSNs, card numbers) before embedding
- Discord connector requires explicit channel allowlist (no DMs by default)
- API server binds to localhost in dev, cluster-internal in K8s (no public exposure)
