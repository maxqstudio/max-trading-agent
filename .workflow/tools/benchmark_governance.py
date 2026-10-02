#!/usr/bin/env python3
"""Measure the current Skill Workflow governance cost without optimizing it.

SW2-00 uses this script to establish a reproducible baseline before SW2-01
changes scanner/compiler/validator execution. The benchmark is observational:
it does not upgrade acceptance gates and it runs mutating sync work only inside
a temporary repository copy.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, TypeVar

from extract_project_facts import extract_project_facts
from sequence_contract import compute_source_digest, source_files

T = TypeVar("T")


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def timed(repeats: int, fn: Callable[[], T]) -> tuple[dict[str, float], T]:
    samples: list[float] = []
    value: T | None = None
    for _ in range(repeats):
        start = time.perf_counter()
        value = fn()
        samples.append(time.perf_counter() - start)
    assert value is not None
    return (
        {
            "repeats": repeats,
            "min_seconds": min(samples),
            "median_seconds": statistics.median(samples),
            "max_seconds": max(samples),
            "mean_seconds": statistics.fmean(samples),
        },
        value,
    )


def run_command(root: Path, command: list[str]) -> dict[str, object]:
    start = time.perf_counter()
    proc = subprocess.run(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    elapsed = time.perf_counter() - start
    return {
        "command": command,
        "seconds": elapsed,
        "returncode": proc.returncode,
        "output_tail": proc.stdout.splitlines()[-20:],
    }


def governance_tool(root: Path, name: str) -> Path | None:
    for candidate in (
        root / "scripts" / name,
        root / ".workflow" / "tools" / name,
    ):
        if candidate.is_file():
            return candidate
    return None


def copy_for_sync(root: Path, destination: Path) -> None:
    ignored = shutil.ignore_patterns(
        ".git",
        "docs",
        "artifacts",
        "__pycache__",
        "*.pyc",
        ".pytest_cache",
    )
    shutil.copytree(root, destination, ignore=ignored)
    generated = destination / ".workflow" / "generated"
    if generated.exists():
        shutil.rmtree(generated)


def fixture_tool(fixture: Path, source_root: Path, source_tool: Path) -> Path:
    rel = source_tool.relative_to(source_root)
    return fixture / rel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument(
        "--output",
        default="artifacts/performance/sw2-00-self-baseline.json",
    )
    ap.add_argument("--include-selftests", action="store_true")
    ap.add_argument("--expected-head", default="")
    ap.add_argument("--label", default="SW2-00 Skill Workflow baseline")
    args = ap.parse_args()

    if args.repeats < 1:
        print("FAIL repeats must be >= 1")
        return 2

    root = Path(args.root).resolve()
    sync_tool = governance_tool(root, "sync_project_truth.py")
    docs_validator = governance_tool(root, "validate_project_docs.py")
    if sync_tool is None or docs_validator is None:
        print("FAIL governance sync/docs tools missing")
        return 2

    observed_head = git(root, "rev-parse", "HEAD") or "NOT_AVAILABLE"
    expected_head = args.expected_head.strip()

    inventory_timing, inventory = timed(args.repeats, lambda: source_files(root))
    source_bytes = sum(path.stat().st_size for path in inventory)
    python_files = [path for path in inventory if path.suffix.lower() == ".py"]

    digest_timing, source_digest = timed(
        args.repeats, lambda: compute_source_digest(root)
    )
    facts_timing, facts = timed(args.repeats, lambda: extract_project_facts(root))

    commands: dict[str, dict[str, object]] = {}

    with tempfile.TemporaryDirectory(prefix="skill-workflow-sw2-baseline-") as td:
        fixture = Path(td) / "repo"
        copy_for_sync(root, fixture)
        fixture_sync = fixture_tool(fixture, root, sync_tool)
        fixture_validator = fixture_tool(fixture, root, docs_validator)
        commands["sync_project_truth"] = run_command(
            fixture,
            [
                sys.executable,
                str(fixture_sync),
                "--root",
                str(fixture),
            ],
        )
        commands["validate_project_docs_after_sync"] = run_command(
            fixture,
            [
                sys.executable,
                str(fixture_validator),
                "--root",
                str(fixture),
            ],
        )

    if args.include_selftests:
        compiler_selftest = governance_tool(root, "selftest_project_truth_compiler.py")
        strict_selftest = governance_tool(root, "selftest_strict_project_workflow.py")
        if compiler_selftest is None or strict_selftest is None:
            print("FAIL requested selftests are missing")
            return 2
        commands["project_truth_compiler_selftest"] = run_command(
            root,
            [sys.executable, str(compiler_selftest)],
        )
        commands["strict_workflow_selftest"] = run_command(
            root,
            [sys.executable, str(strict_selftest)],
        )

    failures = [
        name
        for name, result in commands.items()
        if int(result.get("returncode", 1)) != 0
    ]
    if expected_head and observed_head != expected_head:
        failures.append(
            "PROVENANCE_MISMATCH:observed=" + observed_head + ":expected=" + expected_head
        )

    report = {
        "schema_version": 1,
        "benchmark": args.label,
        "observed_head": observed_head,
        "expected_head": expected_head or "NOT_DECLARED",
        "exact_head_match": bool(expected_head) and observed_head == expected_head,
        "tool_layout": str(sync_tool.parent.relative_to(root).as_posix()),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "source": {
            "digest": source_digest,
            "files": len(inventory),
            "python_files": len(python_files),
            "bytes": source_bytes,
            "lines": facts.get("source_summary", {}).get("lines"),
        },
        "timings": {
            "source_files": inventory_timing,
            "compute_source_digest": digest_timing,
            "extract_project_facts": facts_timing,
        },
        "current_io_model": {
            "extract_project_facts_source_enumerations": 2,
            "per_python_file_reads_inside_extract_project_facts": 3,
            "explanation": (
                "Current extract_project_facts enumerates source_files once for module/fact extraction, "
                "reads Python text for line counting and AST parsing, then compute_source_digest "
                "enumerates source files again and reads bytes. This is an implementation-derived "
                "baseline, not an OS-level I/O trace."
            ),
        },
        "commands": commands,
        "failures": failures,
        "result": "FAIL" if failures else "PASS",
        "boundary": (
            "This report measures current execution cost only. It does not prove semantic correctness, "
            "runtime behavior, or final project acceptance."
        ),
    }

    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
