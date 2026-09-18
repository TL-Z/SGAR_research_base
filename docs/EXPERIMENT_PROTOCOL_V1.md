# SGAR Benchmark Experiment Protocol v1.0

Status: frozen for admission audits; implementation changes are not implied by this document.

This protocol separates three concerns:

```text
method harness       benchmark adapter       official verifier
```

The method harness remains native whenever it is already a supported implementation. A
benchmark adapter owns task loading, task-facing environment setup, input conversion,
output/artifact mapping, and verifier invocation. It must not change the method's planner,
router, prompt, model-selection, retry, or execution policy.

## 1. Canonical identity

Every trial and every model request must be joinable by:

```text
experiment_id
benchmark_id
benchmark_version
task_id
trial_id
method_id
method_version
harness_version
```

`trial_id` is created before method execution and is propagated to request ledgers,
trajectories, verifier output, failure records, and summaries. A framework-specific run id
may be retained, but cannot replace the canonical identity.

## 2. Canonical trial record

The normalized trial record contains the following groups. Native raw logs remain immutable
and are retained in addition to this record.

### Outcome

```text
official_reward
verified_success
verifier_status
verifier_exit_code
verifier_duration_seconds
termination_reason
failure_class
failure_type
```

`failure_class` must distinguish at least `method_failure`, `verifier_failure`,
`infrastructure_failure`, `provider_failure`, `timeout`, and `invalid_trial`.

### Runtime

```text
trial_start
trial_end
e2e_wall_clock_seconds
environment_setup_seconds
agent_execution_seconds
verifier_seconds
cleanup_seconds
```

The measurement boundary is the runner's trial lifecycle: setup starts before task-facing
environment creation; trial end is after verifier output and cleanup bookkeeping. If a
framework cannot separate a component, it records `null` plus an explanation rather than
silently assigning the time to another component.

### Model request ledger

One row is required for every actual upstream model request, including retries, router,
worker, subagent, planner, profiler, compiler, recovery, and trace-summary requests.

```text
request_id
request_index
role
component
model_id
provider_id
endpoint_id
request_start
request_end
latency_seconds
status
error_type
retry_index
input_tokens
cache_read_tokens
cache_write_tokens
output_tokens
reasoning_tokens
total_tokens
native_cost
native_cost_currency
native_cost_source
```

Unknown token dimensions are `null`, never inferred from a different token field. The
ledger is the primary source for cross-method normalization. A provider/gateway ledger may
be retained as an audit or reconciliation source.

### Cost policy

Native cost is preserved if it passes the method audit for coverage, request binding,
response-usage provenance, retry handling, and pricing scope. It is never discarded merely
because a normalized cost is also computed.

```text
native_cost                 # method-reported, audited when possible
normalized_cost             # recomputed from frozen price snapshot
price_snapshot_id
cost_reconciliation_status
```

The normalized cost is recomputed after the run from model id and input/cache/output token
counts. This permits changing the official or team relay price table without rerunning a
benchmark. Native cost differences are reported, not hidden.

### Reproducibility

```text
method_commit
harness_commit
config_hash
benchmark_manifest_hash
task_package_hash
verifier_hash
environment_image_digest
runtime_version
dependency_lock_hash
model_configuration_hash
timeout_configuration
retry_configuration
seed
concurrency
network_policy_version
proxy_policy_version
raw_trajectory_path
raw_usage_ledger_path
verifier_log_path
```

## 3. Method-specific diagnostics

Native diagnostics are retained without requiring every method to emit the same concepts.
Examples include routing/delegation, worker or subagent counts, selected primitives,
compiler plans, selected resources, recovery/replan events, shell/tool/file actions, and
framework-specific trajectories.

If a diagnostic is normalized, its definition must be explicit. In particular, the
following action classes must not be conflated:

```text
model_generation
deterministic_tool
environment_command
controller_action
verifier
```

## 4. Benchmark contract

For every benchmark, freeze:

```text
dataset id and revision
task manifest and task-list hash
task package hash
instruction bytes
task-specific environment image/Dockerfile
image digest
official tests/verifier
reward extraction semantics
resource limits
network permission
agent timeout
verifier timeout
trial/retry semantics
```

Runner implementations may differ. The benchmark contract may not.

## 5. Environment and network contract

Method framework dependencies are method-owned and may differ. The task-facing environment
must be equivalent for the same benchmark task: image/Dockerfile, mounts, workdir, resource
limits, network permission, tests, verifier, and timeout policy.

Formal experiment commands are launched as:

```bash
sgar-net-run <original-command>
```

The wrapper must be checked at the real child-container boundary for each harness once:

1. proxy variables are inherited;
2. permitted external egress uses the approved proxy;
3. model, localhost, and internal endpoints match `NO_PROXY`;
4. benchmark network restrictions are still enforced;
5. Docker pull behavior is recorded separately because it is daemon-owned.

## 6. Trial semantics and aggregation

The default unit is exactly one task and one trial. Provider retries and method retries are
recorded separately. Hidden complete-task reruns, best-of-N selection, or failed-trial
replacement are not allowed in a Pass@1 result. Pass@k requires predeclared independent
trials.

Primary metrics are official verified success/reward, request count, input/cache/output
tokens, normalized cost, end-to-end wall-clock, and failure rates. Native cost, detailed
trajectories, and method-specific diagnostics remain available for audit and appendix
analysis.

## 7. Admission states

Each method/benchmark pair receives one of:

```text
PASS
PASS_WITH_LOGGING_FIXES
BLOCKED
```

Only logging, identity propagation, path/config mapping, benchmark adapter, result
collection, and endpoint mapping may be fixed during admission. Method algorithms and
execution semantics are out of scope.
