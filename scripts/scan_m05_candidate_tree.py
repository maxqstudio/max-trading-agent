from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("vault_token", re.compile(r"\bhv[bs]\.[A-Za-z0-9_-]{20,}\b")),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    ("bearer_literal", re.compile(r"Bearer\s+[A-Za-z0-9._-]{30,}")),
)

FORBIDDEN_TRACKED_BASENAMES = {
    "settings.json",
    "settings.backup.json",
    "llm_api_key.dpapi",
}


def git_bytes(*args: str, root: Path = ROOT) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"GIT_COMMAND_FAILED:{' '.join(args)}:{detail}")
    return completed.stdout


def resolve_candidate(candidate_sha: str, *, root: Path = ROOT) -> str:
    resolved = git_bytes(
        "rev-parse",
        "--verify",
        f"{candidate_sha}^{{commit}}",
        root=root,
    ).decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", resolved):
        raise RuntimeError("CANDIDATE_SHA_INVALID")
    return resolved


def scan_candidate_tree(candidate_sha: str, *, root: Path = ROOT) -> dict[str, Any]:
    resolved = resolve_candidate(candidate_sha, root=root)
    names = git_bytes(
        "ls-tree",
        "-r",
        "--name-only",
        resolved,
        root=root,
    ).decode("utf-8").splitlines()

    matches: list[dict[str, Any]] = []
    forbidden: list[str] = []
    scanned_text_files = 0

    for raw_name in names:
        name = raw_name.strip()
        if not name:
            continue
        if Path(name).name.casefold() in FORBIDDEN_TRACKED_BASENAMES:
            forbidden.append(name)

        blob = git_bytes("show", f"{resolved}:{name}", root=root)
        if b"\x00" in blob:
            continue
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue

        scanned_text_files += 1
        for rule, pattern in SECRET_RULES:
            for hit in pattern.finditer(text):
                literal = hit.group(0)
                if literal == "Bearer ollama":
                    continue
                matches.append(
                    {
                        "file": name,
                        "rule": rule,
                        "line": text.count("\n", 0, hit.start()) + 1,
                        "redacted_fingerprint": hashlib.sha256(
                            literal.encode("utf-8")
                        ).hexdigest()[:12],
                    }
                )

    result = {
        "schema": "MAX_REBUILD_M05_EXACT_GIT_TREE_SECRET_SCAN_V1",
        "scope": "EXACT_GIT_CANDIDATE_TREE",
        "candidate_sha": resolved,
        "scanned_tracked_text_files": scanned_text_files,
        "matches": len(matches) + len(forbidden),
        "high_confidence_matches": matches,
        "dpapi_or_settings_file_tracked": bool(forbidden),
        "forbidden_secret_storage_tracked": sorted(forbidden),
        "status": "PASS" if not matches and not forbidden else "FAIL",
        "claim": "exact candidate tree contains no detected plaintext secret"
        if not matches and not forbidden
        else "exact candidate tree secret scan found findings",
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    result = scan_candidate_tree(args.candidate_sha)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
