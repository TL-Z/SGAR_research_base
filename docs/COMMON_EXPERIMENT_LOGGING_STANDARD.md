# Benchmark 实验公共日志最低标准

版本：2026-09-18

适用于 SGAR、AOrchestra、Uno-Orchestra、Terminus-2、SWE-Agent、ReAct、Codex 及后续方法。各方法可以保留自己的原生日志格式，但必须能够提供或可靠派生以下信息。未知字段写 `null` 并说明原因，不能用 `0` 代替。

## 1. 实验、方法与 trial 身份

每次完整 task 尝试必须有唯一且可关联的身份：

```text
experiment_id
benchmark_id
benchmark_version
task_id
trial_id
trial_index / pass_index
method_id
method_version / commit
harness_version / commit
run_id / native_session_id
```

`task_id` 不等于 `trial_id`。完整重跑必须生成新的 `trial_id`；模型 retry、agent turn、subagent 和 tool call 都不是新 trial。

## 2. Benchmark 与配置快照

```text
task_package_path
benchmark_manifest_hash
task_package_hash / task_tree_hash
instruction_hash
verifier_hash
config_path + config_hash
model configuration
timeout configuration
retry configuration
seed
concurrency
pass_k
```

模型配置至少包括实际 model/provider/endpoint 标识及影响行为的参数。API key、Authorization header 和完整 secret 不得写入日志。

## 3. 环境与复现信息

```text
environment type
image reference + image digest
Docker/runtime version
CPU / RAM / GPU / storage limits
workdir / mounts
task network permission
proxy_policy_version
effective proxy / NO_PROXY summary
dependency or lock-file hash（可用时）
```

同时记录镜像是否在正式 trial 前已经 ready。正式 trial 中发生的意外 pull/build 必须单独标记为 setup/infrastructure 异常，不能无声计入 agent latency。

## 4. Trial 生命周期与时间

至少记录以下 wall-clock 时间点或可计算的 duration：

```text
trial_start / trial_end
environment_setup_start / end / seconds
agent_start / agent_end / agent_execution_seconds
verifier_start / verifier_end / verifier_seconds
cleanup_start / end / seconds
e2e_wall_clock_seconds
```

统一语义：

- `agent_execution_seconds` 从 task 正式交给方法开始，到 submit、method failure 或 agent timeout 为止；
- verifier 使用独立计时和独立 timeout，不计入 agent latency；
- `e2e_wall_clock_seconds` 可包含 setup、agent、verifier 和 cleanup；
- batch 前的一次性镜像 pull/build 不属于 trial；
- 并行请求 latency 之和不能代替 wall-clock。

无法拆分某一阶段时填 `null + reason`，不能把时间转移到其他阶段。

## 5. 结果与官方 verifier

```text
submitted / submission status
official_reward
verified_success
verifier_ran
verifier_status
verifier_exit_code
verifier_timeout
verifier_stdout_path
verifier_stderr_path
verifier_duration_seconds
termination_reason
```

必须区分官方 reward 为 0、verifier 未运行、verifier 自身失败、环境失败和方法失败。内部 evaluator/critic 的结果可以保留，但不能代替官方 verifier。

## 6. 逐请求模型调用日志

每一次真实发送给模型 provider/relay 的请求，包括 retry，都需要一行或一组可关联事件：

```text
experiment_id / task_id / trial_id
logical_call_id
attempt_id
provider_request_id（可用时）
request_index / retry_index
role / component
model_id / provider_id / endpoint_id
request_start / request_end / latency_seconds
status / error_type
input_tokens
cache_read_tokens
cache_write_tokens
output_tokens
reasoning_tokens
total_tokens
raw_provider_usage
native_cost
```

覆盖所有角色：router、planner、worker、main agent、subagent、summary、compression、recovery、critic 等。开始/完成事件、stream chunk、ATIF step、agent action 都不能重复算成新的 LLM call。

token 字段必须来自真实 provider 或可审计的本地推理统计，并说明 input 是否包含 cache、reasoning 是否已包含在 output。无法观测时填 `null`，不得从其他字段猜测。

## 7. Cost

```text
native_cost
native_cost_currency
native_cost_source
native_cost_scope
cost_status: validated / partial / estimated / unknown
normalized_cost（后处理生成）
price_snapshot_id
```

方法已有 cost 原样保留，但要说明覆盖了哪些模型角色和 retry。统一比较使用原始 token 按冻结价格表重新计算；本地模型费用为 0 不代表 token 或计算量可以不记录。付费工具、embedding 等费用单独记录，不能混入 LLM generation cost。

## 8. Agent、tool 与执行轨迹

至少保存可审计的完整轨迹或事件引用：

```text
event_id / parent_event_id
event_type
component / role
tool or action name + version
start / end / status / error
input summary or hash
output/artifact reference
workspace changes / submitted artifact
```

事件类型至少能区分：

```text
model_generation
deterministic_tool
environment_command
controller_action
verifier
```

方法特有的 route、delegation、primitive、compiler plan、resource selection、replan/recovery 等诊断信息原样保留，不要求所有方法具有相同内部指标。

## 9. 失败、timeout 与重试

```text
failure_class
failure_type
failure_phase
termination_reason
exception type / sanitized message
retry count and retry reason
last successful phase
cleanup status
```

`failure_class` 至少区分：

```text
method_failure
provider_failure
verifier_failure
infrastructure_failure
timeout
invalid_trial
telemetry_failure
```

timeout 应进一步区分 environment、agent/model、tool 和 verifier。失败、取消或 crash 后仍必须保留已经产生的请求、usage、trajectory 和 verifier/异常证据。

## 10. 原始证据与关联路径

每个 trial 至少保留：

```text
raw_trajectory_path
raw_request_ledger_path
trial_result_path
verifier_log_path
failure_log_path
artifact/output path + hash
environment manifest path
```

汇总 CSV/JSON 不能作为唯一证据。所有汇总值应能回溯到 request ledger、trajectory、official verifier 和环境 manifest。

## 11. 日志质量与覆盖率

每个 trial 还需要报告：

```text
telemetry_status: complete / partial / failed
missing_fields
request_usage_coverage
cost_coverage
unmatched_request_count
duplicate_request_count
reconciliation_status
```

日志缺失不能改变方法 reward，也不能将 trial 从实验分母中静默删除。方法结果与 telemetry 完整性是两个独立维度。

## 最低验收条件

一个方法只有满足以下条件，才算日志准入：

1. 任意 trial 都能从结果追溯到 task、配置、方法版本和环境；
2. 所有实际模型角色和 retry 都能落到 request ledger；
3. request ledger、native summary、trajectory 和 cost 能对账；
4. agent、verifier 和 e2e 时间边界明确；
5. official reward=0、方法失败、verifier failure、基础设施失败和 timeout 可以区分；
6. 成功、失败、取消后都保留原始证据；
7. 日志或 converter 不改变模型选择、prompt、tool、retry、调度或终止行为。
