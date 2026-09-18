# Method Telemetry Gap Matrix

Date: 2026-09-18

This is a static implementation audit, not a benchmark result. Percentages below are
planning estimates for the frozen P0 schema and are not success scores.

## 1. Coverage summary

| Method | Native P0 coverage estimate | Evidence confidence | Main gap |
|---|---:|---|---|
| AOrchestra | 75–85% | high: completed native trials and request ledgers exist | normalize setup/verifier timing, environment digest, cost reconciliation, proxy admission |
| Uno-Orchestra | 70–80% | high: pilot JSONL/summary/trajectory artifacts exist | benchmark metadata, canonical trial binding, environment metadata, retry/failure normalization |
| Terminus-2 via Harbor | 70–85% intrinsic | medium: installed implementation inspected, formal local TB trial not bound | real admission trial and exact request/trial reconciliation |
| SWE-Agent via Harbor | 55–70% intrinsic | medium: converter inspected, formal local TB trial not bound | cache/usage completeness and real Harbor evidence |
| SGAR native | 85–95% method-internal telemetry; 35–50% benchmark-facing telemetry | high for native runs, low for TB2.1 | native TB adapter, official verifier, task identity/environment binding |

The ranges express observability of the required dimensions. They do not mean that a method
needs to be rewritten by the corresponding percentage.

## 2. Required P0 dimension matrix

Legend: `Y` = present and usable; `P` = present but needs semantic/coverage audit;
`D` = derivable from raw artifacts; `M` = missing or not yet implemented; `U` = not
verified because no bound formal trial was available.

| Dimension | AOrchestra | Uno | Terminus-2/Harbor | SWE-Agent/Harbor | SGAR native |
|---|---:|---:|---:|---:|---:|
| benchmark/task identity | Y | P | U | U | M |
| trial identity | Y | M/P | U | U | P |
| task manifest/package hash | Y | M/P | U | U | M |
| official reward/success | Y | Y | U | U | M |
| verifier status/exit/log | P | P | U | U | M |
| verifier duration | D | P | U | U | M |
| e2e wall clock | Y | Y | U | U | D |
| setup/agent/verifier time split | M/P | M | U | U | P |
| request count | P/Y | Y | U | U | Y |
| request-level model id/role | Y | P | U | U | Y |
| input/output tokens | Y | Y | U | U | Y |
| cache read/write tokens | P | P | U | U | P |
| reasoning tokens | Y where provider returns | P | U | U | P |
| retry/status/error per request | Y | P | U | U | Y |
| native cost | P | Y | U | U | Y |
| normalized-cost inputs | Y | P | U | U | Y |
| native trajectory/raw logs | Y | Y | U | U | Y |
| environment resource metadata | P/Y | P | U | U | M/P |
| image/runtime digest | M/P | M | U | U | M |
| proxy/NO_PROXY evidence | M | P | U | U | M |
| failure taxonomy | Y | P | U | U | P |

## 3. AOrchestra gaps

### Already present

- frozen TB2.1 identity and manifest hash;
- task id and trial id;
- model, sub-model, summary model and relay endpoint;
- max steps, max attempts, agent timeout and verifier timeout;
- environment CPU, memory, storage, GPU and internet flags when supplied by task config;
- start/end and wall-clock time;
- official reward and success;
- trajectory, command log, verifier logs and `failure.json`;
- request-level JSONL with model, role, request id, input/output/cached/reasoning tokens,
  latency, status, retry index, task/trial identity.

### Still required

1. Reconcile every native aggregate (`LevelResult`, CSV, trajectory totals) against the
   request ledger. A native dollar value is retained only after this scope check.
2. Add or derive setup duration and verifier duration/exit status from executor logs.
3. Capture image digest and Docker/runtime version, not only the task environment TOML.
4. Preserve cache-read/cache-write distinction if the provider supplies it; currently the
   ledger visibly exposes cached input but not a guaranteed write-cache dimension.
5. Add canonical proxy-policy and child-container inheritance evidence.
6. Export a canonical trial record; the CSV alone is not sufficient.

These are observability and conversion changes. MainAgent, SubAgent, delegation, submit,
forced-submit, and verifier semantics must not change.

## 4. Uno-Orchestra gaps

### Already present

- official verifier reward, verifier error/log, and `verifier_ran` in pilot summaries;
- planner attempts, max attempts, delegations, worker models and worker steps;
- per-task trajectory;
- per-task latency;
- router and worker model usage in `verification.jsonl`, including per-model calls,
  prompt/completion tokens and native cost;
- `predictions.jsonl`, `verification.jsonl`, and `summary.json` aggregation;
- Docker task resources and proxy variables in the native executor;
- LiteLLM request callback with model, token counts, duration and request id.

### Still required

1. Change emitted benchmark metadata from `Terminal-Bench-2.0` to the frozen TB2.1 identity.
2. Add canonical `experiment_id` and `trial_id`; task id alone is insufficient for repeated
   runs and callback joins.
3. Record resolved task package/hash and the symlink target, not only the environment variable.
4. Add setup time, verifier duration/exit code, image digest, runtime version and resource
   manifest to each task record.
5. Normalize provider/router/worker retries and classify router, worker, Docker, provider,
   timeout and verifier failures separately.
6. Reconcile `last_usage`, `verification.jsonl`, prediction usage and LiteLLM callbacks.
   Callback request id is an audit key, not a complete task attribution key.
7. Retain native cost, but also export request-level token rows for team price-table
   recomputation.
8. Record proxy policy and actual child-container `NO_PROXY` behavior.

No planner/router/worker/prompt change is required by this list.

## 5. Terminus-2 via Harbor gaps

### Already present in the installed implementation

- Harbor trial timings and exception fields;
- ATIF trajectory and tool/action records;
- main trajectory token metrics;
- subagent trajectory references and subagent input/output/cache/cost metrics;
- native cost aggregation that includes subagent metrics;
- Harbor result-level aggregation for single-step or multi-step trials.

### Required before PASS

1. Run one real method-specific TB2.1 trial under the exact intended Harbor config.
2. Bind canonical experiment/task/trial identity into job config, agent logs, ATIF and result.
3. Confirm that all summarization, question, answer, retry and main-agent requests are
   included exactly once.
4. Compare Harbor's `input_tokens` semantics (input includes cache) with the canonical
   `input_tokens` plus `cache_read/write_tokens` schema.
5. Record image digest, resource limits, proxy/NO_PROXY state and verifier timing.
6. Preserve native Harbor cost while exporting raw token data for normalized cost.

## 6. SWE-Agent via Harbor gaps

### Already present in the installed adapter

- conversion from `.traj` to ATIF;
- model/version/environment metadata when present in `.traj` info;
- parsed thought/action/observation trajectory;
- input/output token and native-cost fields when provided by the trajectory;
- Mini-SWE-Agent usage normalization for chat-completion and Responses API shapes;
- Harbor agent/verifier/environment result infrastructure.

### Required before PASS

1. Determine whether the selected SWE-Agent or Mini-SWE-Agent path is the formal method;
   do not mix their telemetry formats.
2. Run and bind a real TB2.1 trial with canonical identity.
3. Check that cache tokens, retries and failed provider requests are not omitted from the
   `.traj`/ATIF conversion.
4. Reconcile converted ATIF metrics against Harbor result metrics and raw provider usage.
5. Record image/runtime/resource/proxy/verifier metadata and infrastructure failures.
6. Retain native cost if complete, but recompute normalized cost from request-level tokens.

## 7. SGAR native gaps

### Already present

SGAR native runs have unusually rich internal telemetry:

- `model_calls.jsonl` with start/finish events, operation/request ids, stage, model/provider,
  input/output/cached tokens, provider attempts, status and native cost breakdown;
- `run_manifest.json` with run identity, ledger hashes, unmatched-call audits, status and
  source/config identity;
- `cost_summary.json` and pricing snapshot;
- execution timing, resource calls, tool calls, artifact bytes, recovery and evaluation
  summaries;
- planner attempts, retrieval identity, candidate pools, compiler/execution traces and raw
  artifacts.

### Missing at the benchmark boundary

1. Canonical benchmark/task/trial identity and frozen task manifest validation.
2. TB2.1 instruction/task-file to SGAR `TaskInvocation` mapping.
3. Task workspace and artifact/output mapping that the official verifier can consume.
4. Official TB2.1 verifier execution, exit status, reward and verifier logs.
5. Benchmark-facing environment image/resource/network/timeout metadata.
6. Canonical export of native model ledger into the shared trial/request schema.
7. Proxy/NO_PROXY evidence at SGAR-created child containers.
8. Explicit separation of benchmark `invalid_trial`, infrastructure failure and method
   failure.

SGAR does not need a Harbor adapter for the primary path. Its existing native cost and
pricing records should be preserved and audited, then normalized alongside other methods.

## 8. Change-size estimate

| Method | Expected change type | Approximate scope |
|---|---|---|
| AOrchestra | converter/sidecar metadata | small: 5–8 fields plus reconciliation |
| Uno-Orchestra | metadata + trial join + converter | small/medium: 8–12 fields and one canonical collector |
| Terminus-2 | admission wrapper/audit | no method change; one real trial plus Harbor export mapping |
| SWE-Agent | admission wrapper/audit | no method change; one real trial plus usage completeness checks |
| SGAR | new native benchmark adapter/runner | medium/large: task mapping, output mapping, verifier, telemetry export |

None of these estimates authorizes algorithm changes. The only substantial implementation is
SGAR's benchmark-facing runner because that boundary does not currently exist.
