"""G34: offline guard against mutable or inconsistent Docker sandbox images.

Targeted checks cover the executable G21 T0 sandbox and every hosted workflow
that pulls that image. No image code is executed by this static audit; actual
image identity/platform is verified by a separate GitHub-hosted Docker job.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

DIGEST = "a61cbd76a882645de7f91423329fe43a67eed60d7da24f9e5331414bb3618b20"
IMAGE = "python:3.12-alpine@sha256:" + DIGEST
PLATFORM = "linux/amd64"
SANDBOX = "max_trading_agent/tool_factory.py"
PULL_WORKFLOWS = (
    ".github/workflows/g24-signed-t0.yml",
    ".github/workflows/g29-ephemeral-main-merge.yml",
    ".github/workflows/g30-sequential-main.yml",
    ".github/workflows/g31-postmerge-main.yml",
    ".github/workflows/g35-sequential-main.yml",
    ".github/workflows/tool-factory.yml",
)
REQUIRED = (SANDBOX, *PULL_WORKFLOWS)
TAG_ONLY = re.compile(r"\bpython:3\.12-alpine(?!@sha256:[0-9a-f]{64})")
SHA_FORMAT = re.compile(r"[0-9a-f]{64}\Z")


class DockerImageAuditError(ValueError):
    """Fixed, non-sensitive reason code."""


def audit_sources(sources: Mapping[str, str]) -> dict:
    if type(sources) is not dict or set(sources) != set(REQUIRED):
        raise DockerImageAuditError("G34_MISSING_REQUIRED_SOURCE")
    if not SHA_FORMAT.fullmatch(DIGEST):
        raise DockerImageAuditError("G34_INVALID_PIN")
    for path in REQUIRED:
        value = sources[path]
        if type(value) is not str or not value or len(value) > 200_000:
            raise DockerImageAuditError("G34_INVALID_SOURCE_BYTES")
        if TAG_ONLY.search(value):
            raise DockerImageAuditError("G34_MUTABLE_DOCKER_TAG")
        if path == SANDBOX:
            if value.count('_IMAGE = "' + IMAGE + '"') != 1:
                raise DockerImageAuditError("G34_UNPINNED_EXECUTION_IMAGE")
            if not re.search(
                r'"docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none"',
                value,
            ):
                raise DockerImageAuditError("G34_CONTAINER_PLATFORM_OR_NETWORK_CHANGED")
            if not all(token in value for token in (
                '"--read-only"', '"--cap-drop", "ALL"',
                '"--security-opt", "no-new-privileges"',
                '"--user", "65534:65534"', '"--pids-limit", "32"',
                '"--memory", "128m"', '"--cpus", "1"',
            )):
                raise DockerImageAuditError("G34_SANDBOX_ISOLATION_CHANGED")
        else:
            expected = "run: docker pull --platform linux/amd64 " + IMAGE
            if value.count(expected) != 1:
                raise DockerImageAuditError("G34_WORKFLOW_PULL_NOT_PINNED")
            if len(re.findall(r"\bdocker pull\b", value)) != 1:
                raise DockerImageAuditError("G34_UNEXPECTED_WORKFLOW_PULL")
    return {
        "schema_version": 1,
        "status": "G34_STATIC_DOCKER_IMAGE_PIN_AUDIT_PASS",
        "target": "OFFICIAL_DOCKER_PYTHON_MANIFEST_LINUX_AMD64",
        "image": IMAGE,
        "manifest_digest": DIGEST,
        "platform": PLATFORM,
        "source_files_checked": len(REQUIRED),
        "workflow_pull_count": len(PULL_WORKFLOWS),
        "runtime_image_pinned": True,
        "runtime_image_executed_by_static_audit": False,
        "image_has_no_known_vulnerabilities": False,
        "image_signature_verified": False,
        "owner_acceptance": "PENDING",
        "broker_execution": False,
        "execution": "DISABLED",
        "decision": "NO_TRADE",
    }


def audit_repository(root: Path) -> dict:
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise DockerImageAuditError("G34_UNSAFE_REPOSITORY")
    sources: dict[str, str] = {}
    for name in REQUIRED:
        p = root / name
        if not p.is_file() or p.is_symlink() or p.stat().st_size > 200_000:
            raise DockerImageAuditError("G34_SOURCE_MISSING_OR_UNSAFE")
        try:
            sources[name] = p.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise DockerImageAuditError("G34_UNREADABLE_SOURCE") from None
    return audit_sources(sources)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(prog="g34-docker-image-pin")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    try:
        report = audit_repository(Path(args.root))
    except (DockerImageAuditError, ValueError, OSError, UnicodeError):
        raise SystemExit("G34_DOCKER_IMAGE_AUDIT_BLOCKED") from None
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
