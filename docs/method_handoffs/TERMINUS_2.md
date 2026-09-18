# Terminus-2 / Harbor 负责人任务书

请先阅读 [共同任务说明](/home/zhoutianle/Projects/SGAR_research_base/docs/method_handoffs/COMMON_TASK.md)。保留 Terminus-2 的现有 agent 和 Harbor 执行方式；不直接修改共享 site-packages，不改模型、prompt 或 retry 策略。

## 需要检查

1. 提交实际 Harbor Python/版本或源码 hash、agent 配置、job/trial 配置、模型 endpoint 标识和真实 trial 路径；不能用参考安装代替部署证据。
2. 对照原生模型日志、Harbor `TrialResult`、ATIF 主/子轨迹，确认 main、summary、subagent、continuation 和 retry 的真实请求数与 token usage。
3. 明确 input/cache/output/reasoning、native cost 和 latency 的来源及覆盖范围；ATIF step 数不能直接当模型调用数，复制 context 不能重复计费。
4. 检查 request/attempt、trial、trajectory、verifier、failure/timeout/cancel 的 ID 关联和去重；未知字段保留为 `null`。
5. 核对 image/config/source hash、agent/verifier/e2e 时间和 official verifier 状态。验证 Harbor 运行可由统一外层 wrapper 启动；镜像/sidecar 预热由基建负责人完成。

## 最小交付

- 一份 `physical request/attempt → role → native log → ATIF/result` 映射；
- summary/subagent 的 token、cost、retry 覆盖证据及缺口；
- 若需改动，只提交版本化 sidecar、context 传递或 converter 方案，不原地改 Harbor 共享安装；
- 一个已有 trial 的离线对账和一个固定 smoke 验收计划。

## 参考位置

- `/ssd/zhoutianle/envs/rare-harbor/lib/python3.14/site-packages/harbor/agents/terminus_2/terminus_2.py`
- `/ssd/zhoutianle/envs/rare-harbor/lib/python3.14/site-packages/harbor/llms/chat.py`
- `/ssd/zhoutianle/envs/rare-harbor/lib/python3.14/site-packages/harbor/models/metric/usage_info.py`
- `/ssd/zhoutianle/envs/rare-harbor/lib/python3.14/site-packages/harbor/models/trial/result.py`
