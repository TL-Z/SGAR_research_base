# Method Admission Audit (read-only)

Date: 2026-09-18

This audit checked the local implementations and artifacts without changing method code or
starting a paid benchmark run. It is an implementation handoff: other method owners can
use the per-method action lists independently.

## Executive status

| Method | Current path | Status | Main reason |
|---|---|---|---|
| AOrchestra | native TerminalBench runner | PASS_WITH_LOGGING_FIXES | TB2.1 task package is identical to the canonical local copy; normalize request-level ledger and environment metadata |
| Uno-Orchestra | native `eval_pipeline` + Docker executor | PASS_WITH_LOGGING_FIXES | task tree is a symlink view of the canonical 89-task package; fix version metadata and complete canonical trial/usage summary |
| Terminus-2 | Harbor installed agent | BLOCKED_FOR_ADMISSION | Harbor source exists, but no completed local method-specific TB2.1 trial and no bound experiment manifest were found |
| SWE-Agent | Harbor installed agent | BLOCKED_FOR_ADMISSION | adapter exists, but no completed local method-specific TB2.1 trial and no bound experiment manifest were found |
| SGAR | native `run_pipeline` (not Harbor) | PASS_WITH_ADAPTER_WORK | native logs are rich; TB2.1 task/workspace/output/verifier adapter is not yet implemented |

`BLOCKED_FOR_ADMISSION` means “evidence is missing,” not that the method is invalid.

## 1. Frozen TerminalBench package

The canonical local package is:

```text
/ssd/zhoutianle/sgar-benchmarks/terminalbench21/official_tb21/terminal-bench-2-1
```

The AOrchestra package is:

```text
/ssd/sikai/AOrchestra/benchmark/terminalbench/terminal-bench-2-1
```

Both contain 89 tasks and 946 files. A read-only content hash comparison found identical
task trees:

```text
task-tree-sha256 = ea3745796a4e7711fd0bd82fe93bb15c535fe902710de328a0440c5fa8b6c60e
```

Uno uses:

```text
/ssd/uno-orchestra/uno-tb21
```

Its top-level entries are a two-level symlink view (`task/task -> canonical task`). The
resolved files are also the same 89-task, 946-file package and resolve to the same
task-tree hash. The runner must continue to record the resolved canonical path and hash,
not only the symlink path.

## 2. AOrchestra audit

Evidence:

- entry: `/ssd/sikai/AOrchestra/bench_aorchestra_terminalbench.py`;
- runner: `/ssd/sikai/AOrchestra/aorchestra/runners/terminalbench_runner.py`;
- config: `/ssd/sikai/AOrchestra/config/benchmarks/aorchestra_terminalbench.yaml`;
- task package: `/ssd/sikai/AOrchestra/benchmark/terminalbench/terminal-bench-2-1`.

Observed execution chain:

```text
list_levels
→ TerminalBenchBenchmark.make_env
→ native Docker executor
→ MainAgent / DelegateTaskTool / SubAgentRunner
→ SubmitTool or forced executor.run_tests
→ official test script and reward
→ trajectory + trial_manifest + failure.json + CSV
```

Existing records are strong: trial identity, benchmark manifest hash, config hash, models,
timeouts, environment config, wall clock, official reward, failure class, trajectory,
verifier log path, and usage ledger are present. The usage scope explicitly includes main
agent, subagents, trace summarization, and memory compression requests.

Required handoff actions:

1. Treat the usage ledger as the primary request source and export every row into the
   canonical request schema.
2. Reconcile `LevelResult` and sidecar totals against the ledger; do not assume the native
   `cost` field includes every subagent/summary request until the reconciliation passes.
3. Record cache token dimensions and request status/retry fields when the provider exposes
   them.
4. Add/derive environment image digest, resolved Docker/runtime metadata, verifier exit
   status, and setup/verifier durations.
5. Keep `summary_model` requests in total request/token accounting.
6. Do not change MainAgent/SubAgent semantics or the native verifier.

## 3. Uno-Orchestra audit

Evidence:

- launcher: `/ssd/uno-orchestra/run_tb_eval.sh`;
- pipeline: `/ssd/uno-orchestra/Uno-Orchestra/eval_pipeline/run.py`;
- benchmark: `/ssd/uno-orchestra/Uno-Orchestra/eval_pipeline/benchmarks/terminalbench.py`;
- executor: `/ssd/uno-orchestra/Uno-Orchestra/eval_pipeline/executors/docker_executor.py`;
- task symlink tree: `/ssd/uno-orchestra/uno-tb21`.

Observed chain:

```text
TERMINAL_BENCH_TASKS_DIR
→ eval_pipeline --bench terminalbench --interactive
→ planner/router/worker
→ Uno Docker executor
→ official tests/test.sh
→ verification.jsonl + predictions.jsonl + summary.json
```

Native records include reward, verifier error/log, route count, routed models/backends,
planner attempts, delegations, worker steps, input/output/total tokens, task cost, and
pass@k. LiteLLM callback records per-request model, tokens, duration, and request id.

Required handoff actions:

1. Correct the benchmark metadata that currently returns `Terminal-Bench-2.0`; formal runs
   must emit the frozen `terminal-bench/terminal-bench-2-1` identity.
2. Record resolved task path and canonical task-tree hash, not only
   `TERMINAL_BENCH_TASKS_DIR`.
3. Join predictions, verification, `last_usage`, and callback records by canonical
   `experiment_id/task_id/trial_id`. The current callback request id is not sufficient by
   itself for task attribution.
4. Export router and worker requests together; confirm that no direct API path bypasses the
   team relay.
5. Add trial start/end, setup time, verifier duration, environment digest, resource limits,
   retry/failure class, and proxy policy metadata.
6. Preserve native planner/router/worker behavior.

## 4. Harbor / Terminus-2 / SWE-Agent audit

The installed Harbor environment is:

```text
/ssd/zhoutianle/envs/rare-harbor
```

Harbor's trial result model records environment/agent/verifier timings, exception data,
reward, and agent contexts. `compute_token_cost_totals()` aggregates input, cache, output,
and native cost from either single-step or multi-step agent results. ATIF trajectories can
also contain final metrics and tool/action steps.

The installed Terminus-2 implementation explicitly records main and subagent trajectory
segments, input/output/cache token counts and native cost. The installed SWE-Agent adapter
converts `.traj` records and preserves trajectory actions, model metadata, input/output
tokens and native cost. Mini-SWE-Agent has a separate converter with support for both chat
completion and Responses API usage shapes.

Admission is nevertheless blocked until a real method-specific run proves:

- task id, benchmark manifest hash, trial id, and method version are bound through Harbor;
- all main/subagent requests are included exactly once;
- Harbor token semantics (`input` includes cache, with cache separately recorded) are mapped
  to the canonical schema;
- native `.traj`/ATIF and Harbor `result.json` agree on usage and timing;
- environment image digest, resource limits, proxy policy, and verifier status are present;
- environment-build failures are classified as infrastructure failures.

The existing Harbor SWE-bench artifact demonstrates that Harbor captures a Docker build
exception and null verifier result, but it is not a successful method admission trial.

## 5. SGAR native audit

SGAR remains native. The source entrypoint is:

```text
/home/zhoutianle/Projects/SGAR_research_base/sgar_mvp/main.py:run_pipeline
```

Native output already includes `run_manifest.json`, `model_calls.jsonl`,
`cost_summary.json`, `model_pricing_snapshot.json`, `execution_summary.json`,
`planner_attempts.jsonl`, `recovery_events.jsonl`, artifact summaries, retrieval/runtime
identity, evaluation summaries, and `trace.jsonl`.

Required native benchmark work is limited to an outer adapter/runner:

- canonical TB task loader and manifest validation;
- task instruction/files to `TaskInvocation` mapping;
- task workspace and artifact/output mapping;
- official verifier invocation and result normalization;
- trial identity propagation into native output;
- request ledger normalization and native-cost preservation;
- failure/environment/proxy metadata.

The existing Harbor integration reports `TB2.1_OFFICIAL_TASK_RUNS = 0` and is not used as
the SGAR primary path.

## 6. Cross-method admission checklist

Before a method is allowed into pilot runs, check:

```text
[ ] frozen task package and verifier hash
[ ] instruction bytes unchanged
[ ] task-facing image/resource/timeout parity
[ ] one task = one trial semantics
[ ] canonical trial_id propagation
[ ] request-level model usage coverage
[ ] native cost scope audited, if present
[ ] normalized token fields recoverable
[ ] official verifier result captured
[ ] method/infra/provider/verifier failures separated
[ ] raw trajectory and logs retained
[ ] real child-container proxy/NO_PROXY check
[ ] no algorithm/prompt/retry semantics changed
```
