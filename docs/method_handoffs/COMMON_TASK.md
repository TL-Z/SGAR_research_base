# Baseline 方法负责人共同任务

版本：2026-09-18

本轮只处理各自方法的准入检查、日志核对和必要的最小记录补齐。方法的 planner/router、prompt、模型选择、retry 策略、tool 行为和 verifier 语义都不得改变。基建、公共镜像预热、统一代理服务和 SGAR benchmark adapter 由主实验线负责；方法负责人只需检查自己的 runner 是否兼容，并报告具体阻塞。

## 必查内容

1. 实际使用的 repo、commit、解释器、配置、模型/策略/checkpoint 和 runner 命令。
2. 任务包、task ID、verifier 和方法输出是否来自本地固定的 TB2.1 版本。
3. `experiment_id / task_id / trial_id / method_id` 是否能贯穿请求日志、trajectory、结果和 verifier。
4. 每个真实模型请求（包括 router、worker、subagent、summary、retry）是否有 model、role、status、时间、token usage 和 request/attempt 关联。
5. 成功、官方 reward、verifier 状态、timeout、方法失败、基础设施失败能否区分。
6. `agent`、`verifier`、`e2e` 时间边界是否有明确来源；不能把未知值填成 0。
7. native cost 是否说明来源和覆盖范围；原始 token 必须保留，后续统一重算 cost。
8. trajectory、usage/request ledger、verifier stdout/stderr 和异常在失败/取消后是否仍可追溯。

## 允许的改动

只做最小的日志、ID 传递、结果映射、外层 wrapper 或 converter 调整。已有原生日志先保留，不为了格式统一重写方法内部日志。若发现 timeout、容器生命周期、verifier 或 retry 语义问题，单独列为执行语义问题，不伪装成日志补丁。

## 交付物

在方法自己的工作目录提交：

- `METHOD_AUDIT.md`：当前调用链、已有记录和缺口；
- `FIELD_MAPPING.csv`：统一字段对应的原始文件/字段、单位、派生方式和状态；
- `MINIMAL_PATCH_PLAN.md`：只列必要改动及其影响；
- 脱敏 evidence：一个真实 trial；没有真实 trial 要明确写缺失；
- `VALIDATION_PLAN.md`：1 个固定 smoke 的命令、预期输出和对账方式。

## 完成条件

固定 task 能把请求、token、native cost、trajectory、官方 verifier 结果和失败原因互相对上；日志补齐没有改变原方法的调用顺序、模型/路由选择、工具行为或终止条件。未满足的项目必须标为 `missing` 或 `unverified`，不能用默认 0 代替。

统一标准见 [实验协议](/home/zhoutianle/Projects/SGAR_research_base/docs/EXPERIMENT_PROTOCOL_V1.md)。
