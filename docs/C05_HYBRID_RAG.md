# Canonical M05 — Offline hybrid knowledge retrieval (candidate)

This local-only implementation uses **SQLite FTS5 plus a 64-dimensional signed-token hashing vector**. The built-in vector encoder is a deterministic *lexical baseline*, **not** a pretrained neural semantic embedding model and not a reason to claim search intelligence. An `Embedder` protocol supports replacing it later with an optional explicitly installed local model; no network/model download or paid API is used by default.

```sh
python -m max_trading_agent rag-index --db .local/mt5_hybrid.sqlite3
python -m max_trading_agent rag-search "copy rates OHLCV" --db .local/mt5_hybrid.sqlite3
python -m max_trading_agent rag-eval --db .local/mt5_hybrid.sqlite3
python -m max_trading_agent rag-diff --db .local/mt5_hybrid.sqlite3
```

Each result provides source URL, SHA-256 content digest, tool effect class and `execution=DISABLED`. **URL_LISTED_NOT_LIVE_VERIFIED** means the official documentation URL is in the curated catalog but was **not fetched or freshly checked**. Tool references, including `order_send`, are explanatory data only, not executor permissions.

`rag-diff` reports changed, added or removed curated *local metadata*; it does not fetch external pages. Indexes are replaced within one SQLite transaction after embedding validation; malformed replacements cannot silently erase last-good content. Retrieval rejects unexpected model versions, corrupt vectors or source hashes. A small independent fixed query set `knowledge/rag_benchmark.json` estimates recall@k and MRR for an instrument-independent MT5 API retrieval baseline. A good score does not validate broker market analysis, strategy alpha, third-party source freshness, or resistance against every RAG injection.

**Remaining:** licensed source refresh pipeline with actual verification schedule, neural local embedder quality acceptance, retrieval CI reference coverage and owner acceptance. See [Issue #33](https://github.com/maxqstudio/max-trading-agent/issues/33). AUTO_DEMO/LIVE still disabled.
