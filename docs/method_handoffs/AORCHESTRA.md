# AOrchestra 负责人任务书

请先阅读 [共同任务说明](/home/zhoutianle/Projects/SGAR_research_base/docs/method_handoffs/COMMON_TASK.md)。AOrchestra 继续使用 native MainAgent/SubAgent harness；本轮不改 agent、delegation、prompt 或模型策略。

## 需要检查

1. 核实正式入口、TB2.1 task 路径、配置/源码版本和实际模型。不要把旧 pilot 与正式配置混在一起。
2. 沿实际发送链检查 main、subagent、delegate trace-summary、memory-compression 等请求是否都进入同一套 ledger，并能关联到 `trial_id`。
3. 对一个已有 trial 对账：ledger ↔ trajectory ↔ LevelResult/CSV ↔ 官方 verifier。核对请求数、input/cache/output/reasoning token、retry 和 native cost 是否漏记或重复。
4. 检查失败、取消、timeout、main 创建失败和落盘失败是否留下终态记录；未知 usage 必须为 `null`，不能写 0。
5. 核对 agent、verifier、e2e 时间字段及 verifier exit/status。image digest、配置 hash、代理状态作为复现元数据记录，不改变执行逻辑。
6. 确认正式启动可由统一外层 wrapper 运行，task container 内的普通网络和 model/internal endpoint 的 bypass 语义可被证明；镜像预热由基建负责人完成。

## 最小交付

- 一份多角色 request ledger 的逐项合计示例；
- native cost 的覆盖范围和未覆盖角色清单；
- 一个 trial 的完整字段映射和失败/timeout 分支说明；
- 需要修改时，只提交 ledger/context 传递、sidecar 或 converter 方案，不改 MainAgent/SubAgent 行为；
- 一个固定 smoke 的对账结果。

## 参考位置

- `/ssd/sikai/AOrchestra/bench_aorchestra_terminalbench.py`
- `/ssd/sikai/AOrchestra/aorchestra/runners/terminalbench_runner.py`
- `/ssd/sikai/AOrchestra/base/engine/usage_ledger.py`
- `/ssd/sikai/AOrchestra/base/engine/async_llm.py`
- `/ssd/sikai/AOrchestra/benchmark/terminalbench/docker_executor.py`
