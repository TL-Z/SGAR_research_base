# SGAR Native Benchmark Runner Plan

Status: design only; no implementation or benchmark run is authorized by this document.

SGAR will not use Harbor for the primary benchmark path. The runner follows the external
shape of AOrchestra and Uno while preserving the complete native SGAR pipeline.

## 1. Boundary

```text
TB task package
  → TB adapter: load and validate task
  → canonical SGAR invocation
  → native SGAR run_pipeline
  → native artifact/output publication
  → TB official verifier
  → canonical trial result
```

The adapter may translate paths, input schemas, output schemas, and verifier results. It may
not alter Planner, Profiler, retrieval, Compiler, lowering, DAG orchestration, executor
selection, model selection, retry, recovery, or evaluator policy.

## 2. Proposed module layout

The exact filenames can be chosen during implementation, but responsibilities should remain
separate:

```text
sgar_mvp/benchmarks/common/
  trial_identity.py       # experiment/task/trial IDs and immutable manifest
  telemetry_export.py     # native logs → canonical request/trial records
  environment_manifest.py # image/resources/network/proxy metadata

sgar_mvp/benchmarks/terminalbench21/
  manifest.py             # frozen package and task hash validation
  task_adapter.py         # task.toml/instruction/files → SGAR invocation
  output_adapter.py       # native artifacts → task workspace/output contract
  verifier.py             # official tests/test.sh execution and classification
  runner.py               # one-task and batch lifecycle
```

Do not place TerminalBench-specific branches inside `planner.py`, `orchestrator.py`,
`plan_compiler.py`, or native executor selection.

## 3. Frozen input contract

The first implementation target is the canonical local TB2.1 package:

```text
/ssd/zhoutianle/sgar-benchmarks/terminalbench21/official_tb21/terminal-bench-2-1
```

The runner must validate before a model call:

```text
benchmark_id = terminal-bench/terminal-bench-2-1
task_count = 89
task_tree_sha256 = ea3745796a4e7711fd0bd82fe93bb15c535fe902710de328a0440c5fa8b6c60e
```

The exact task package can later be relocated, but its manifest hash must remain the source
of truth. `instruction.md`, `task.toml`, environment files, tests, and auxiliary assets are
read-only benchmark inputs.

## 4. SGAR invocation mapping

For each task, create a canonical invocation containing:

```text
experiment_id
benchmark_id/version
task_id
trial_id
instruction_text
task_workspace
task_input_files
expected_output_contract
official_verifier_contract
method_config_path/hash
```

The invocation must map task paths into the native SGAR task/artifact abstraction before
calling `run_pipeline`. It must not create a fixed plan or handwritten candidate. Any
unsupported task contract must fail admission before a model request and be classified as
`invalid_trial`, not as a method failure.

## 5. Output and verifier mapping

After `run_pipeline` returns:

1. identify the native committed artifact/output;
2. validate that it is within the task's allowed workspace/output boundary;
3. copy or expose it to the exact path expected by the official TB verifier;
4. run the official `tests/test.sh` with the task's declared verifier timeout;
5. capture stdout, stderr, exit code, duration, and reward;
6. write the canonical result and preserve all native SGAR artifacts.

The internal SGAR evaluator remains diagnostic. It cannot replace the official benchmark
verifier. A disabled internal evaluator must be recorded as configuration metadata, not as a
missing benchmark result.

## 6. Telemetry integration

The runner creates `trial_id` before invoking SGAR and passes it through the execution
context or an out-of-band immutable run manifest. Native files remain unchanged in meaning.
The exporter derives:

```text
model_calls.jsonl → canonical request ledger
cost_summary.json → native cost/audit fields
run_manifest.json → identity/status/config fields
execution_summary.json → execution timing/status
trace.jsonl → native diagnostics and trajectory reference
```

The exporter must check that model-call rows are neither missing nor double counted. If
native cost is present and passes the cost audit, preserve it alongside normalized cost
computed later from request tokens.

Telemetry must be observational: no synchronous network call, retry, prompt mutation,
randomness, or scheduling decision may be introduced into the SGAR execution path. Export
failure must not change the method result; it must mark the trial as incomplete for
admission.

## 7. Environment and proxy handling

The formal entrypoint is launched through:

```bash
sgar-net-run <sgar-native-runner-command>
```

The runner must record the proxy policy version and the task-facing network permission. It
must not enable network for tasks that declare it disabled. SGAR's own executors must be
checked at the actual child-container boundary for proxy inheritance and `NO_PROXY` behavior.
No global Docker daemon configuration change is part of this plan.

## 8. Execution phases

### Phase A — static adapter contract

- validate all 89 tasks without model calls;
- validate instruction/task/test paths;
- validate resource fields and verifier timeout;
- validate output/artifact mapping rules;
- produce manifest and adapter report.

### Phase B — deterministic verifier controls

- run an oracle/positive output and a negative output through the official verifier;
- verify reward, exit status, timeout, and log classification;
- verify no task input or tests are modified.

### Phase C — native SGAR smoke

- run one simple task with model calls;
- reconcile native model ledger, trial record, and verifier result;
- verify cleanup and child-container boundaries.

### Phase D — parity pilot

- use a fixed, stratified 5-task subset shared by all methods;
- then expand to 20–30 tasks;
- freeze configuration before full benchmark execution.

## 9. Acceptance gates

The SGAR runner is admitted only when:

```text
[ ] all selected tasks resolve to the frozen manifest
[ ] official instruction/tests/verifier are unchanged
[ ] native SGAR output maps to the official task workspace
[ ] positive and negative verifier controls behave correctly
[ ] one trial produces complete identity/usage/outcome records
[ ] native cost is preserved and token-based normalization is possible
[ ] environment/resource/timeout metadata is complete
[ ] proxy/NO_PROXY child-container check passes
[ ] no native stage or execution policy was changed
[ ] cleanup leaves no unintended containers or files
```

Only after these gates pass may the runner enter the shared 20–30-task pilot.

## 10. Future benchmark adapters

GAIA and SWE-bench Verified should reuse the common identity, telemetry, environment
manifest, and result schema. They receive separate task/output/verifier adapters. The SGAR
native pipeline and telemetry exporter are not rewritten for each benchmark.
