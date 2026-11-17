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

## G17 — official documentation link reachability audit

```sh
python -m max_trading_agent rag-source-audit
```

This is a **separate opt-in network action** from the offline RAG index/search. It connects via TLS only to the exact fixed host `www.mql5.com` and canonical Python MT5 API paths; no arbitrary host or user-provided URL may be fetched. All paths are validated before network activity. GET uses a 2 KiB range request, <=2 KiB response read, a five-second timeout per source, no redirect following and a maximum of 64 catalog entries. It neither ingests nor stores website HTML, and no LLM/tool interpreter consumes the response.

Reports include official catalog URL, safe HTTP status/reachability code, full curated metadata SHA-256, timestamp, and counts. `ALL_HTTP_HTML_REACHABLE` **means only those URLs responded with HTTP 200/206 and an HTML Content-Type during that attempt**. It is not a full-page digest, authenticity of technical advice, publication-date check, license clearance, semantic freshness, or a safe-to-execute claim. TLS/network errors, redirects, rate limits and inaccessible pages remain **INCONCLUSIVE** rather than silently passing. `source_content_verified=false`, `source_license_verified=false`, `source_freshness_verified=false` on **all** reports.

A GitHub-hosted optional/scheduled source-audit workflow stores a short-lived JSON report with no secrets; its job success means **report collection**, not link/source acceptance. CI fake-network unit tests verify SSRF/path rejection, no redirect, body budget and fail-closed error classification. Actual source-content review and correct licensing remain future work; **all execution permissions stay disabled**. See [Issue #45](https://github.com/maxqstudio/max-trading-agent/issues/45).

## G19 — strict canonical URLs and local index-integrity veto

G19 applies the **same G17 canonical MetaQuotes HTTPS URL validator** to curated RAG metadata before any index replacement; it also rejects duplicate source URLs and malformed tag metadata. Index creation remains an atomic local replacement. No network request, page ingestion, HTML parsing or LLM decision is involved.

Every read-only `rag-search` and `rag-diff` operation now cross-checks each stored document's digest, declared embedder version, bounded finite vector and **the actual FTS indexed ID/title/summary/tags rows**, not just table size. With the built-in `sha256-token-hash-v1-64d` embedder, the vector is recomputed from the stored document to detect plausible but substituted finite numeric vectors. A mismatch raises `INDEX_INTEGRITY_FAILED` before ranking results or declaring a legitimate source change; no silent repair or auto-promotion occurs.

**Threat-model limit:** checks detect inconsistent local storage and unapproved URL shapes; they are *not cryptographic publisher attestation*. An attacker who can replace the complete data, code and validator can forge it, and the schema does not prove that a permissible summary is factually true, licensed, current or free from prompt-injection language. For non-default pluggable embedders, G19 validates stored vector shape/finite values but does **not** recompute model embeddings (which may be nondeterministic or costly). `source_verification=URL_LISTED_NOT_LIVE_VERIFIED`, `execution=DISABLED`, and external owner acceptance remain unchanged. See [Issue #49](https://github.com/maxqstudio/max-trading-agent/issues/49).
