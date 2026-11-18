"""Optional local hybrid MT5 API knowledge retrieval: FTS5 + validated vectors.

Built-in hashing vectors are a *deterministic lexical feature baseline*, not
a neural semantic model. Sources are URL inventory, NOT live-verified pages.
Untrusted source text is never executed as instructions.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol, Sequence

from .core import CATALOG, load_catalog
from .source_audit import SourceAuditError, _strict_path

_DIM = 64
_TERMS = re.compile(r"[A-Za-z0-9_]+")
_SOURCE = "https://www.mql5.com/en/docs/python_metatrader5/"


class RagError(ValueError):
    """Safe error code; never echoes source contents or user secrets."""


class Embedder(Protocol):
    version: str

    def encode(self, text: str) -> Sequence[float]:
        ...


class HashingEmbedder:
    """No model download, network access, API billing or semantic claim."""
    version = "sha256-token-hash-v1-64d"

    def encode(self, text: str) -> list[float]:
        terms = _TERMS.findall(text.lower())[:512]
        vector = [0.0] * _DIM
        for word in terms:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % _DIM
            vector[index] += -1.0 if digest[2] & 1 else 1.0
        return vector


def _vector(encoder: Embedder, text: str) -> list[float]:
    try:
        raw = encoder.encode(text)
        if not isinstance(raw, (list, tuple)) or len(raw) != _DIM:
            raise ValueError()
        # Implementations of the local protocol must actually return
        # numeric components. Strings/booleans must not be coerced into
        # apparently valid embeddings by float(...).
        if any(type(x) not in (int, float) for x in raw):
            raise ValueError()
        values = [float(x) for x in raw]
        if any(not math.isfinite(x) for x in values) or not any(values):
            raise ValueError()
        return values
    except (ValueError, TypeError, OverflowError, AttributeError):
        raise RagError("INVALID_EMBEDDING") from None


def _checksum(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=True).encode("utf-8")).hexdigest()


def _cosine(a: list[float], b: list[float]) -> float:
    da = math.sqrt(sum(x*x for x in a))
    db = math.sqrt(sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / (da*db) if da and db else 0.0


class HybridKnowledge:
    """Atomic, local source-auditable index; never an executable tool registry."""

    def __init__(self, db: Path, embedder: Embedder | None = None):
        self.path = Path(db)
        self.encoder = embedder or HashingEmbedder()
        ver = getattr(self.encoder, "version", None)
        if not isinstance(ver, str) or not re.fullmatch(r"[A-Za-z0-9_-]{5,64}", ver):
            raise RagError("INVALID_EMBEDDER_VERSION")

    def _docs(self, catalog: Path) -> list[dict]:
        records = load_catalog(catalog)
        if not 1 <= len(records) <= 256:
            raise RagError("INVALID_CATALOG_SIZE")
        docs = []
        seen_urls = set()
        for item in records:
            url = item["source_url"]
            if (not isinstance(url, str) or not url.startswith(_SOURCE)
                    or not isinstance(item.get("summary"), str)
                    or not 1 <= len(item["summary"]) <= 2048
                    or not isinstance(item.get("title"), str)
                    or not 1 <= len(item["title"]) <= 160
                    or not isinstance(item["tags"], list)
                    or not 1 <= len(item["tags"]) <= 32
                    or any(not isinstance(tag, str)
                           or not re.fullmatch(r"[A-Za-z0-9_]{1,40}", tag)
                           for tag in item["tags"])):
                raise RagError("UNTRUSTED_SOURCE_METADATA")
            try:
                _strict_path(url)  # same canonical URL gate as G17 HTTPS audit
            except SourceAuditError:
                raise RagError("UNTRUSTED_SOURCE_METADATA") from None
            if url in seen_urls:
                raise RagError("DUPLICATE_SOURCE_URL")
            seen_urls.add(url)
            doc = {"id": item["id"], "title": item["title"],
                   "summary": item["summary"], "tags": item["tags"],
                   "source_url": url, "effect": item["effect"]}
            docs.append(doc)
        return docs

    def index(self, catalog: Path = CATALOG) -> dict:
        """Compute & validate whole candidate before transaction replaces index."""
        docs = self._docs(Path(catalog))
        candidate = []
        for doc in docs:
            content = " ".join([doc["title"], doc["summary"], " ".join(doc["tags"])])
            vector = _vector(self.encoder, content)
            candidate.append((doc, vector, _checksum(doc)))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("""CREATE TABLE IF NOT EXISTS hybrid_docs(
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    summary TEXT NOT NULL, tags TEXT NOT NULL,
                    source_url TEXT NOT NULL, effect TEXT NOT NULL,
                    digest TEXT NOT NULL, vector TEXT NOT NULL,
                    embedder_version TEXT NOT NULL
                )""")
                db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS hybrid_fts
                    USING fts5(id UNINDEXED, title, summary, tags)""")
                db.execute("DELETE FROM hybrid_docs")
                db.execute("DELETE FROM hybrid_fts")
                for doc, vec, digest in candidate:
                    db.execute("INSERT INTO hybrid_docs VALUES(?,?,?,?,?,?,?,?,?)",
                               (doc["id"], doc["title"], doc["summary"],
                                " ".join(doc["tags"]), doc["source_url"],
                                doc["effect"], digest, json.dumps(vec),
                                self.encoder.version))
                    db.execute("INSERT INTO hybrid_fts VALUES(?,?,?,?)",
                               (doc["id"], doc["title"], doc["summary"],
                                " ".join(doc["tags"])))
        except sqlite3.Error:
            raise RagError("INDEX_WRITE_FAILED") from None
        return {"documents": len(candidate), "embedder": self.encoder.version,
                "source_verification": "URL_LISTED_NOT_LIVE_VERIFIED",
                "model_type": "LOCAL_LEXICAL_HASH_BASELINE",
                "execution": "DISABLED"}

    def _connection(self):
        if not self.path.is_file():
            raise RagError("INDEX_MISSING")
        return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)

    def _checked_rows(self, db: sqlite3.Connection) -> list[tuple]:
        """Verify the visible FTS projection and every document before rankings.

        Integrity is structural and locally reproducible; it is NOT proof of
        correct/licensed upstream documentation or protection from a malicious
        actor who can replace the complete database and code.
        """
        rows = db.execute(
            "SELECT id,title,summary,tags,source_url,effect,digest,vector,"
            "embedder_version FROM hybrid_docs ORDER BY id"
        ).fetchall()
        if not 1 <= len(rows) <= 256:
            raise RagError("INVALID_INDEX")
        fts = db.execute(
            "SELECT id,title,summary,tags FROM hybrid_fts ORDER BY id"
        ).fetchall()
        if (len({row[0] for row in rows}) != len(rows)
                or fts != [row[:4] for row in rows]):
            raise RagError("INDEX_INTEGRITY_FAILED")
        checked = []
        for row in rows:
            key, title, summary, tags, url, effect, digest, embedded, version = row
            if not isinstance(tags, str):
                raise RagError("INDEX_INTEGRITY_FAILED")
            doc = {"id": key, "title": title, "summary": summary,
                   "tags": tags.split(), "source_url": url, "effect": effect}
            try:
                _strict_path(url)
            except SourceAuditError:
                raise RagError("INDEX_INTEGRITY_FAILED") from None
            if _checksum(doc) != digest or version != self.encoder.version:
                raise RagError("INDEX_INTEGRITY_FAILED")
            stored = json.loads(embedded)
            if (not isinstance(stored, list) or len(stored) != _DIM
                    or any(type(n) not in (int, float)
                           or not math.isfinite(float(n)) for n in stored)):
                raise RagError("INDEX_INTEGRITY_FAILED")
            # The persisted vector must reconstruct from the same
            # document for *every* local Embedder implementation, not only
            # the built-in hashing class. A nondeterministic adapter cannot
            # supply reproducible, auditable evidence and must fail closed.
            content = " ".join([title, summary, tags])
            try:
                expected = _vector(self.encoder, content)
            except RagError:
                raise RagError("INDEX_INTEGRITY_FAILED") from None
            if stored != expected:
                raise RagError("INDEX_INTEGRITY_FAILED")
            checked.append((row, stored))
        return checked

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        if not isinstance(query, str) or not 1 <= len(query) <= 200 or (
            type(limit) is not int or not 1 <= limit <= 30
        ):
            raise RagError("INVALID_QUERY")
        terms = _TERMS.findall(query)[:16]
        if not terms:
            raise RagError("INVALID_QUERY")
        vec = _vector(self.encoder, query)
        try:
            with closing(self._connection()) as db:
                checked = self._checked_rows(db)
                q = " OR ".join('"' + t + '"' for t in terms)
                lexical = dict(db.execute(
                    "SELECT id, bm25(hybrid_fts) FROM hybrid_fts "
                    "WHERE hybrid_fts MATCH ?", (q,)
                ))
            results = []
            for row, stored in checked:
                key, title, summary, tags, url, effect, digest, _, version = row
                score = 0.8 * (1 / (1 + abs(lexical[key])) if key in lexical else 0)
                score += 0.2 * max(0, _cosine(vec, stored))
                results.append({
                    "id": key, "title": title, "source_url": url,
                    "source_digest": digest, "score": round(score, 8),
                    "source_verification": "URL_LISTED_NOT_LIVE_VERIFIED",
                    "effect": effect, "execution": "DISABLED",
                })
            return sorted(results, key=lambda d: (-d["score"], d["id"]))[:limit]
        except (sqlite3.Error, OSError, ValueError, TypeError) as err:
            if isinstance(err, RagError):
                raise
            raise RagError("INDEX_INTEGRITY_FAILED") from None

    def source_diff(self, catalog: Path = CATALOG) -> dict:
        """Diff only local curated metadata; NEVER fetch external documents."""
        docs = self._docs(Path(catalog))
        try:
            with closing(self._connection()) as db:
                checked = self._checked_rows(db)
                existing = {row[0]: row[6] for row, _ in checked}
        except RagError:
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError):
            raise RagError("INDEX_INTEGRITY_FAILED") from None
        current = {d["id"]: _checksum(d) for d in docs}
        return {"added": sorted(set(current) - set(existing)),
                "removed": sorted(set(existing) - set(current)),
                "changed": sorted(k for k in set(current) & set(existing)
                                  if current[k] != existing[k]),
                "source_verification": "URL_LISTED_NOT_LIVE_VERIFIED"}

    def evaluate(self, cases: list[dict], *, k: int = 3) -> dict:
        if not isinstance(cases, list) or not 1 <= len(cases) <= 100 or (
            type(k) is not int or not 1 <= k <= 10
        ):
            raise RagError("INVALID_BENCHMARK")
        hits = rr = 0.0
        for case in cases:
            if not isinstance(case, dict) or not isinstance(case.get("query"), str) or (
                not isinstance(case.get("expected"), str)
            ):
                raise RagError("INVALID_BENCHMARK")
            items = self.search(case["query"], limit=k)
            ids = [v["id"] for v in items]
            if case["expected"] in ids:
                hits += 1
                rr += 1 / (ids.index(case["expected"]) + 1)
        return {"cases": len(cases), "recall_at_k": round(hits/len(cases), 6),
                "mrr_at_k": round(rr/len(cases), 6), "k": k,
                "semantic_model_claimed": False}
