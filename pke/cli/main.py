"""PKE CLI — command-line interface for ingestion and management."""

from __future__ import annotations

import argparse
import json
import sys


def cmd_ingest(args: argparse.Namespace) -> None:
    """Run an ingestion pipeline."""
    source = args.source

    if source == "obsidian":
        from pke.ingest.obsidian import ingest_obsidian

        stats = ingest_obsidian(vault_path=args.target, full=args.full)
    elif source == "github":
        from pke.ingest.github import ingest_github

        stats = ingest_github(repo=args.target, full=args.full)
    elif source == "discord":
        from pke.ingest.discord import ingest_discord

        stats = ingest_discord(channel_id=args.target, full=args.full)
    elif source == "localcode":
        from pke.ingest.localcode import ingest_localcode

        stats = ingest_localcode(target=args.target, full=args.full)
    else:
        print(f"Unknown source: {source}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(stats, indent=2))


def cmd_setup(args: argparse.Namespace) -> None:
    """Set up Qdrant collection."""
    from pke.db.setup import ensure_collection

    ensure_collection()
    print("Collection setup complete.")


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the API server."""
    import uvicorn

    from pke.config import settings

    uvicorn.run(
        "pke.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=args.reload,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="pke", description="Personal Knowledge Engine")
    sub = parser.add_subparsers(dest="command", required=True)

    # ingest
    p_ingest = sub.add_parser("ingest", help="Run ingestion pipeline")
    p_ingest.add_argument("source", choices=["obsidian", "github", "discord", "localcode"])
    p_ingest.add_argument("--target", help="Target path/repo/channel (source-specific)")
    p_ingest.add_argument("--full", action="store_true", help="Full re-ingestion")
    p_ingest.set_defaults(func=cmd_ingest)

    # setup
    p_setup = sub.add_parser("setup", help="Set up Qdrant collection")
    p_setup.set_defaults(func=cmd_setup)

    # serve
    p_serve = sub.add_parser("serve", help="Start API server")
    p_serve.add_argument("--reload", action="store_true", help="Enable auto-reload")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
