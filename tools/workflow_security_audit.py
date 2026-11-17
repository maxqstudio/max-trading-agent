"""G33 fail-closed GitHub Actions supply-chain review (offline and read-only).

Prevent mutable action references, persisted checkout tokens and accidentally
broadened OIDC/attestation authority. This static check does NOT prove what a
remote action or an executed workflow does.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_APPROVED_ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/setup-node": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/attest": "1e69f48acb82d1966a394da916b4c1698aa569d6",
}
_ALLOWED_SCOPES = frozenset({
    "contents", "pull-requests", "actions", "id-token", "attestations"
})
_SIGNER = "g24-signed-t0.yml"
_SIGNER_JOB = "attest-isolated-tool-review"
_USE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)\s*(?:#.*)?$")
_STEP = re.compile(r"^ {6}- (?:name|uses|run|id):")
_JOB = re.compile(r"^  ([a-z][a-z0-9-]*):\s*$")
_SCOPE = re.compile(r"^( {2}| {6})([a-z][a-z0-9-]*):\s*(read|write)\s*$")
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class WorkflowSecurityError(ValueError):
    """Sanitized error: never expose command output, tokens, or workflow text."""


def _permissions(lines: list[str], *, indent: int, start: int) -> dict[str, str]:
    values: dict[str, str] = {}
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        spaces = len(line) - len(line.lstrip(" "))
        if spaces < indent:
            break
        match = _SCOPE.fullmatch(line)
        if (spaces != indent or match is None
                or match.group(2) not in _ALLOWED_SCOPES
                or match.group(2) in values):
            raise WorkflowSecurityError("G33_INVALID_PERMISSIONS")
        values[match.group(2)] = match.group(3)
        i += 1
    if not values:
        raise WorkflowSecurityError("G33_MISSING_PERMISSIONS")
    return values


def inspect_workflow(filename: str, source: str) -> dict:
    if type(filename) is not str or not filename.endswith((".yml", ".yaml")):
        raise WorkflowSecurityError("G33_INVALID_WORKFLOW_NAME")
    if type(source) is not str or not 30 <= len(source) <= 150_000:
        raise WorkflowSecurityError("G33_INVALID_WORKFLOW_BYTES")
    lines = source.splitlines()
    if not any(line == "jobs:" for line in lines):
        raise WorkflowSecurityError("G33_MISSING_JOBS")
    if not any(line == "on:" for line in lines):
        raise WorkflowSecurityError("G33_MISSING_TRIGGER")
    if any(re.match(r"^  (?:pull_request_target|workflow_run):\s*", line)
           for line in lines):
        raise WorkflowSecurityError("G33_UNTRUSTED_PRIVILEGED_TRIGGER")
    global_indices = [i for i, line in enumerate(lines)
                      if line == "permissions:"]
    if len(global_indices) != 1:
        raise WorkflowSecurityError("G33_GLOBAL_PERMISSIONS_REQUIRED")
    global_grants = _permissions(lines, indent=2, start=global_indices[0])
    if (global_grants.get("contents") != "read"
            or any(value != "read" for value in global_grants.values())):
        raise WorkflowSecurityError("G33_GLOBAL_PERMISSIONS_TOO_BROAD")

    jobs_index = lines.index("jobs:")
    current_job: str | None = None
    job_grants: dict[str, dict[str, str]] = {}
    seen_jobs: set[str] = set()
    actions: list[dict] = []
    checkouts = 0

    for i, line in enumerate(lines):
        if i > jobs_index:
            job = _JOB.fullmatch(line)
            if job:
                current_job = job.group(1)
                if current_job in seen_jobs:
                    raise WorkflowSecurityError("G33_DUPLICATE_JOB")
                seen_jobs.add(current_job)
            if line.startswith("    permissions:"):
                if line != "    permissions:" or not current_job:
                    raise WorkflowSecurityError("G33_INVALID_JOB_PERMISSIONS")
                if current_job in job_grants:
                    raise WorkflowSecurityError("G33_DUPLICATE_JOB_PERMISSIONS")
                job_grants[current_job] = _permissions(lines, indent=6, start=i)

        match = _USE.fullmatch(line)
        # Disallow YAML quirks that could mask an external action: a step/job
        # containing a uses key must match this exact non-templated grammar.
        if re.match(r"^\s*(?:-\s+)?uses\s*:", line) and match is None:
            raise WorkflowSecurityError("G33_MALFORMED_USES")
        if match is None:
            continue
        spec = match.group(1)
        if "@" not in spec:
            raise WorkflowSecurityError("G33_ACTION_NOT_PINNED")
        name, revision = spec.rsplit("@", 1)
        if (name not in _APPROVED_ACTIONS
                or _SHA.fullmatch(revision) is None
                or _APPROVED_ACTIONS[name] != revision):
            raise WorkflowSecurityError("G33_UNREVIEWED_ACTION_REFERENCE")
        if current_job is None:
            raise WorkflowSecurityError("G33_ACTION_OUTSIDE_JOB")
        actions.append({"name": name, "commit_sha": revision})
        if name != "actions/checkout":
            continue
        checkouts += 1
        # Checkout credentials must never be persisted in .git/config, even
        # for read-only PR jobs and OIDC-attestation producer workflows.
        end = i + 1
        while end < len(lines) and not _STEP.match(lines[end]) and (
                _JOB.fullmatch(lines[end]) is None):
            end += 1
        with_block = lines[i+1:end]
        configured = [row.strip().split(":", 1)[1].strip()
                      for row in with_block
                      if re.fullmatch(r" {10}persist-credentials:\s*\S+\s*", row)]
        if configured != ["false"]:
            raise WorkflowSecurityError("G33_CHECKOUT_TOKEN_PERSISTENCE")

    if not seen_jobs or not actions or not checkouts:
        raise WorkflowSecurityError("G33_MISSING_ACTION_OR_CHECKOUT")
    signature_jobs = []
    for job, scopes in job_grants.items():
        if scopes.get("contents") != "read":
            raise WorkflowSecurityError("G33_JOB_CONTENTS_NOT_READ_ONLY")
        for scope, access in scopes.items():
            if access == "write":
                if not (filename == _SIGNER and job == _SIGNER_JOB
                        and scope in {"id-token", "attestations"}):
                    raise WorkflowSecurityError("G33_UNEXPECTED_WRITE_PERMISSION")
        if scopes.get("id-token") == "write" or scopes.get("attestations") == "write":
            if (filename != _SIGNER or job != _SIGNER_JOB
                    or scopes.get("id-token") != "write"
                    or scopes.get("attestations") != "write"):
                raise WorkflowSecurityError("G33_INCOMPLETE_SIGNER_PERMISSION")
            signature_jobs.append(job)

    if filename == _SIGNER and signature_jobs != [_SIGNER_JOB]:
        raise WorkflowSecurityError("G33_EXPECTED_SIGNER_JOB_MISSING")
    return {
        "workflow": filename,
        "pinned_actions": len(actions),
        "checkout_credentials_not_persisted": checkouts,
        "signature_jobs": signature_jobs,
    }


def audit_workflows(root: Path) -> dict:
    folder = Path(root) / ".github" / "workflows"
    if not folder.is_dir() or folder.is_symlink():
        raise WorkflowSecurityError("G33_UNSAFE_WORKFLOW_DIRECTORY")
    files = sorted(
        file for file in folder.iterdir()
        if file.suffix in (".yml", ".yaml")
    )
    if not 1 <= len(files) <= 32:
        raise WorkflowSecurityError("G33_UNBOUNDED_WORKFLOW_COUNT")
    reports = []
    for path in files:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 150_000:
            raise WorkflowSecurityError("G33_UNSAFE_WORKFLOW_FILE")
        try:
            contents = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise WorkflowSecurityError("G33_UNREADABLE_WORKFLOW") from None
        reports.append(inspect_workflow(path.name, contents))
    names = [x["workflow"] for x in reports]
    if _SIGNER not in names:
        raise WorkflowSecurityError("G33_SIGNER_WORKFLOW_MISSING")
    return {
        "schema_version": 1,
        "status": "G33_WORKFLOW_SECURITY_STATIC_AUDIT_PASS",
        "scope": "GITHUB_ACTIONS_STATIC_POLICY_ONLY",
        "workflow_count": len(reports),
        "pinned_action_references": sum(x["pinned_actions"] for x in reports),
        "checkout_credentials_disabled": sum(
            x["checkout_credentials_not_persisted"] for x in reports),
        "signer_workflow": _SIGNER,
        "signer_job": _SIGNER_JOB,
        "privileged_job_count": sum(len(x["signature_jobs"]) for x in reports),
        "workflows": reports,
        "dynamic_runner_behavior_proven_safe": False,
        "third_party_action_code_audited": False,
        "all_supply_chain_risks_eliminated": False,
        "broker_execution": False,
        "owner_acceptance": "PENDING",
        "execution": "DISABLED",
        "decision": "NO_TRADE",
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="g33-workflow-security-audit")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    try:
        report = audit_workflows(Path(args.root))
    except (OSError, ValueError, UnicodeError):
        raise SystemExit("G33_WORKFLOW_AUDIT_BLOCKED") from None
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
