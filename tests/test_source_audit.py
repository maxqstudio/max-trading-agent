"""Strict official-host network audit contracts; tests never reach the internet."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from max_trading_agent.core import CATALOG
from max_trading_agent.source_audit import (
    SourceAuditError, _bounded_fetch, _outcome, _strict_path,
    audit_source_links,
)

WHEN = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)


class SourceAuditTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.records = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def fake(self, path):
        self.calls.append(path)
        return (200, "text/html; charset=utf-8", b"<!doctype html> official docs")

    def catalog(self, records):
        path = self.root / "catalog.json"
        path.write_text(json.dumps(records), encoding="utf-8")
        return path

    def test_reachable_metadata_never_claims_verified_content(self):
        report = audit_source_links(fetcher=self.fake, observed_at=WHEN)
        self.assertEqual(report["total_urls"], len(self.records))
        self.assertEqual(report["status"], "ALL_HTTP_HTML_REACHABLE")
        self.assertEqual(report["observed_at_utc"], "2026-11-17T08:00:00+00:00")
        self.assertEqual(len(report["catalog_sha256"]), 64)
        self.assertEqual(report["reachability_counts"],
                         {"HTTP_HTML_REACHABLE": len(self.records)})
        self.assertEqual(len(self.calls), len(self.records))
        self.assertTrue(all(x.startswith("/en/docs/python_metatrader5/")
                            for x in self.calls))
        for key in ("source_content_verified", "source_license_verified",
                    "source_freshness_verified", "broker_execution"):
            self.assertIs(report[key], False)
        self.assertEqual(report["decision"], "NO_TRADE")
        self.assertEqual(report["execution"], "DISABLED")
        self.assertEqual(report["owner_acceptance"], "PENDING")
        for row in report["links"]:
            self.assertFalse(row["source_truth_verified"])
        self.assertNotIn("<!doctype", json.dumps(report))

    def test_identical_catalog_and_clock_give_identical_report(self):
        first = audit_source_links(fetcher=self.fake, observed_at=WHEN)
        second = audit_source_links(fetcher=self.fake, observed_at=WHEN)
        self.assertEqual(first, second)

    def test_rejects_userinfo_ports_other_hosts_and_encoded_paths(self):
        urls = [
            "http://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py",
            "https://www.mql5.com.evil.com/en/docs/python_metatrader5/mt5initialize_py",
            "https://evil.com/en/docs/python_metatrader5/mt5initialize_py",
            "https://www.mql5.com:444/en/docs/python_metatrader5/mt5initialize_py",
            "https://user:pass@www.mql5.com/en/docs/python_metatrader5/mt5initialize_py",
            "https://www.mql5.com/en/docs/python_metatrader5/../mt5initialize_py",
            "https://www.mql5.com/en/docs/python_metatrader5/%2e%2e/mt5initialize_py",
            "https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py?next=evil",
            "https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py#section",
            "https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py%0d%0aX",
            "https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py/",
            "https://127.0.0.1/en/docs/python_metatrader5/mt5initialize_py",
        ]
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesRegex(SourceAuditError, "SOURCE_URL_NOT_ALLOWED"):
                    _strict_path(url)

    def test_poisonous_last_catalog_entry_blocks_all_network_first(self):
        rows = json.loads(CATALOG.read_text())
        rows[-1]["source_url"] = "https://127.0.0.1/private/secret"
        with self.assertRaisesRegex(SourceAuditError, "SOURCE_CATALOG_INVALID"):
            audit_source_links(self.catalog(rows), fetcher=self.fake)
        self.assertEqual(self.calls, [])

    def test_duplicate_documentation_url_fails_before_any_request(self):
        rows = json.loads(CATALOG.read_text())
        rows[-1]["source_url"] = rows[0]["source_url"]
        with self.assertRaisesRegex(SourceAuditError, "SOURCE_CATALOG_INVALID"):
            audit_source_links(self.catalog(rows), fetcher=self.fake)
        self.assertEqual(self.calls, [])

    def test_fixed_host_https_request_budget_and_no_redirect_following(self):
        class Response:
            status = 302
            def getheader(self, field, default=""):
                return "text/html"
            def read(self, amount):
                self_read_sizes.append(amount)
                return b"302 move"
        class Connection:
            def __init__(self):
                self.closed = False
                self.requests = []
            def request(self, *args, **kwargs):
                self.requests.append((args, kwargs))
            def getresponse(self):
                return Response()
            def close(self):
                self.closed = True
        self_read_sizes = []
        conn = Connection()
        with patch("max_trading_agent.source_audit.http.client.HTTPSConnection",
                   return_value=conn) as factory:
            result = _bounded_fetch(
                "/en/docs/python_metatrader5/mt5initialize_py"
            )
        self.assertEqual(result[0], 302)
        self.assertEqual(factory.call_args.args, ("www.mql5.com",))
        self.assertEqual(factory.call_args.kwargs["timeout"], 5)
        self.assertTrue(conn.closed)
        self.assertEqual(len(conn.requests), 1)
        args, kw = conn.requests[0]
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1],
                         "/en/docs/python_metatrader5/mt5initialize_py")
        self.assertEqual(kw["headers"]["Range"], "bytes=0-2047")
        self.assertEqual(kw["headers"]["Accept-Encoding"], "identity")
        self.assertEqual(self_read_sizes, [2048])
        self.assertEqual(_outcome(*result), "REDIRECT_NOT_FOLLOWED")

    def test_response_classification_prevents_false_success(self):
        cases = [
            ((404, "text/html", b"missing"), "HTTP_NOT_FOUND"),
            ((403, "text/html", b"denied"), "ACCESS_RESTRICTED"),
            ((429, "text/html", b"rate"), "RATE_LIMITED"),
            ((301, "text/html", b"redirect"), "REDIRECT_NOT_FOLLOWED"),
            ((200, "application/json", b"{}"), "CONTENT_TYPE_UNVERIFIED"),
            ((200, "text/html", b""), "CONTENT_TYPE_UNVERIFIED"),
            ((200, "text/html", b"x"), "HTTP_HTML_REACHABLE"),
            ((206, "text/html", b"x"), "HTTP_HTML_REACHABLE"),
            ((None, "text/html", b"x"), "HTTP_UNVERIFIED"),
        ]
        for arg, want in cases:
            with self.subTest(arg=arg):
                self.assertEqual(_outcome(*arg), want)

    def test_network_errors_are_inconclusive_and_cannot_echo_secrets(self):
        def broken(path):
            if path.endswith("mt5initialize_py"):
                raise OSError("SECRET_PRIVATE_API_KEY_ON_DISK")
            return (200, "text/html", b"ok")
        report = audit_source_links(fetcher=broken, observed_at=WHEN)
        self.assertEqual(report["status"], "INCONCLUSIVE")
        self.assertEqual(report["reachability_counts"]["NETWORK_UNVERIFIED"], 1)
        self.assertNotIn("SECRET_PRIVATE_API_KEY_ON_DISK", json.dumps(report))
        self.assertFalse(report["source_content_verified"])

    def test_non_utc_naive_clock_and_oversized_catalog_fail_closed(self):
        with self.assertRaisesRegex(SourceAuditError, "SOURCE_OBSERVATION_TIME_INVALID"):
            audit_source_links(fetcher=self.fake,
                               observed_at=datetime(2026, 11, 17))
        self.assertEqual(self.calls, [])
        rows = json.loads(CATALOG.read_text())
        for i in range(70):
            added = dict(rows[0])
            added["id"] = "synthetic" + str(i)
            added["source_url"] = (
                "https://www.mql5.com/en/docs/python_metatrader5/mt5synthetic" +
                str(i) + "_py"
            )
            rows.append(added)
        with self.assertRaisesRegex(SourceAuditError, "SOURCE_CATALOG_INVALID"):
            audit_source_links(self.catalog(rows), fetcher=self.fake)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
