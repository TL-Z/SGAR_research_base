# Baseline 负责人任务分发

这些任务书只覆盖其他方法的自查、日志核对和必要的最小记录适配。公共基建、TB2.1 镜像预热、统一代理、公共 preflight 以及 SGAR native benchmark adapter 不在这些任务书内，由主实验线负责。

## 分发关系

| 负责人 | 文档 | 方法边界 |
|---|---|---|
| AOrchestra 负责人 | [AORCHESTRA.md](AORCHESTRA.md) | 保留 native MainAgent/SubAgent |
| Uno-Orchestra 负责人 | [UNO_ORCHESTRA.md](UNO_ORCHESTRA.md) | 保留 native router/worker/policy |
| Terminus-2 负责人 | [TERMINUS_2.md](TERMINUS_2.md) | 保留 Terminus-2 + Harbor |
| SWE-Agent 负责人 | [SWE_AGENT.md](SWE_AGENT.md) | 先确认不是 Mini-SWE-Agent |

所有负责人先读 [COMMON_TASK.md](COMMON_TASK.md)，再读自己的专项文档。共同标准以 [实验协议 v1.0](/home/zhoutianle/Projects/SGAR_research_base/docs/EXPERIMENT_PROTOCOL_V1.md) 为准。

## 工作顺序

1. 先确认实际代码、配置、版本和真实运行证据。
2. 再完成字段覆盖和调用链对账，区分日志缺口与执行语义问题。
3. 只提出或实施不改变方法行为的最小日志、ID、wrapper 或 converter 调整。
4. 用一个固定 smoke 对账请求、token、trajectory、verifier 和失败状态。

没有真实 trial 的负责人必须明确标记 evidence 缺失，不能用参考安装或示例日志代替。
