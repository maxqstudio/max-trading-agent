ROLE_OPTIMIZER_WINNER = "OPTIMIZER_WINNER"
ROLE_OWNER_SELECTED_QUALIFIED_CANDIDATE = "OWNER_SELECTED_QUALIFIED_CANDIDATE"

OPTIMIZER_REQUEST_SCHEMA_CURRENT = "MAX_REBUILD_OPTIMIZER_REQUEST_V6"
OPTIMIZER_TERMINAL_QUALIFIED_POOL = "QUALIFIED_POOL_READY"

OPTIMIZER_WORKFLOW_LEGACY = "LEGACY_AUTOMATIC_WINNER"
OPTIMIZER_WORKFLOW_OWNER_EXPLICIT = "QUALIFIED_POOL_OWNER_SELECTION"
OPTIMIZER_WORKFLOW_FIELD = "optimizer_result_workflow"

_SCHEMA_WORKFLOW_DEFAULTS = {
    "MAX_REBUILD_OPTIMIZER_REQUEST_V1": OPTIMIZER_WORKFLOW_LEGACY,
    "MAX_REBUILD_OPTIMIZER_REQUEST_V2": OPTIMIZER_WORKFLOW_LEGACY,
    "MAX_REBUILD_OPTIMIZER_REQUEST_V3": OPTIMIZER_WORKFLOW_LEGACY,
    "MAX_REBUILD_OPTIMIZER_REQUEST_V4": OPTIMIZER_WORKFLOW_OWNER_EXPLICIT,
}
_SUPPORTED_WORKFLOWS = {
    OPTIMIZER_WORKFLOW_LEGACY,
    OPTIMIZER_WORKFLOW_OWNER_EXPLICIT,
}


def optimizer_result_workflow(request: dict) -> str:
    """Return the workflow capability frozen into an optimizer request.

    Historical request schemas are deterministically mapped for compatibility.
    Future schemas must persist the capability explicitly instead of inheriting
    behavior from whichever schema happens to be current at runtime.
    """
    explicit = request.get(OPTIMIZER_WORKFLOW_FIELD)
    if explicit is not None:
        value = str(explicit)
        if value not in _SUPPORTED_WORKFLOWS:
            raise RuntimeError("OPTIMIZER_RESULT_WORKFLOW_UNSUPPORTED")
        return value

    schema = str(request.get("schema") or "")
    mapped = _SCHEMA_WORKFLOW_DEFAULTS.get(schema)
    if mapped is None:
        raise RuntimeError("OPTIMIZER_RESULT_WORKFLOW_MISSING")
    return mapped


def optimizer_uses_owner_selection(request: dict) -> bool:
    return optimizer_result_workflow(request) == OPTIMIZER_WORKFLOW_OWNER_EXPLICIT
