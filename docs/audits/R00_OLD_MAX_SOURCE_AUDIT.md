# R00 Old MAX Source Audit

Status: SOURCE AUDIT EVIDENCE
Current build authority: `maxqstudio/max_rebuild`
Historical source authority: `maxqstudio/max_research_agent@3e969efcdeb4ca6a2ae63acbd80592e378d2a446`

This file records the mandatory R00 inspection of historical **source code**. Historical documentation was not used as a substitute for source inspection.

## Source inspected

- `ModelLab/research/research_control.py`
- `ModelLab/research/research_checkpoint.py`
- `ModelLab/research/research_memory.py`
- `ModelLab/research/structured_research_memory.py`
- `ModelLab/research/research_engine_v3.py`
- `ModelLab/research/research_planner.py`
- `ModelLab/research/research_feedback.py`
- `ModelLab/research/future_learning_foundation.py`
- `ModelLab/research/research_e2e.py`
- `ModelLab/host/hardware_profile.py`
- `ModelLab/models/model_registry.py`
- `ModelLab/models/capacity_governor.py`
- `ModelLab/max_graph/state.py`
- `ModelLab/scientist/python/scientist_python_capabilities.py`
- `ModelLab/scientist/python/scientist_python_runtime.py`
- `ModelLab/core/checkpoint_integrity.py`
- `ModelLab/core/artifact_paths.py`

## Port decisions

| Old implementation | Domain invariant | Defect / debt found | Decision | New R00 implementation |
| --- | --- | --- | --- | --- |
| Manual Research separates Owner candidate intent from deterministic validation | Owner intent must never become scientific PASS authority | Old Factory path mixes proposal/search controls with larger autonomous lifecycle | KEEP invariant, simplify | Persisted Owner authorization is separate from deterministic R00 validators |
| Atomic research checkpoint with SHA and backup | Restart must consume committed state only | File checkpoint authority duplicated other runtime state | REPAIR | SQLite is sole state authority; sealed manifests are immutable evidence, not a second mutable state store |
| Research Memory traverses run lineage | Scientific learning requires explicit lineage | Memory could become coupled to run-folder conventions | KEEP invariant, repair storage | Append-only SQLite Research Memory events bind research ID and source-manifest SHA |
| Structured Research Memory classifies ADAPTIVE / VALIDATION / PROTECTED stages | Locked/Fresh/Champion evidence must not silently become tuning feedback | Alias-heavy legacy machinery is broader than R00 needs | KEEP invariant, simplify | Explicit learning zones; protected stages are never adaptive-eligible |
| Candidate validation preserves exact Owner-entered semantics | Candidate identity must bind executable scientific intent | Legacy registry/config contains historical family bounds and complexity | KEEP invariant, discard registry ceilings | R00 defines deterministic future candidate identity only; no candidate is trained or admitted yet |
| Capacity governor distinguishes LEGAL, RESOURCE, SCIENTIFIC | Executable capacity is the minimum of three independent authorities | Legacy resource proxy includes numeric parameter ceilings that can become accidental hard ceilings | REPAIR | R00 freezes the equation and hardware evidence; no arbitrary global parameter-count ceiling |
| Hardware profile leaves unavailable truth unknown | Resource authority must not fabricate hardware | Runtime free RAM/VRAM mixed with identity snapshot | KEEP + REPAIR | Stable hardware identity hash excludes dynamic free-resource values; both remain recorded |
| Scientist Python uses opaque input IDs and a strict capability allowlist | Scientist computation is analytical evidence only | Dedicated interpreter/runtime is substantial machinery not required for R00 | KEEP boundary, defer runtime | R00 freezes advisory/sandbox contract only; no unrestricted Python execution is added |
| Research E2E labels synthetic evidence as non-production | Test evidence must never masquerade as scientific evidence | Synthetic workflow can fabricate promotion-runtime proof inside sandbox E2E | DISCARD for production authority | R00 validators use only deterministic foundation evidence; no synthetic model or promotion evidence |
| Future learning foundations disable RL/RAG/embedder fail-closed | Unimplemented adaptive systems must stay disabled | Extra interfaces add complexity before scientific need exists | KEEP fail-closed principle, discard machinery | R00 creates no adaptive self-training, RAG, RL, or hidden learning loop |
| Legacy UI / CMD / run-folder orchestration | Human authority and clear state visibility matter | Streamlit/legacy shell and duplicated operational state are obsolete | DISCARD implementation | New React Research page uses current MAX REBUILD design and backend-persisted authority |

## R00 conclusions

Retained:

- explicit Owner authorization;
- deterministic validation authority;
- immutable lineage and hashes;
- fail-closed restart semantics;
- append-only Research Memory;
- protected-evaluation memory isolation;
- bounded Scientist authority;
- hardware truthfulness;
- dynamic capacity as `min(LEGAL, RESOURCE, SCIENTIFIC)`.

Rejected:

- legacy UI;
- old CMD orchestration;
- duplicated mutable state;
- autonomous gate chaining;
- old hard-coded/global parameter ceilings;
- synthetic E2E evidence as production authority;
- leakage-prone or protected-evidence feedback;
- unrestricted Scientist execution.

R00 does not port a model trainer, dataset builder, ONNX exporter, Research Challenger producer, or Champion mutation path.
