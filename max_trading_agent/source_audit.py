"""Bounded HTTPS *reachability* audit for curated MetaQuotes Python API URLs.

No arbitrary fetch, redirect following, HTML persistence, knowledge mutation,
LLM, broker traffic, source-content trust, or trading authorization.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .core import CATALOG, load_catalog

_HOST = "www.mql5.com"
_ALLOWED_PATH = re.compile(r"/en/docs/python_metatrader5/[a-z0-9_]+_py\Z")
_MAX_URLS = 64
_PREFIX_BYTES = 2048
_TIMEOUT_SECONDS = 5


class SourceAuditError(ValueError):
    """Safe failure code, never echo URL/token/HTML/connection exception."""


def _strict_path(url: str) -> str:
    """Validate the *entire URL* before sending a request to the fixed host."""
    if not isinstance(url, str) or not 30 <= len(url) <= 180:
        raise SourceAuditError("SOURCE_URL_NOT_ALLOWED")
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc != _HOST
                or parts.hostname != _HOST or parts.port is not None
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or not _ALLOWED_PATH.fullmatch(parts.path)):
            raise ValueError("source url not allowed")
        return parts.path
    except ValueError:
        raise SourceAuditError("SOURCE_URL_NOT_ALLOWED") from None


def _bounded_fetch(path: str) -> tuple[int, str, bytes]:
    """One fixed-host HTTPS GET. No redirect following and <=2 KiB body read."""
    if not _ALLOWED_PATH.fullmatch(path):
        raise SourceAuditError("SOURCE_PATH_NOT_ALLOWED")
    connection = http.client.HTTPSConnection(
        _HOST, timeout=_TIMEOUT_SECONDS, context=ssl.create_default_context()
    )
    try:
        connection.request("GET", path, headers={
            "User-Agent": "MaxTradingAgent-Docs-LinkAudit/1.0",
            "Accept": "text/html",
            "Accept-Encoding": "identity",
            "Range": f"bytes=0-{_PREFIX_BYTES-1}",
        })
        response = connection.getresponse()
        return (response.status, response.getheader("Content-Type", ""),
                response.read(_PREFIX_BYTES))
    finally:
        connection.close()


def _outcome(status: int, mime: str, prefix: bytes) -> str:
    if type(status) is not int or not 100 <= status <= 599:
        return "HTTP_UNVERIFIED"
    if status in (200, 206):
        if (isinstance(mime, str)
                and mime.lower().split(";", 1)[0].strip() in (
                    "text/html", "application/xhtml+xml"
                )
                and isinstance(prefix, bytes) and len(prefix) > 0):
            return "HTTP_HTML_REACHABLE"
        return "CONTENT_TYPE_UNVERIFIED"
    if status in (404, 410):
        return "HTTP_NOT_FOUND"
    if status in (301, 302, 303, 307, 308):
        return "REDIRECT_NOT_FOLLOWED"
    if status in (401, 403):
        return "ACCESS_RESTRICTED"
    if status == 429:
        return "RATE_LIMITED"
    return "HTTP_UNVERIFIED"


def audit_source_links(
    catalog: Path = CATALOG,
    *, fetcher: Callable[[str], tuple[int, str, bytes]] | None = None,
    observed_at: datetime | None = None,
) -> dict:
    """Audit curated URL reachability, never the truth/freshness of page contents.

    The default makes one fixed-host network call per URL. Tests inject a
    deterministic fetcher, never touching network.
    """
    try:
        records = load_catalog(Path(catalog))
        if not 1 <= len(records) <= _MAX_URLS:
            raise ValueError("source count outside audit budget")
        paths = [_strict_path(item["source_url"]) for item in records]
        # Validate ALL paths before any request: a poisoned tail causes zero
        # requests, rather than partial network activity.
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate documentation URL")
    except (ValueError, TypeError, KeyError, OSError, UnicodeError):
        raise SourceAuditError("SOURCE_CATALOG_INVALID") from None
    now = observed_at or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise SourceAuditError("SOURCE_OBSERVATION_TIME_INVALID")
    timestamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    metadata_hash = hashlib.sha256(json.dumps(
        records, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")).hexdigest()
    getter = _bounded_fetch if fetcher is None else fetcher
    reports = []
    for item, path in zip(records, paths):
        try:
            result = getter(path)
            if not isinstance(result, tuple) or len(result) != 3:
                raise ValueError("unexpected response")
            status, mime, prefix = result
            outcome = _outcome(status, mime, prefix)
            safe_status = status if type(status) is int and 100 <= status <= 599 else None
        except Exception:
            # Never expose a server exception, raw response, key, path,
            # proxy configuration or private filesystem detail.
            outcome, safe_status = "NETWORK_UNVERIFIED", None
        reports.append({
            "tool_id": item["id"], "source_url": item["source_url"],
            "http_status": safe_status, "reachability": outcome,
            "source_truth_verified": False,
        })
    counts = {reason: sum(row["reachability"] == reason for row in reports)
              for reason in sorted({row["reachability"] for row in reports})}
    return {
        "mode": "CURATED_DOC_LINK_AUDIT_ONLY",
        "status": ("ALL_HTTP_HTML_REACHABLE" if counts.get(
            "HTTP_HTML_REACHABLE", 0
        ) == len(reports) else "INCONCLUSIVE"),
        "observed_at_utc": timestamp,
        "catalog_sha256": metadata_hash,
        "total_urls": len(reports),
        "reachability_counts": counts, "links": reports,
        "source_content_verified": False,
        "source_license_verified": False,
        "source_freshness_verified": False,
        "decision": "NO_TRADE", "execution": "DISABLED",
        "broker_execution": False, "owner_acceptance": "PENDING",
        "limitations": (
            "A partial HTTPS HTML response proves only transient URL "
            "reachability, not document completeness, correctness, "
            "license status, prompt safety or broker execution authority."
        ),
    }
