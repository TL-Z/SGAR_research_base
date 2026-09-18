# SWE-Agent 负责人任务书

请先阅读 [共同任务说明](/home/zhoutianle/Projects/SGAR_research_base/docs/method_handoffs/COMMON_TASK.md)。先确认正式方法确实是 SWE-Agent；Mini-SWE-Agent 是不同方法，不能混用结果。本轮不改 action parser、prompt、history、retry 或 agent policy。

## 需要检查

1. 提交正式 SWE-Agent/Harbor 版本、实际配置、模型 endpoint、task runner 和真实 trial 路径；参考 adapter 不能代替运行证据。
2. 对照原生 `.traj`、`info/model_stats`、ATIF 和 Harbor result，确认每个实际模型请求、retry、失败/cancelled 请求及 request ID 的对应关系。
3. 核对 input/cache/output/reasoning token、native cost 和 latency 的真实来源。转换器生成的 timestamp 不能冒充请求发生时间；action history 长度不能冒充模型调用数。
4. 检查 task/trial/session/trajectory 的 ID、resume/重跑和输出覆盖；核对 official verifier、reward、failure/timeout 和 proxy/环境元数据。
5. 若字段缺失，优先使用原生日志或外层 collector/converter 补齐；不要为了补字段修改 agent 行为。镜像预热和公共代理由基建负责人处理，负责人只需验证入口兼容并报告阻塞。

## 最小交付

- 一份 `native info/model_stats → ATIF → result → canonical` 字段映射；
- 明确缺失、默认 0、转换生成 timestamp 和推算 cost；
- 一个真实 trial 的对账证据，不能混入 Mini-SWE-Agent；
- 必要时提交可复现的 wrapper/converter 方案，以及一个固定 smoke 验收计划。

## 参考位置

- `/ssd/zhoutianle/envs/rare-harbor/lib/python3.14/site-packages/harbor/agents/installed/swe_agent.py`
- `/ssd/zhoutianle/envs/rare-harbor/lib/python3.14/site-packages/harbor/agents/installed/mini_swe_agent.py`
- `/ssd/zhoutianle/envs/rare-harbor/lib/python3.14/site-packages/harbor/agents/factory.py`
- `/ssd/zhoutianle/envs/rare-harbor/lib/python3.14/site-packages/harbor/models/trial/result.py`
