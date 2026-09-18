#
## 任务目标

我们正在为 SGAR 和各个 baseline 建立统一的 benchmark 实验记录规范。这次不是要重写方法，也不是要求所有方法都使用 Harbor，而是要确认每个方法已有的原始记录是否真实、完整、可追溯，并补齐确实缺失的日志或外层适配。

每个方法继续保留自己的原生执行逻辑。需要统一的是 task/verifier 版本、实验身份、记录语义、环境约束和最终汇总方式。

## 请按以下顺序完成

### 第一步：只读了解当前实现

请从当前实际使用的代码、配置和已有运行结果出发，梳理一个 task 的完整调用链：task 输入、方法执行、模型和工具调用、环境/容器、最终输出、官方 verifier（这个本地应该是有对应的部署了，看看对应方法里是不是用的本地的verifier，如果不是可能切换成用本地的，否则可能会存在依赖不全的问题，本地已经验证可用了）、结果和日志。

重点确认：

- 实际使用的 repo、commit、环境、配置和模型/策略版本；
- benchmark task 从哪里加载，使用的具体版本和任务包；
- task-facing environment、资源限制和 timeout 如何生效；
- 官方 verifier（本地） 是否真正运行；
- 每次模型请求、subagent/worker、retry 是否都有记录；
- reward、成功、失败、timeout 和环境错误能否区分；
- native cost 从哪里来，覆盖哪些请求；
- 一个已有 trial 的日志能否互相对应。

这一阶段只读检查，不要修改代码，不要跑全量 benchmark。已有字段先核对真实性和统计范围，不要重复实现。

### 第二步：对照统一标准找差距

请逐项核查以下信息：

- experiment/task/trial identity；
- benchmark、task package 和 verifier 版本；
- official reward、verifier status、exit code 和 verifier 时间；
- trial 起止时间和 wall-clock；
- model、role、request id、状态和 retry；
- input/cache/output token；
- native cost 及其覆盖范围；
- image、CPU/RAM/GPU、timeout、network/proxy；
- failure 分类；
- trajectory、request ledger 和 verifier logs。

每项标注为：已经有、可以从原始日志派生、当前缺失、或当前无法确认。

请把问题分成两类：

1. 记录问题：可以通过日志、sidecar、collector 或 converter 解决；
2. 执行语义问题：例如 timeout、verifier、容器生命周期、retry 或资源限制不一致，需要单独提出，不能假装只是加日志。

### 第三步：提出最小修改方案

针对每个缺口说明：修改哪个文件或函数、增加什么信息、在哪个位置采集、怎样关联到 task/trial、怎样避免重复统计，以及如何证明方法行为没有变化。

原生日志可以继续保持原格式，不要为了统一而重写成同一种 CSV。方法已有的 native cost 可以保留，但必须同时保留原始 token usage，后续由我们统一重新计价。不要因为统一计价而修改方法自己的预算、路由或终止策略。

### 第四步：实施后做小规模验收

方案确认后再实施日志或 adapter 修改。先做一个固定 smoke task，必要时增加一到三个固定 task，不要直接跑全量。

验收需要对账：模型请求、token、native cost、trajectory、verifier reward、失败/timeout/retry 和 cleanup。重点证明记录补齐没有改变原方法的调用顺序、模型选择、工具行为和终止逻辑。

## 统一参考材料

请按以下顺序阅读：

1. 统一实验标准：[实验协议 v1.0](/home/zhoutianle/Projects/SGAR_research_base/docs/EXPERIMENT_PROTOCOL_V1.md)。
2. 当前实现线索：[方法审计报告](/home/zhoutianle/Projects/SGAR_research_base/docs/METHOD_ADMISSION_AUDIT_20260918.md)。
3. 缺口参考：[Telemetry 缺口矩阵](/home/zhoutianle/Projects/SGAR_research_base/docs/METHOD_TELEMETRY_GAP_MATRIX_20260918.md)。
4. 网络约束：[SGAR 实验代理统一用法 PDF](</root/.codex/attachments/b2fb3d5d-0eb0-4037-8c33-2d4b88e8d42b/SGAR 实验代理统一用法.pdf>)。
5. 科学设计参考：[SGAR 实验设计 PDF](</root/.codex/attachments/14122c5a-1401-411d-ad94-c1110ec9870c/SGAR 实验设计 (1).pdf>)。

正式运行命令统一经过 sgar-net-run；代理不能改变 benchmark 原本的联网权限。两份 PDF 分别用于执行网络约束和实验设计理解，不是方法算法修改指令。

## 各方法的重点

### AOrchestra

重点核对 MainAgent、SubAgent、summary 和 memory compression 请求是否都进入同一个 request ledger；对账 ledger、trajectory、LevelResult、CSV 和 verifier 结果；确认 native cost 是否覆盖全部角色；补充必要的 verifier timing、环境 digest 和代理证据。

继续使用 AOrchestra 自己的 TerminalBench runner，不修改 MainAgent、SubAgent、delegation 或 verifier。

参考：

- /ssd/sikai/AOrchestra/bench_aorchestra_terminalbench.py
- /ssd/sikai/AOrchestra/aorchestra/runners/terminalbench_runner.py
- /ssd/sikai/AOrchestra/base/engine/usage_ledger.py
- /ssd/sikai/AOrchestra/benchmark/terminalbench/docker_executor.py

### Uno-Orchestra

重点确认正式使用的 router、policy/checkpoint 和 worker pool；确认实际使用 TB2.1 task package；修正 benchmark metadata；把 router、worker 的请求、token、latency、retry、trajectory 和 verifier 结果绑定到同一个 trial。

继续使用 Uno 自己的 harness，不修改 router、worker、prompt 或 primitive。

参考：

- /ssd/uno-orchestra/run_tb_eval.sh
- /ssd/uno-orchestra/Uno-Orchestra/eval_pipeline/run.py
- /ssd/uno-orchestra/Uno-Orchestra/eval_pipeline/benchmarks/terminalbench.py
- /ssd/uno-orchestra/Uno-Orchestra/eval_pipeline/executors/docker_executor.py
- /ssd/uno-orchestra/uno_cost_logger.py

### Terminus-2

先确认实际使用的 Harbor 配置和真实 trial。重点核对 main agent、summarization、subagent 和 retry 是否全部记录，以及 Harbor 的 input/cache/output 语义如何转换到统一标准。

在没有真实 trial 证据前，只能标记为待准入，不能仅凭 Harbor 支持 ATIF 就认为记录完整。不要修改 Terminus 的 agent、模型或执行策略。

参考 /ssd/zhoutianle/envs/rare-harbor/lib/python3.14/site-packages/harbor/agents/terminus_2/ 以及 Harbor 的 TrialResult、UsageInfo 和 trajectory 定义。

### SWE-Agent

先确认正式使用的是 SWE-Agent 还是 Mini-SWE-Agent，不要混用两者结果。重点核对原生 trajectory、model usage、ATIF 和 Harbor result，尤其是 cache、retry、失败请求和时间字段。

不要修改 action parser、prompt、历史处理器或 agent policy。缺失信息优先通过原生日志或外层 collector 补齐。

参考 Harbor 的 installed/swe_agent.py、installed/mini_swe_agent.py、factory.py 和 TrialResult。

### SGAR

SGAR 不走 Harbor，保留完整 native pipeline。SGAR 需要实现的是 benchmark 外层 adapter/runner：

TB task → SGAR native invocation → SGAR 原生 Planner/Profiler/Retrieval/Compiler/Runtime → 官方 verifier → 统一 trial record。

需要处理 task 输入、workspace/artifact 映射、官方 verifier、trial identity、native telemetry 导出和环境记录；不要把 TerminalBench 分支写进 Planner、Compiler 或 Runtime。

参考：[SGAR native runner 方案](/home/zhoutianle/Projects/SGAR_research_base/docs/SGAR_NATIVE_BENCHMARK_RUNNER_PLAN.md)、/home/zhoutianle/Projects/SGAR_research_base/sgar_mvp/main.py，以及 SGAR TerminalBench 的 STATUS.md 和 round2_integration_design.md。

## 最终交付

每位负责人最后提交五项内容：

1. 一份审计报告：当前实际调用链、已有记录和主要差距；
2. 一份字段映射表：统一指标对应的原始文件、字段、统计范围、单位和派生方式；
3. 一份最小修改方案：文件、函数、采集点、预计影响；
4. 一份脱敏 evidence：已有 trial 的 result、usage、trajectory 和 verifier 记录；没有真实 trial 要明确说明；
5. 一份验收计划：smoke task、命令、预期日志和对账方法。

## 完成标准

只有同时满足以下条件，才认为方法完成本轮适配：

- benchmark/task/verifier 版本明确且一致；
- task、trial、request 可以互相追溯；
- 官方 verifier 的 reward 和状态被真实记录；
- 所有实际模型角色的请求和 token usage 都能统计；
- native cost 的来源和覆盖范围清楚；
- failure、timeout、retry 和环境错误可以区分；
- trajectory、usage ledger 和 verifier logs 保留；
- environment、resource、timeout 和 proxy 状态可复现；
- smoke 运行证明没有改变原方法行为。

完成后再由主实验工作线统一转换结果、重新计算 cost，并决定是否进入 20–30 task pilot。
