"""LLM-authored PURE research-tool proposals; no runtime import or trade authority.

Candidate Python runs ONLY inside opt-in Docker containment. Generated examples
are never independent correctness evidence. T0 result is a reviewable proposal.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

_ID = re.compile(r"[a-z][a-z0-9_]{2,47}\Z")
_VERSION = re.compile(r"0\.[0-9]{1,3}\.[0-9]{1,3}\Z")
_ALLOWED_CALLS = frozenset({
    "abs", "min", "max", "sum", "len", "range", "round", "float",
    "int", "sorted", "enumerate", "zip", "all", "any", "list", "tuple"
})
_BLOCK_NAMES = frozenset({
    "eval", "exec", "compile", "open", "__import__", "input", "globals",
    "locals", "vars", "getattr", "setattr", "delattr", "type", "object",
    "breakpoint", "help", "exit", "quit", "os", "sys", "socket", "subprocess",
    "importlib", "pathlib", "requests", "threading", "multiprocessing",
})
_ALLOWED_NODES = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg,
    ast.Return, ast.Assign, ast.AugAssign, ast.Expr, ast.If, ast.For,
    ast.While, ast.Break, ast.Continue, ast.Compare, ast.BoolOp,
    ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Store,
    ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Subscript,
    ast.Slice, ast.IfExp, ast.ListComp, ast.comprehension,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Pow, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
)
_IMAGE = "python:3.12-alpine"


class FactoryError(ValueError):
    """Sanitized, predictable rejection code; never reveals LLM output."""


def _number(x: Any) -> float:
    if type(x) not in (int, float) or not math.isfinite(float(x)) or abs(x) > 1e9:
        raise FactoryError("INVALID_ORACLE_NUMBER")
    return float(x)


def check_source(source: str) -> None:
    if not isinstance(source, str) or not 20 <= len(source) <= 6000:
        raise FactoryError("INVALID_TOOL_SOURCE")
    try:
        tree = ast.parse(source, mode="exec")
    except (SyntaxError, ValueError):
        raise FactoryError("INVALID_TOOL_SYNTAX") from None
    if not tree.body or any(not isinstance(x, ast.FunctionDef) for x in tree.body):
        raise FactoryError("TOOL_MODULE_MUST_ONLY_DEFINE_FUNCTIONS")
    functions = {x.name for x in tree.body}
    if "analyze" not in functions or len(functions) != len(tree.body):
        raise FactoryError("MISSING_ANALYZE_ENTRYPOINT")
    for func in tree.body:
        if (func.name.startswith("_") or func.name in _BLOCK_NAMES
                or func.name in _ALLOWED_CALLS or func.decorator_list
                or len(func.args.args) != 1 or func.args.args[0].arg != "values"
                or func.args.defaults or func.args.kwarg_defaults
                or func.args.kwarg or func.args.vararg or func.returns):
            raise FactoryError("UNSAFE_FUNCTION_CONTRACT")
    nodes = list(ast.walk(tree))
    if len(nodes) > 350:
        raise FactoryError("TOOL_COMPLEXITY_BUDGET")
    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise FactoryError("UNSAFE_PYTHON_SYNTAX")
        if isinstance(node, ast.Name) and (
            node.id.startswith("_") or node.id in _BLOCK_NAMES
        ):
            raise FactoryError("UNSAFE_NAME")
        if isinstance(node, ast.arg) and (
            node.arg.startswith("_") or node.arg in _BLOCK_NAMES
        ):
            raise FactoryError("UNSAFE_NAME")
        if isinstance(node, ast.Call):
            if (not isinstance(node.func, ast.Name)
                    or node.func.id not in _ALLOWED_CALLS | functions):
                raise FactoryError("UNSAFE_CALL")
        if isinstance(node, ast.Constant) and (
            type(node.value) not in (int, float, str, bool, type(None))
            or isinstance(node.value, str) and len(node.value) > 200
            or type(node.value) in (int, float) and abs(node.value) > 1e12
        ):
            raise FactoryError("UNSAFE_LITERAL")


def validate_candidate(data: Any) -> dict:
    if not isinstance(data, dict) or set(data) != {
        "tool_id", "version", "effect", "purpose", "code", "examples"
    }:
        raise FactoryError("INVALID_TOOL_SPEC")
    if not isinstance(data["tool_id"], str) or not _ID.fullmatch(data["tool_id"]):
        raise FactoryError("INVALID_TOOL_ID")
    if not isinstance(data["version"], str) or not _VERSION.fullmatch(data["version"]):
        raise FactoryError("INVALID_TOOL_VERSION")
    if data["effect"] != "PURE":
        raise FactoryError("TOOL_EFFECT_DENIED")
    if (not isinstance(data["purpose"], str)
            or not 8 <= len(data["purpose"]) <= 500):
        raise FactoryError("INVALID_TOOL_PURPOSE")
    check_source(data["code"])
    examples = data["examples"]
    if not isinstance(examples, list) or not 1 <= len(examples) <= 12:
        raise FactoryError("INVALID_TOOL_EXAMPLES")
    for item in examples:
        if not isinstance(item, dict) or set(item) != {"input", "expected"}:
            raise FactoryError("INVALID_TOOL_EXAMPLES")
        values = item["input"]
        if not isinstance(values, list) or not 1 <= len(values) <= 128:
            raise FactoryError("INVALID_TOOL_EXAMPLES")
        [_number(v) for v in values]
        _number(item["expected"])
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return {"tool_id": data["tool_id"], "version": data["version"],
            "status": "AWAITING_INDEPENDENT_EVALUATION",
            "tier": "T0_PROPOSED", "execution": "DISABLED",
            "broker_execution": False, "decision": "NO_TRADE",
            "sha256": hashlib.sha256(encoded).hexdigest()}


def propose_from_llm(goal: str, client: Any) -> tuple[dict, dict]:
    """One opt-in bounded provider call; never interpret output as permissions."""
    if not isinstance(goal, str) or not 10 <= len(goal) <= 600:
        raise FactoryError("INVALID_TOOL_GOAL")
    prompt = json.dumps({
        "task": "Generate one NEW PURE numerical research function, not a trading tool.",
        "goal": goal,
        "output_schema": {
            "tool_id": "snake_case_ascii", "version": "0.1.0",
            "effect": "PURE", "purpose": "8-500 chars",
            "code": "Python source: def analyze(values): return numeric result",
            "examples": [{"input": [1, 2, 3], "expected": 2}],
        },
        "restrictions": [
            "JSON object only, max 4500 characters", "No imports",
            "No file/network/process/account access", "No trade execution",
            "No policy/permission modifications", "No function attributes",
            "Computations with bounded numeric lists only",
            "Examples are untrusted and will not certify correctness",
        ],
    }, separators=(",", ":"))
    try:
        answer = client.complete("Tool Builder (T0 research)", prompt)
        if not isinstance(answer, str) or len(answer) > 4500:
            raise ValueError()
        candidate = json.loads(answer)
    except (ValueError, TypeError, AttributeError):
        raise FactoryError("MODEL_SPEC_INVALID") from None
    record = validate_candidate(candidate)
    return candidate, record


def save_proposal(candidate: dict, directory: Path) -> dict:
    """Private, immutable local artifact. Never write a repo registry or risk file."""
    record = validate_candidate(candidate)
    root = Path(directory)
    if root.is_symlink():
        raise FactoryError("UNSAFE_ARTIFACT_DIRECTORY")
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / (record["tool_id"] + ".json")
        raw = json.dumps(candidate, sort_keys=True, indent=2,
                         allow_nan=False).encode("utf-8")
        if path.is_symlink():
            raise FactoryError("UNSAFE_ARTIFACT_PATH")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise FactoryError("TOOL_ID_CONFLICT")
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
        return record
    except FactoryError:
        raise
    except OSError:
        raise FactoryError("PROPOSAL_STORAGE_UNAVAILABLE") from None


def independent_cases(tool_id: str, oracle_path: Path) -> list[dict] | None:
    """Golden fixtures are versioned OUTSIDE agent-controlled candidate JSON."""
    try:
        data = json.loads(Path(oracle_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FactoryError("ORACLE_UNAVAILABLE") from None
    if not isinstance(data, dict):
        raise FactoryError("ORACLE_CORRUPT")
    cases = data.get(tool_id)
    if cases is None:
        return None  # novel tool: T0 proposal only, no invented PASS
    if not isinstance(cases, list) or not 3 <= len(cases) <= 40:
        raise FactoryError("ORACLE_CORRUPT")
    for item in cases:
        if not isinstance(item, dict) or set(item) != {"input", "expected"}:
            raise FactoryError("ORACLE_CORRUPT")
        if not isinstance(item["input"], list) or not 1 <= len(item["input"]) <= 128:
            raise FactoryError("ORACLE_CORRUPT")
        [_number(v) for v in item["input"]]
        _number(item["expected"])
    return cases


def sandbox_evaluate(candidate: dict, oracles: list[dict] | None,
                     *, timeout_seconds: int = 15) -> dict:
    """Only explicitly invoked Docker, never execute generated code in-process.

    No secrets/network/workdir mounts. Docker containment is defense in depth,
    NOT a perfect security boundary or owner-approved production release.
    """
    record = validate_candidate(candidate)
    if oracles is None:
        return {**record, "status": "PENDING_INDEPENDENT_ORACLE"}
    if type(timeout_seconds) is not int or not 2 <= timeout_seconds <= 30:
        raise FactoryError("INVALID_SANDBOX_BUDGET")
    if not isinstance(oracles, list) or not 3 <= len(oracles) <= 40:
        raise FactoryError("ORACLE_CORRUPT")
    for x in oracles:
        if not isinstance(x, dict) or set(x) != {"input", "expected"}:
            raise FactoryError("ORACLE_CORRUPT")
        if not isinstance(x["input"], list) or not 1 <= len(x["input"]) <= 128:
            raise FactoryError("ORACLE_CORRUPT")
        [_number(v) for v in x["input"]]
        _number(x["expected"])
    runner = Path(__file__).resolve().parent.parent / "tools" / "factory_sandbox_runner.py"
    try:
        with tempfile.TemporaryDirectory(prefix="t0_tool_") as root:
            place = Path(root)
            (place / "candidate.py").write_text(candidate["code"], encoding="utf-8")
            (place / "oracles.json").write_text(json.dumps(oracles), encoding="utf-8")
            (place / "runner.py").write_bytes(runner.read_bytes())
            # Docker non-root user needs read access to this ephemeral directory.
            os.chmod(place, 0o755)
            cmd = [
                "docker", "run", "--rm", "--network", "none",
                "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--pids-limit", "32", "--memory", "128m", "--cpus", "1",
                "--user", "65534:65534", "--tmpfs",
                "/tmp:rw,nosuid,noexec,size=16m",
                "--mount", f"type=bind,src={place},dst=/work,readonly",
                "--workdir", "/work", _IMAGE,
                "python", "-I", "-B", "-S", "/work/runner.py",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=timeout_seconds, check=False)
        return {**record,
                "status": ("SANDBOX_PASSED_REVIEW_REQUIRED" if result.returncode == 0
                           else "SANDBOX_FAILED"),
                "oracle_cases": len(oracles), "broker_execution": False}
    except subprocess.TimeoutExpired:
        return {**record, "status": "SANDBOX_TIMEOUT"}
    except (OSError, ValueError):
        raise FactoryError("SANDBOX_UNAVAILABLE") from None
