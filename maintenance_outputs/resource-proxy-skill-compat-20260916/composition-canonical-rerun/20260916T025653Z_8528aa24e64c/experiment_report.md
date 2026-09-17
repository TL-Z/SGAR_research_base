# S-GAR Pipeline Execution Report

**Query**: `<task-query sha256=885175426df1064265378c315e8c2017891e3b30e584730f0817af9c5564e2ee utf8_bytes=881>`

**Environment profile**: docker_cli=`True` docker_daemon=`False` docker_ready=`False`

**Control model chain**: configured=`gpt-5.6-sol` available=`gpt-5.6-sol`

**Planner parse**: mode=`json_schema` requested=`json_schema` attempt=`1` control_model=`gpt-5.6-sol`

## Replan History

- replan_count=`0` protected=`task_001` subtasks=`task_001:markdown.md`

## 1. Task Decomposition (Planner)

| ID | Role | Description | Artifact | Dependencies |
| -- | ---- | ----------- | -------- | ------------ |
| `task_001` | **tool-grounded evidence note producer** | As one indivisible final-output subtask, use Agent resource_id agen... | `markdown` | none |

## 2. Orchestration Graph

```mermaid
graph TD
    Q["<task-query sha256=88517542..."] --> P[SGAR Planner]
    P --> ST0["[tool-grounded evidence note producer]<br>task_001"]
    ST0 --> D0{"Sim:0.63<br>Ap:0.8"}
    D0 -- "Candidate (not selected)" --> R0(("model.gpt_6_astra.v1"))
```

## 3. Retrieval & Candidate Pool

### task_001

- Revision: graph=`0` | subtask=`task_001` | subtask_revision=`0`
- Candidate pool hash: `d9d10aec546e6a1bfb466d1b16b2c42d64916fa54506b9c9cc272647d0bf88a6`
- Retrieval evidence hash: `d4327d1a241490a0a1c1df60fa4541212513aaf8262bccc4e0c96b975bdef9f0`
- HyDE profile hash: `70f359c7938a5f27d819fd26eabbd0271039e73cb63a482b5565731a0486a944`
- Confidence: calibration=`unconfigured` | quota_coverage=`True` | contract_coverage=`True` | dependency_coverage=`True`
- HyDE accounting reference: `{'operation_id': 'b047f177b0b64d54bfa905b008e93336', 'provider_attempt_id': 'b047f177b0b64d54bfa905b008e93336:1', 'provider_attempt_ids': ['b047f177b0b64d54bfa905b008e93336:1']}`
- Candidate-pool artifact: `candidate_pools/0_task_001_0.json`
- Type quotas (base retrieval is not reduced by dependency closure):
  - Model: quota=`5` | eligible=`16` | base=`5` | final=`6` | shortfall=`0`
  - Tool: quota=`10` | eligible=`138` | base=`10` | final=`11` | shortfall=`0`
  - Skill: quota=`8` | eligible=`154` | base=`8` | final=`8` | shortfall=`0`
  - Agent: quota=`3` | eligible=`20` | base=`3` | final=`5` | shortfall=`0`
  - Resource: quota=`0` | eligible=`0` | base=`0` | final=`0` | shortfall=`0`
  - Device: quota=`0` | eligible=`0` | base=`0` | final=`0` | shortfall=`0`
- Dependency closure: edges=`24` | rejections=`0` | optional_hints=`47`
- Interpretation: these are frozen **candidates**, not resources selected by the Plan Compiler.

## 4. Sealed Executable Plan Compilation

### task_001

- Status: `success`
- Artifact hash: `ec8a0d47351a3ff4f05a35c2bb34ff18fc5eca0d2946a6f956800fd5e8d766df`
- Candidate pool hash: `d9d10aec546e6a1bfb466d1b16b2c42d64916fa54506b9c9cc272647d0bf88a6`
- Compiler input hash: `5f6a77eb1653ea908df49a122290fe88b69ce188a2fb5c889703d85c85795b9c`
- Accounting operation: `plan_compiler:efe2608e382fa9f3a2f143923a47b2968c7d0389d8bee30c2f1a95db7fede988:semantic:1`
- Execution accounting operations: `none`
- Transport attempts: `1`
- Checked Compiler payloads: `0`

## 5. Compatibility Candidate Projection

This compatibility view reports one retrieved candidate for older visualizations; it is not the Plan Compiler's selected resource.

- **task_001** -> candidate_mode=`SEMI_GENERATIVE_MODE` -> candidate=`model.gpt_6_astra.v1`

## 6. Runtime RoutingSession Trace

### task_001

- Policy expected mode: `N/A`
- Actual runtime mode: `N/A`
- Fallback used: `False`
- Execution outcome: status=`structured_failure` | strictness=`balanced` | category=`unknown` | type=`controller_provider_tool_schema_identity_changed` | graph_replan_allowed=`False`
- Attempts:

#### Recovery

- Status: `framework_failure`
- Plan adaptations: `0`
- Full Generation attempts: `0`
- Reused checkpoints: `0`
- Plan artifact hashes: `ec8a0d47351a3ff4f05a35c2bb34ff18fc5eca0d2946a6f956800fd5e8d766df`
- Failure evidence hashes: `none`
- Temporary Tool artifact hashes: `none`
- Evaluator handoff is one-way; evaluator feedback is not returned to recovery.

#### Evaluation and Artifact Lifecycle

- not_run

<!-- SGAR_MODEL_COST_START -->
## Model Cost

- Observed model cost (USD): `$0.171340500000`
- Provider-reported cost complete: `True`
- Pricing catalog: `221b024c487460ca1e991d64d8e64245ab80716970fd7eee4efb7abd749a47aa`
- Tokens: input=`67489`, cache=`0`, output=`7998`
- Budget: mode=`stop_after_limit`, warning=`False`, limit_reached=`False`, overshoot_usd=`0.000000000000`
- Incomplete accounting: missing_usage=`0`, invalid_usage=`0`, no_response=`0`, pending=`0`

| Stage | Attempts | Input | Cache | Output | Observed USD |
| ----- | -------- | ----- | ----- | ------ | ------------ |
| `agent_execution` | 1 | 3478 | 0 | 168 | 0.000361650000 |
| `plan_compiler` | 1 | 54047 | 0 | 4061 | 0.116822250000 |
| `planner_decompose` | 1 | 7725 | 0 | 1511 | 0.026747550000 |
| `retrieval_hyde` | 1 | 2239 | 0 | 2258 | 0.027409050000 |

<!-- SGAR_MODEL_COST_END -->

<!-- SGAR_RESOURCE_EXECUTION_START -->
## Resource Execution

- Protocol: `sgar-execution-events-v1`
- Calls: started=`1`, terminal=`1`
- Complete: `True`
- Artifacts registered: `0`
- Ledger SHA-256: `61c3485157daef52add7b6afb73a16e4138d37655880aa7e73a46ed0784ac223`
- Unmatched calls: `0`

| Status | Count |
| ------ | ----- |
| `success` | 1 |

<!-- SGAR_RESOURCE_EXECUTION_END -->

<!-- SGAR_RECOVERY_START -->
## Recovery

- Protocol: `sgar-recovery-events-v1`
- Complete: `True`
- Terminal operations: `1`
- Adaptation starts: `0`
- Full Generation starts: `0`
- Checkpoint reuse events: `0`
- Temporary Tool generations: `0`
- Observed Recovery model cost (USD): `$0.116822250000`
- Recovery cost complete: `True`
- Ledger SHA-256: `d59c20de0a59e73383d6ae5175c3617896d97674820dd0d335c53569054c784b`
- Host path occurrences: `0`
- Hidden value occurrences: `0`

<!-- SGAR_RECOVERY_END -->

<!-- SGAR_EVALUATION_ARTIFACT_START -->
## Evaluation and Artifact Publication

- Evaluation protocol: `sgar-evaluation-events-v1`
- Initial evaluations: `0`
- Evidence reviews: `0`
- Evaluation operations complete: `True`
- Artifacts staged: `0`
- Artifacts verified: `0`
- Artifacts committed: `0`
- Artifacts quarantined: `0`
- Unmatched evaluation operations: `0`
- Unmatched artifact operations: `0`
- Unmatched Context commits: `0`
- Evaluation ledger SHA-256: `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
- Artifact ledger SHA-256: `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`
- Context ledger SHA-256: `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`

<!-- SGAR_EVALUATION_ARTIFACT_END -->
