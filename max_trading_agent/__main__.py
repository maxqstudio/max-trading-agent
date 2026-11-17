"""CLI for offline MT5 knowledge and read-only reference agent missions."""
import argparse
import json
import sys
from pathlib import Path

from .core import build_index, load_catalog, plan, search
from .runtime import DEFAULT_JOURNAL, DEFAULT_KNOWLEDGE, EventJournal, ReferenceMission


def main() -> None:
    parser = argparse.ArgumentParser(prog="max-trading-agent")
    parser.add_argument("command", choices=("validate", "index", "search", "plan", "run", "serve", "mt5-probe",
                        "signal-research", "market-import", "market-manifest",
                        "market-backtest", "experiment-show",
                        "shadow-open", "shadow-settle", "shadow-show",
                        "watch", "mt5-acceptance", "demo-preflight",
                        "rag-index", "rag-search", "rag-diff", "rag-eval"))
    parser.add_argument("value", nargs="?")
    parser.add_argument("--db", default=str(DEFAULT_KNOWLEDGE))
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--symbol", help="Exact broker symbol (read-only probe)")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--proposal", help="Private local candidate JSON for offline DEMO preflight")
    parser.add_argument("--policy", help="Private local risk limits JSON for offline DEMO preflight")
    parser.add_argument("--snapshot", help="Private MT5 snapshot JSON for SIGNAL research")
    parser.add_argument("--market-db", default=".local/market_evidence.sqlite3")
    parser.add_argument("--file", help="Private historical OHLC CSV path")
    parser.add_argument("--source", help="Historical data provenance label")
    parser.add_argument("--rights", default="USER_PROVIDED", help="Usage/license description")
    parser.add_argument("--quote-basis", default="MID", choices=("MID", "BID", "ASK"))
    parser.add_argument("--dataset", help="SHA-256 dataset identifier")
    parser.add_argument("--experiment", help="SHA-256 experiment identifier")
    parser.add_argument("--spread", type=float, help="Modeled full spread in price units")
    parser.add_argument("--slippage", type=float, help="Modeled slippage per side in price units")
    parser.add_argument("--fee-per-side", type=float, help="Modeled fee in price units per asset unit")
    parser.add_argument("--candidate", help="Stable prospective hypothesis ID")
    parser.add_argument("--side", choices=("LONG", "SHORT"), help="Hypothetical direction")
    parser.add_argument("--origin", default="USER_RESEARCH",
                        choices=("USER_RESEARCH", "LLM_RESEARCH"))
    parser.add_argument("--interval-seconds", type=int, default=15,
                        help="Watch interval 5-60 seconds")
    parser.add_argument("--max-polls", type=int, default=0,
                        help="0: continuous until Ctrl+C; >=1: bounded")
    parser.add_argument("--samples", type=int, default=3,
                        help="MT5 acceptance: number of bounded samples (2-10)")
    parser.add_argument("--sample-interval", type=int, default=2,
                        help="MT5 acceptance: interval between samples (1-30 seconds)")
    parser.add_argument("--identity-key-file",
                        default=".local/mt5_identity.key",
                        help="Private HMAC key file for opt-in MT5 read-only observation")
    parser.add_argument("--enable-shadow-research", action="store_true",
                        help="Opt-in to one additional hypothesis model call and shadow tracking")
    parser.add_argument("--shadow-slippage", type=float,
                        help="Explicit adverse shadow slippage per side in price units")
    parser.add_argument("--shadow-fee-per-side", type=float,
                        help="Explicit shadow per-side fee per asset unit in price units")
    parser.add_argument("--shadow-max-cycles", type=int, default=5,
                        help="Persistent research cycle cap per symbol/timeframe (1-50)")
    parser.add_argument("--enable-llm-research", action="store_true",
                        help="EXPLICIT opt-in: up to 4 billable LLM calls per trigger")
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
        from .identity import IdentityError, load_or_create_key
        if not args.symbol:
            parser.error("mt5-probe requires --symbol (e.g., EURUSD)")
        try:
            run_id, output = ProbeMission(
                EventJournal(Path(args.journal)),
                ReadOnlyMT5.from_local_terminal(
                    identity_key=load_or_create_key(Path(args.identity_key_file))
                ),
            ).execute(args.symbol, timeframe=args.timeframe, bars=args.bars)
            output["journal_run_id"] = run_id
        except (MT5ReadError, IdentityError) as exc:
            details = exc.as_dict() if isinstance(exc, MT5ReadError) else {
                "code": exc.code
            }
            print(json.dumps({"ok": False, "read_only": True, "error": details}),
                  file=sys.stderr)
            raise SystemExit(2) from None
    elif args.command == "mt5-acceptance":
        from .identity import IdentityError, load_or_create_key
        from .mt5_readonly import MT5ReadError, ReadOnlyMT5
        from .terminal_acceptance import AcceptanceError, TerminalAcceptance
        if not args.symbol:
            parser.error("mt5-acceptance requires --symbol")
        if not 2 <= args.samples <= 10 or not 1 <= args.sample_interval <= 30:
            parser.error("mt5-acceptance requires --samples 2-10 "
                         "and --sample-interval 1-30")
        try:
            key = load_or_create_key(Path(args.identity_key_file))
            auditor = TerminalAcceptance(
                EventJournal(Path(args.journal)),
                ReadOnlyMT5.from_local_terminal(identity_key=key),
                key,
            )
            output = auditor.execute(
                args.symbol, timeframe=args.timeframe, bars=args.bars,
                samples=args.samples, sample_interval=args.sample_interval,
            )
        except (IdentityError, MT5ReadError, AcceptanceError) as exc:
            code = getattr(exc, "code", str(exc))
            print(json.dumps({"ok": False, "code": code,
                              "decision": "NO_TRADE", "execution": "DISABLED"}),
                  file=sys.stderr)
            raise SystemExit(2) from None
    elif args.command in ("rag-index", "rag-search", "rag-diff", "rag-eval"):
        from .hybrid_knowledge import HybridKnowledge, RagError
        rag = HybridKnowledge(Path(args.db))
        try:
            if args.command == "rag-index":
                output = rag.index()
            elif args.command == "rag-search":
                if not args.value:
                    parser.error("rag-search requires a query")
                output = rag.search(args.value)
            elif args.command == "rag-diff":
                output = rag.source_diff()
            else:
                cases = Path(__file__).resolve().parent.parent / "knowledge" / "rag_benchmark.json"
                output = rag.evaluate(json.loads(cases.read_text(encoding="utf-8")), k=5)
        except (RagError, OSError, UnicodeError, ValueError):
            print(json.dumps({"ok": False, "code": "RAG_VALIDATION_BLOCKED",
                              "execution": "DISABLED"}), file=sys.stderr)
            raise SystemExit(2) from None
    elif args.command == "demo-preflight":
        from .demo_preflight import PreflightError, check_demo_preflight
        from .identity import IdentityError, load_or_create_key
        if not args.snapshot or not args.proposal or not args.policy:
            parser.error("demo-preflight requires --snapshot --proposal --policy")
        try:
            def private_json(path):
                file = Path(path)
                if not file.is_file() or not 0 < file.stat().st_size <= 2_000_000:
                    raise PreflightError("INVALID_PRIVATE_FILE")
                return json.loads(file.read_text(encoding="utf-8"))
            snapshot = private_json(args.snapshot)
            proposal = private_json(args.proposal)
            policy = private_json(args.policy)
            key = load_or_create_key(Path(args.identity_key_file))
            output = check_demo_preflight(snapshot, proposal, policy, key)
        except (PreflightError, IdentityError, OSError, ValueError, UnicodeError):
            print(json.dumps({"ok": False, "code": "PREFLIGHT_BLOCKED",
                              "decision": "NO_TRADE"}), file=sys.stderr)
            raise SystemExit(2) from None
    elif args.command == "signal-research":
        from .llm_client import LLMError, OpenAICompatibleClient
        from .signal_research import SignalResearch
        if not args.snapshot:
            parser.error("signal-research requires --snapshot PATH")
        try:
            path = Path(args.snapshot)
            if not 0 < path.stat().st_size <= 2_000_000:
                raise LLMError("SNAPSHOT_FILE_TOO_LARGE")
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            client = OpenAICompatibleClient.from_env()
            run_id = SignalResearch(EventJournal(Path(args.journal)), client).execute(snapshot)
            output = {"run_id": run_id, "mode": "SIGNAL", "decision": "NO_TRADE",
                      "execution": "DISABLED"}
        except (OSError, UnicodeError, ValueError) as exc:
            print(json.dumps({"ok": False, "code": "INVALID_SNAPSHOT_FILE"}), file=sys.stderr)
            raise SystemExit(2) from None
        except LLMError as exc:
            print(json.dumps({"ok": False, "code": exc.code}), file=sys.stderr)
            raise SystemExit(2) from None
    elif args.command in ("market-import", "market-manifest",
                          "market-backtest", "experiment-show"):
        from .market_store import MarketDataError, MarketStore
        from .backtest_mission import BacktestMission
        store = MarketStore(Path(args.market_db))
        try:
            if args.command == "market-import":
                if not args.file or not args.symbol or not args.source:
                    parser.error("market-import requires --file --symbol --source --timeframe")
                output = store.import_csv(
                    Path(args.file), symbol=args.symbol, timeframe=args.timeframe,
                    source=args.source, rights=args.rights,
                    quote_basis=args.quote_basis)
            elif args.command == "market-manifest":
                if not args.dataset:
                    parser.error("market-manifest requires --dataset")
                output = store.manifest(args.dataset)
            elif args.command == "experiment-show":
                if not args.experiment:
                    parser.error("experiment-show requires --experiment")
                output = store.experiment(args.experiment)
            else:
                if not args.dataset or any(x is None for x in (
                        args.spread, args.slippage, args.fee_per_side)):
                    parser.error("market-backtest requires --dataset --spread "
                                 "--slippage --fee-per-side")
                output = BacktestMission(
                    EventJournal(Path(args.journal)), store
                ).execute(args.dataset, spread=args.spread, slippage=args.slippage,
                          fee_per_side=args.fee_per_side)
        except MarketDataError as exc:
            print(json.dumps({"ok": False, "error": str(exc),
                              "execution": "DISABLED"}), file=sys.stderr)
            raise SystemExit(2) from None
    elif args.command in ("shadow-open", "shadow-settle", "shadow-show"):
        from .shadow import ShadowError, ShadowLedger
        from .shadow_mission import ShadowMission
        if not args.candidate:
            parser.error("shadow command requires --candidate")
        try:
            ledger = ShadowLedger(Path(args.market_db))
            if args.command == "shadow-show":
                output = ledger.get(args.candidate)
            else:
                if not args.snapshot:
                    parser.error("shadow-open/shadow-settle require --snapshot")
                private_path = Path(args.snapshot)
                if not 0 < private_path.stat().st_size <= 2_000_000:
                    raise ShadowError("SNAPSHOT_FILE_INVALID_SIZE")
                snapshot = json.loads(private_path.read_text(encoding="utf-8"))
                mission = ShadowMission(EventJournal(Path(args.journal)), ledger)
                if args.command == "shadow-open":
                    if args.side is None or args.slippage is None or args.fee_per_side is None:
                        parser.error("shadow-open requires --side --slippage --fee-per-side")
                    output = mission.open(
                        args.candidate, snapshot, side=args.side,
                        slippage=args.slippage, fee_per_side=args.fee_per_side,
                        origin=args.origin,
                    )
                else:
                    output = mission.settle(args.candidate, snapshot)
        except (OSError, UnicodeError, ValueError) as exc:
            # Do not print raw input paths, exception tracebacks or secrets.
            code = str(exc) if isinstance(exc, ShadowError) else "INVALID_SHADOW_INPUT"
            print(json.dumps({"ok": False, "error": code,
                              "execution": "DISABLED"}), file=sys.stderr)
            raise SystemExit(2) from None
    elif args.command == "watch":
        from .llm_client import LLMError, OpenAICompatibleClient
        from .mt5_readonly import MT5ReadError, ReadOnlyMT5
        from .watcher import MarketWatcher, WatchError
        from .shadow import ShadowError
        from .identity import IdentityError, load_or_create_key
        if not args.symbol:
            parser.error("watch requires --symbol")
        if not 5 <= args.interval_seconds <= 60 or not 0 <= args.max_polls <= 100000:
            parser.error("watch requires --interval-seconds 5-60 and --max-polls >= 0")
        if args.enable_shadow_research and (
            not args.enable_llm_research
            or args.shadow_slippage is None or args.shadow_fee_per_side is None
            or not 1 <= args.shadow_max_cycles <= 50
        ):
            parser.error("shadow research requires --enable-llm-research "
                         "--shadow-slippage --shadow-fee-per-side "
                         "and --shadow-max-cycles 1-50")
        try:
            client = (OpenAICompatibleClient.from_env()
                      if args.enable_llm_research else None)
            observer = MarketWatcher(
                EventJournal(Path(args.journal)),
                ReadOnlyMT5.from_local_terminal(
                    identity_key=load_or_create_key(Path(args.identity_key_file))
                ),
                args.symbol, args.timeframe, bars=args.bars,
                client=client, knowledge_db=Path(args.db),
                identity_key=load_or_create_key(Path(args.identity_key_file)),
                enable_shadow_research=args.enable_shadow_research,
                shadow_slippage=args.shadow_slippage,
                shadow_fee_per_side=args.shadow_fee_per_side,
                shadow_max_cycles=args.shadow_max_cycles,
            )
            output = observer.run(
                interval_seconds=args.interval_seconds, max_polls=args.max_polls
            )
        except KeyboardInterrupt:
            output = {"status": "STOPPED_BY_USER", "mode": "READ_ONLY",
                      "execution": "DISABLED"}
        except (LLMError, MT5ReadError, WatchError, IdentityError, ShadowError) as exc:
            code = getattr(exc, "code", str(exc))
            print(json.dumps({"ok": False, "code": code,
                              "execution": "DISABLED"}), file=sys.stderr)
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
    if args.command == "mt5-acceptance":
        if output["status"] == "BLOCKED":
            raise SystemExit(2)
        if output["status"] == "INCONCLUSIVE":
            raise SystemExit(3)


if __name__ == "__main__":
    main()
