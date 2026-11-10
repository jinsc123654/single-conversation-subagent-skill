"""python -m scs_mcp --project-root /absolute/project; stdio only."""

import argparse
import logging
import sys
from pathlib import Path

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Local single-conversation-subagent MCP (stdio)")
    parser.add_argument("--project-root", type=Path, required=True, help="Explicit allowed local project root")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    # SDK logs may contain client-supplied values when debugging. Disable its
    # diagnostics by default; our structured event table stores only enums/IDs.
    logging.basicConfig(stream=sys.stderr, level=logging.CRITICAL)
    try:
        from .server import create_server
        create_server(args.project_root).run(transport="stdio")
    except Exception:
        print("scs_mcp startup failed; check project path, dependencies and local state permissions.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
