# Terminal-Bench 2.1 完整运行前总清单

版本：2026-09-18

本文是当前进入 TB2.1 pilot 之前的唯一总览。方法负责人只处理自己的方法和日志；公共基建、镜像预热、统一代理以及 SGAR adapter 由主实验线负责。

## 结论先行

目前还不能直接开始正式 batch。原因不是四个 baseline 的核心算法不能运行，而是：

1. TB2.1 任务包已经基本统一，但环境镜像尚未全部 warm；当前检查快照为 89 个 task 中 29 个原始 image 在本机、60 个缺失。
2. AOrchestra 和 Uno 都支持本地 cache，但缺镜像时会在 runner 内 lazy pull/build，必须先做公共 preflight，正式 trial 禁止隐式准备环境。
3. AOrchestra 的容器内代理继承还需要实际证明；Uno 的普通入口已有 wrapper，但 SFT 入口尚未统一经过 `sgar-net-run`。
4. Terminus-2 和 SWE-Agent 的 Harbor adapter 已存在，但还没有绑定到正式配置的真实 TB2.1 trial，暂不能算准入通过。
5. SGAR 原生执行和日志已经存在，但 TB2.1 外层 adapter/runner 尚未完成。
6. ReAct 和 Codex 尚未在当前总览中完成正式 runner/Harbor 配置归属确认，不能直接按“日志已齐”视为准入。

统一标准见 [实验协议 v1.0](/home/zhoutianle/Projects/SGAR_research_base/docs/EXPERIMENT_PROTOCOL_V1.md)。正式 verifier 使用原始官方 `tests/test.sh` 和 online 语义；`-vfready` 只用于调试，不进入正式结果。

## 公共前置条件（主实验线负责）

这些条件完成前，各方法不进入 20–30 task pilot：

- 固定 canonical TB2.1 task package、89 个 task、manifest/tree hash 和 verifier 版本；
- 为全部 task image、Dockerfile build 结果及 Harbor sidecar 做统一 preflight；
- 记录 image reference、digest、task/package hash；缺失镜像在正式运行前失败，不在 trial 内自动 pull/build；
- 统一从 `sgar-net-run <original-command>` 启动方法；确认宿主机、Docker build/pull、task container 和 verifier 的代理边界；
- 模型 API 直连并进入 `NO_PROXY`，其他允许的 online 行为走代理；
- 统一 trial record：结果、verifier、时间、请求/token、native cost、失败分类、trajectory 和环境元数据；
- 为每个方法先准备一个固定 smoke，再扩展到 5 个 task，最后才做 20–30 task pilot。

## 各方法现状与 todo

### AOrchestra

**现状**：native TerminalBench runner 可运行；task tree 与 canonical TB2.1 一致；已有 trial manifest、trajectory、failure、CSV 和 request ledger，内部日志基础最好。Docker executor 能复用本地 image，但缺失时会 pull/build。默认配置没有充分证明 task container 内 proxy 已生效。

**准入状态**：`PASS_WITH_LOGGING_FIXES`，但须完成公共 image/proxy gate 后才能 smoke。

**负责人 todo**：

1. 固定正式 config、模型和 runner 版本，排除旧 pilot；
2. 将 main、subagent、delegate summary、memory compression 的每个真实请求映射到 canonical `trial_id`；
3. 对账 ledger、trajectory、LevelResult/CSV、verifier，确认请求、token、retry、native cost 无漏计/重复；
4. 补齐或派生 verifier exit/duration、setup/agent 时间、image/runtime/config 元数据；
5. 先确认当前 timeout 是否包住 verifier；目标是把 `agent_execution_seconds` 与 `verifier_seconds` 分开，不能把 verifier 时间混入 agent latency。若要改变 timeout 的实际覆盖范围，必须单独记录为执行语义变更并做 smoke；
6. 用一个 smoke 证明失败、timeout、取消和落盘失败都有终态记录，并提交容器内 proxy/NO_PROXY 证据。

**不做**：不改 MainAgent、SubAgent、delegation、prompt、模型或 verifier 逻辑。

### Uno-Orchestra

**现状**：native `eval_pipeline` 和 Docker executor 可运行；`uno-tb21` 解析后与 canonical 89-task package 一致；已有 predictions、verification、trajectory、summary 和 callback。当前 benchmark metadata 仍可能写成 TB2.0，canonical trial 关联不完整。普通 `run_tb_eval.sh` 已经过 wrapper，`run_tb_unosft.sh` 当前未经过 wrapper。Docker executor 同样是缺失时 lazy pull/build。

**准入状态**：`PASS_WITH_LOGGING_FIXES`，SFT 入口和 metadata 修正前不能 smoke 通过。

**负责人 todo**：

1. 固定正式 router、policy/checkpoint、worker pool 和 launcher，区分 base-model pilot 与 SFT/native policy；
2. 将 benchmark metadata、resolved task path 和 tree hash 改为/记录 frozen TB2.1 identity；
3. 把 router、worker、callback、retry/fallback、predictions、verification 绑定到 `experiment_id/task_id/trial_id`；
4. 对账 request token/cache/latency/native cost、route count、verifier reward 和 failure class；
5. 确认 SFT launcher 也通过统一外层 wrapper，正式路径使用原始官方 verifier，不使用 `-vfready`；
6. 用一个 smoke 验证 verifier failure、Docker failure、timeout、resume/pass-k 不会误覆盖结果。

**不做**：不改 learned policy、router prompt、worker/primitive 行为或训练结果。

### Terminus-2（Harbor）

**现状**：Harbor/Terminus-2 安装和 adapter 存在；Harbor 支持本地 image/cache，但缺失环境仍可能 compose build/pull。当前没有可绑定到正式配置的本地 TB2.1 method trial，因此所有日志结论仍是实现级判断。

**准入状态**：`BLOCKED`（证据缺失，不代表方法无效）。

**负责人 todo**：

1. 提交实际 Harbor 版本/source hash、agent/job config、模型 endpoint 和正式 runner；
2. 在公共 preflight 完成后跑 1 个真实 TB2.1 smoke；
3. 对照 raw model log、ATIF、Harbor `TrialResult`，核对 main、summary、subagent、continuation、retry 的真实 request/attempt；
4. 明确 input/cache/output/reasoning 和 native cost 的语义，去除 ATIF step 或复制 context 导致的重复统计；
5. 补齐 canonical identity、image/resource/proxy/verifier 元数据和 infrastructure failure 分类。

**不做**：不原地修改共享 Harbor site-packages，不改 Terminus agent、prompt、模型或 retry 策略。

### SWE-Agent（Harbor）

**现状**：Harbor 的 SWE-Agent adapter 存在，但必须先确认正式方法不是 Mini-SWE-Agent；当前同样没有绑定正式配置的 TB2.1 trial。原生 `.traj` 到 ATIF/result 的转换可复用，但转换生成的 timestamp 不能当作真实请求时间。

**准入状态**：`BLOCKED`（方法身份和真实证据均待确认）。

**负责人 todo**：

1. 固定正式 SWE-Agent/Mini-SWE-Agent 身份、版本、配置、模型和 runner，禁止混用结果；
2. 在公共 preflight 完成后跑 1 个真实 TB2.1 smoke；
3. 对账 `.traj`、`info/model_stats`、ATIF、Harbor result 和 raw provider usage；
4. 核查 cache、retry、失败/cancelled request、request ID、native cost 和 latency 是否真实可追溯；
5. 补齐 trial/session/trajectory、verifier、image/resource/proxy 和 failure 元数据。

**不做**：不改 action parser、prompt、history、retry 或 agent policy；缺字段优先用原生日志或外层 converter 补齐。

### SGAR（主实验线负责）

**现状**：SGAR native pipeline 的 model ledger、manifest、cost、execution、recovery 和 trace 日志较完整；但目前没有正式 TB2.1 native adapter，已有 Harbor 路径也不是 SGAR 主路径。

**准入状态**：`PASS_WITH_ADAPTER_WORK`。

**todo**：完成 task/ instruction/workspace/output 映射、official verifier、trial identity、native telemetry export、环境/代理记录和失败分类；保持 Planner、Compiler、Runtime、executor selection 和 evaluator policy 不变。详细方案见 [SGAR Native Runner Plan](/home/zhoutianle/Projects/SGAR_research_base/docs/SGAR_NATIVE_BENCHMARK_RUNNER_PLAN.md)。

### ReAct

**现状**：此前没有纳入统一准入表；当前首先要确认正式实现、runner、环境类型（native 或 Harbor）、模型配置和已有运行证据。

**准入状态**：`BLOCKED_UNTIL_PATH_CONFIRMED`。

**todo**：

1. 确认正式 ReAct 实现、代码版本、launcher、TB2.1 task path 和模型配置；
2. 检查端到端链路：task input → agent/tool loop → workspace/output → 官方 `tests/test.sh` → unified result；
3. 若为 native runner，补齐 task/trial identity、verifier、native telemetry export；若为 Harbor，另外完成 Harbor config、ATIF/result、image/cache 和 proxy 检查；
4. 对齐请求/token、agent/verifier/e2e 时间、failure class、trajectory 和环境元数据；
5. 用一个固定 smoke 证明日志完整且没有改变 ReAct loop、tool 或 retry 行为。

### Codex

**现状**：本地 Harbor 安装包含 Codex agent 入口，但尚未确认它是否就是正式实验路径，也没有在当前总览中绑定正式 TB2.1 trial。

**准入状态**：`BLOCKED_UNTIL_PATH_CONFIRMED`。

**todo**：

1. 确认正式 Codex agent、Harbor 版本/source hash、job/agent config、模型 endpoint 和 launcher；
2. 检查 Harbor result、ATIF/trajectory、raw usage 和 verifier 是否都绑定 canonical `trial_id`；
3. 核对真实 request/attempt、input/cache/output/reasoning token、retry、native cost、latency 和失败状态；
4. 检查本地 image/cache、task container proxy、model API `NO_PROXY` 和官方 verifier；
5. 用一个固定 smoke 对账 agent、verifier、e2e 时间和完整日志，不修改 Codex agent/prompt/retry 逻辑。

## 最终推进顺序

```text
公共 task/verifier/image/proxy preflight
        ↓
确认 ReAct/Codex 正式 runner 归属
        ↓
各 baseline 完成日志/入口检查
        ↓
每个方法 1-task smoke
        ↓
每个方法 5-task 对账
        ↓
SGAR adapter smoke + 全方法固定 pilot
        ↓
20–30 task TB2.1 pilot
```

只有当一个方法同时具备“真实 smoke、官方 verifier 结果、请求/token 可对账、失败可分类、环境和代理证据”时，才从 `BLOCKED` 或 `PASS_WITH_LOGGING_FIXES` 变成可进入 pilot 的准入状态。
