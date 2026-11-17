"""CLI for offline MT5 knowledge and read-only reference agent missions."""
import argparse
import json
import sys
from pathlib import Path

from .core import build_index, load_catalog, plan, search
from .runtime import DEFAULT_JOURNAL, DEFAULT_KNOWLEDGE, EventJournal, ReferenceMission


def main() -> None:
    parser = argparse.ArgumentParser(prog="max-trading-agent")
    parser.add_argument("command", choices=("validate", "index", "search", "plan", "run", "serve", "mt5-probe"))
    parser.add_argument("value", nargs="?")
    parser.add_argument("--db", default=str(DEFAULT_KNOWLEDGE))
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--symbol", help="Exact broker symbol (read-only probe)")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--bars", type=int, default=100)
    args = parser.parse_args()
    if args.command == "validate":
        output = {"valid": True, "entries": len(load_catalog())}
    elif args.command == "index":
        output = {"indexed": build_index(Path(args.db)), "database": args.db}
    elif args.command == "search":
        if not args.value:
            parser.error("search requires a query")
        output = search(Path(args.db), args.value)
    elif args.command == "plan":
        if not args.value:
            parser.error("plan requires a task name")
        output = plan(args.value)
    elif args.command == "run":
        if not args.value:
            parser.error("run requires a reference task name")
        journal = EventJournal(Path(args.journal))
        run_id = journal.start(args.value)
        ReferenceMission(journal, Path(args.db)).execute(run_id, args.value)
        output = {"run_id": run_id, "mode": "REFERENCE_ONLY",
                  "runs": journal.runs(), "events": journal.events(run_id)}
    elif args.command == "mt5-probe":
        # Import occurs only after this command; GitHub Actions needs no MT5 package.
        from .mt5_readonly import MT5ReadError, ReadOnlyMT5
        from .probe import ProbeMission
        if not args.symbol:
            parser.error("mt5-probe requires --symbol (e.g., EURUSD)")
        try:
            run_id, output = ProbeMission(
                EventJournal(Path(args.journal)), ReadOnlyMT5.from_local_terminal()
            ).execute(args.symbol, timeframe=args.timeframe, bars=args.bars)
            output["journal_run_id"] = run_id
        except MT5ReadError as exc:
            print(json.dumps({"ok": False, "read_only": True, "error": exc.as_dict()}),
                  file=sys.stderr)
            raise SystemExit(2) from None
    else:
        from .web import create_server
        with create_server(port=args.port, journal_path=Path(args.journal),
                           knowledge_path=Path(args.db)) as server:
            print(f"Agent Trading Room (REFERENCE ONLY) at http://127.0.0.1:{server.server_port}")
            try:
                server.serve_forever(poll_interval=0.25)
            except KeyboardInterrupt:
                pass
        return
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
