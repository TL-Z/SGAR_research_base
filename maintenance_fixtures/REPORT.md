# SGAR Linux resource hash/readiness/composition report

Status: `SGAR_LINUX_PRE_CASE_NOT_READY`

## Scope and source state

- Workspace: `/home/zhoutianle/Projects/SGAR_research_base`
- Branch/HEAD: `master` / `542f06ea8389df19767596eb9e3b25c64f7fcccd`
- Existing dirty changes were preserved; no reset, stash, clean, checkout, commit, or push was performed.
- Docker image identity recorded at task start: `sha256:3df4a3e71cdb166638b00b2b1af650d54068abb07c11c05042d3f67c9d699185`.
- The only source change made in this task is the generic `CandidateOrigin.USER_EXECUTION_REQUIREMENT` validation correction in `sgar_mvp/src/pipeline_control.py`; prior dirty changes remain separate.

## Skill package hashes

- `skill_package_hash()` now uses a shared component-wise casefold/path tie-break ordering in readiness, SkillRuntime, and the generator.
- Casefold collisions fail closed; ignored directories, POSIX path framing, and file-byte framing remain unchanged.
- Focused tests: 4 passed (including execution-requirement positive/negative checks).
- Real Skill packages: 154/154 match existing manifests; no Skill files or manifest hashes were rewritten. Evidence: `SKILL_HASH_VALIDATION.json`.

## Readiness and index

- Catalog: 338 resources.
- Effective pool: 328 (`Model` 16 ready, 2 blocked, 1 inactive; `Agent` 20 ready; `Tool` 138 ready, 5 transient, 2 inactive; `Skill` 154 ready).
- Active 4B index generation: `2cd50d51aabe41a5972c0d41f578727c`.
- Index and loader: 328 rows, two FAISS indexes at 328 x 2560, Qwen/Qwen3-Embedding-4B identity unchanged, directed Model/Agent/Tool/Skill queries passed. Evidence: `INDEX_VALIDATION.json`.
- Old generation remains in `index-backup-pre-switch`.

## Exceptional Tools

- Network transient tools were diagnosed once in the sealed runtime; failures remain transient and excluded, with no host DNS/proxy/allowlist changes.
- Coingecko and OpenSky source bytes matched Git HEAD; their formal manifests were refreshed through the targeted generator and each had one smoke attempt.
- Dependency CVE checker startup/dependency/database/network stages were diagnosed; the focused smoke passed.
- ESLint formatter and npm package auditor remain inactive by policy.
- Full evidence is in `TOOL_EXCEPTION_DIAGNOSTICS.json`.

## Control receipts

- Five registry roles (`profiler`, `planner`, `plan_compiler`, `plan_adaptation`, `evaluator`) were refreshed once each with Sol, strict production requests, SDK/infrastructure retry 0.
- All five passed; receipt result identity `6132c39ef2c604be9c193ded7823a0b43bb00eac0ac64990b93e891a5a676a57`.
- Receipt validation is in `CONTROL_RECEIPT_VALIDATION.json`; cost was USD `0.017381250000`.
- Opus 5 and Qwen3 Coder Next were not called and remain blocked.

## Candidate-pool fix and composition

- Offline reproduction identified `dependency_candidate_requires_parent_resource` for a top-level user execution requirement. The validator now treats `user_execution_requirement` as an independent, top-level candidate origin while retaining parent enforcement for real dependency origins.
- The first synthetic run reached Planner/Profiler and then failed at candidate-pool sealing; no Compiler/Agent/Tool calls occurred.
- The bounded rerun reached Compiler with the requested Agent, Qwen3.5-35B-A3B base Model, filesystem Tool operation, and verification Skill visible. Compiler rejected the model proposal with `compiler_v3_input_artifact_type_incompatible`: the Skill was emitted as a standalone step and its output was not mapped to a compatible Agent input artifact. No Agent, ResourceRuntime, Tool, Skill injection, evaluator, commit, or delivery event occurred.
- This is the first open boundary. It is a model/compiler contract realization failure, not a Skill hash, readiness, index, embedding, or blocked-model issue. No additional retries or production-specific compatibility branch were added.
- Composition evidence and accounting are in `COMPOSITION_EVIDENCE.json` and `PROVIDER_ACCOUNTING.json`.

## Validation boundary

Passed: imports/targeted unit tests, Skill hash verification, readiness/index consistency, active loader/directed retrieval, control receipt identity, focused Tool diagnostics, and offline candidate-origin checks.

Not run: full pytest, benchmark, training, batch runs, real business cases, blocked-model retests, and unrelated resource-wide smoke tests.

The pre-case gate is therefore **not ready** until the generic Compiler contract can represent an advisory Skill injection on the same Agent controller session and validate that mapping deterministically. The next real-case command template is provided in `NEXT_REAL_CASE_COMMAND.sh`; it was not executed.
