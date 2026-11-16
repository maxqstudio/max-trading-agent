"""CLI for the M00 reference-only knowledge foundation."""
import argparse
import json
from pathlib import Path
from .core import build_index, load_catalog, plan, search

def main() -> None:
    parser = argparse.ArgumentParser(prog="max-trading-agent")
    parser.add_argument("command", choices=("validate", "index", "search", "plan"))
    parser.add_argument("value", nargs="?")
    parser.add_argument("--db", default=".local/mt5_knowledge.sqlite3")
    args = parser.parse_args()
    if args.command == "validate":
        output = {"valid": True, "entries": len(load_catalog())}
    elif args.command == "index":
        output = {"indexed": build_index(Path(args.db)), "database": args.db}
    elif args.command == "search":
        if not args.value:
            parser.error("search requires a query")
        output = search(Path(args.db), args.value)
    else:
        if not args.value:
            parser.error("plan requires a task name")
        output = plan(args.value)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
