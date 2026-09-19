# 可直接提供给 GPT 的 SGAR 项目上下文

请把下面内容作为我当前本地 SGAR 项目的事实背景，帮助我后续构思论文定位、研究问题、Method 和 Experiments。请区分已经实现的机制、当前默认配置、历史兼容机制与尚未验证的贡献。完整说明见同目录 `SGAR_CORE_LOGIC_ANALYSIS_ZH.md`。

## 项目是什么

SGAR 是一个从共享异构资源池中为具体任务构建可执行工作流的研究框架。输入为自然语言请求、授权材料和交付要求；输出为实际交付物或可归因的结构化失败。资源主要分为 Model、Agent、Tool、Skill。仓库没有在正式主路径中训练 SGAR 参数或强化学习策略；目前是推理时的任务规划、检索、编译和执行。

最重要的结构是两层图：

- Planner 生成语义任务 DAG，定义每个节点负责什么、需要什么输入、产出什么、如何验收。
- Compiler 为每个节点生成资源执行图，选择真实资源操作、输入绑定、步骤依赖、输出契约和最终输出位置。

Planner 的一个角色节点不等于资源池中的一个 Agent。同一节点可以由工具完成，也可以由模型、Agent 或混合资源计划完成。检索 Top-1 不是最终执行资源。

## 完整工作流

```text
解析请求、授权并快照输入
→ 固定本次资源池、索引、策略和计费身份
→ Planner 构建语义 DAG
→ 每个节点生成 ideal resource profile
→ 按资源类型检索、检查兼容性、解析依赖闭包
→ 冻结每个节点的候选池·
→ DAG 调度：等待上游产物提交
→ 获取当前节点真正授权的公开输入与上游产物
→ Compiler 编译资源计划
→ 结构验证、输出可实现性检查、lowering、seal
→ 执行资源步骤 / 有界 Controller Session
→ 符合条件的失败最多进行两次同池计划适配
→ 输出 staging、机器检查和按策略执行的语义评估
→ verify、commit
→ 提交产物供下游消费，最终产物确定性导出
```

所有节点的候选池在 DAG 执行前准备；具体节点的编译发生在所需上游输出已经可消费之后。独立任务节点可异步调度，但实际加速比需要测量。

## 各模块的准确设计

**资源表示。** Manifest 记录资源身份、能力、问题空间、输入/输出接口、依赖、执行入口、环境要求、来源与价格/utility 信息。当前目录集合为 338 项，有效集合为 328 项：16 Model、20 Agent、138 Tool、154 Skill。数量是 2026-09-19 本地快照，有效集合仍需运行时筛选。

Model 生成内容；当前 Agent 主要是 `prompt_agent`，需要明确底座模型；Tool 执行明确接口，支持 MCP/Python/REST 等；Skill 是可复用指令和声明引用的集合，加载 Skill 不自动执行脚本、不自动获得工具权限。Tool 不一定是确定性的。

**Planner。** 当前 wire 协议为 v7。按主要职责、独立产物和真实生产者—消费者关系拆分；尽量拆到语义职责清楚，但不把读文件、保存、序列化、提交等机械动作都拆成任务节点。原子请求允许单节点。输入引用导出依赖；只有一个最终交付节点，所有中间节点必须贡献到它。

默认 resource-aware 表示 Planner 能看资源池派生的抽象角色能力上下文，不表示它能选择具体资源。具体执行模型、operation、命令和绑定由 Compiler 决定。仅保留用户明确提出且绑定原文条款的资源约束。

**输入契约。** 节点明确选择 task_text_only 或 requires_material。后者必须引用公开输入、上游输出或已有完成产物。源数据依赖的工作不能靠任务说明猜出源值。下游不会自动得到所有文件。材料区分完整内容、部分内容和可读取句柄；调度依赖不是数据绑定。

**Profiler。** 不看候选池，把类型化子任务转成理想资源描述，输出 capability_text、constraint_text 和简短可见依据摘要 think；不选资源、不解业务题。内部说明英文，用户字段和材料保留原样。think 不参与 embedding。

**检索。** 当前正式路径只编码 capability_text，并使用能力余弦相似度排序。基础配额 Model=5、Tool=10、Skill=8、Agent=3；依赖闭包和用户指定资源可追加，缺口也可使实际数量变小。Agent 底座模型和必需依赖要解析。候选池按子任务 revision 冻结，Compiler 和恢复不能任意重检索扩容。

仓库确实保留双向量索引、双分数、硬过滤、utility、RRF 等旧或备选检索实现，但当前正式 runtime 只接受 capability-only，不能把双向量加权写成当前主方法。当前活跃 embedding policy 是 Qwen3-Embedding-4B/2560 维；旧 release 配置中的 0.6B 不是现行活跃配置证据。

**Router 与 Compiler。** Router 正式路径管理 frozen session，Compiler 才选择操作级执行方案。Compiler 输入包含冻结候选卡片、义务、实际材料、依赖、runtime 能力与价格。步骤必须显式绑定 literal、artifact_handle、step_output 或 resource，并声明输入、输出和最终产物来源。

Compiler 的偏好是先可行且满足契约，再减少复杂度和生成调用；可行时优先确定性工具组合，需要语义处理时采用小规模混合方案，再考虑 Agent/controller；能力相当时比较模型价格。这个偏好不是经证明的全局最优算法，也不意味着已经实证更便宜。

**验证与 lowering。** LLM 提案经过合法性、义务覆盖、权限、依赖、输入输出与格式可实现性检查。资源的自然语言输出描述不是机器 Schema；给固定输出 Tool 写目标 Schema 不会改变其原生输出。可支持 identity、声明载荷提取、JSON 投影/包装和无损序列化；需要业务处理时必须显式安排可执行步骤。验证后降低为具体调用模板并 seal，执行前再检查身份一致。

**Runtime 和 Controller。** 外层执行任务 DAG，内部执行资源计划。Model/Agent 步骤可使用有界 Controller，工具必须由 Compiler 预先选择，动态参数和固定绑定分开。当前最多 4 回合、8 次工具调用、2 次契约/语义修复。代码生成与代码实际执行需要分别建模。

**恢复。** 正式 `strict_plan_only` 下，符合条件的执行失败最多两次同冻结池计划适配，保留有效 checkpoint 和副作用约束。禁止历史 temporary Tool recovery 与 Full Generation fallback。`allow_plan_recovery=false` 是旧分支开关，不能据此说正式 RecoveryController 不恢复。冻结后失败不重新检索/重规划整图。

**产物与评估。** 流程为 staging → evaluation → verify → commit → downstream/export。资源输出不自动成为节点交付物。active 模式语义判定阻断提交；silent 模式仅观察；off 不调用语义 Evaluator，但保留机器检查。当前本地默认 off。静态 PASS 不证明业务正确。原始任务高于生成的契约；Evaluator 只评价当前节点职责，不要求中间节点完成下游任务。

正式提交路径保留实际产物，不运行旧 HiRAG LLM 压缩来替代精确内容。最终导出从 committed artifact 复制，不再交给模型改写答案。

## 当前配置与实验事实

默认控制模型配置为 gpt-5.6-sol；Planner/Profiler/Compiler/adaptation 使用 xhigh，Evaluator 为 high。控制模型与业务执行模型分离。语义 Evaluator 默认 off，正式 recovery 最多两次，Full Generation 为零。资源健康与协议能力需要端点证据，本文没有确认实时外部服务能力。

请求、Planner、Profiler、Compiler、执行、重试、Evaluator 等费用应从请求级 ledger 对账，不能只计执行模型。当前 stop_after_limit 为 10 美元，不保证在途并发请求不会超出。Utility/memory 和 TrainingLabel 字段不表示已经在线学习；正式观察不修改源 manifest。

已有 native Terminal-Bench 2.1 adapter：官方 task container → 原生 SGAR external substrate/RPC → 同容器官方 tests/test.sh → reward 和日志。早期“adapter 未实现”的文档已滞后。普通最终文件导出在 benchmark worker 中可以省略，以环境状态接受验证。

非常重要：当前 TB runner 的 success 表示运行链结束条件，不要求官方 reward=1。本次抽查三个 regex-log trial 全部 reward=0，其中两个 success=true，而这两次原生 pipeline_status 均为 structured_failure；因此不能把 success 作为原生方法成功或解题成功。现有记录不构成正式全量评测。历史 G2 有 pipeline 成功记录，但对应旧提交/实验配置，real_case_passed=null，不能代替当前主方法的正确率证据。

## 我希望你如何帮助我组织论文

先理解上述实现，不预设论文贡献已经成立。可以优先考虑“以契约连接的分层异构资源工作流合成”作为主线，研究两层图、理想资源检索、操作级组合以及产物移交如何共同影响正确率、成本和失败可诊断性。

请先提出少量可检验研究问题，再建议主实验与最必要消融。方法定义必须固定检索、backbone、资源池、Evaluator 模式和恢复预算；分别报告编译成功、执行成功、产物交付和外部正确率，计入控制和失败请求的成本。创新性需文献比较，性能主张需实验。

不要把本系统改写成固定四 Agent 流程、参数训练式路由器、默认双向量检索、无限自修复框架，或已经验证优于基线的方法。SGAR 正式英文全称仍需项目作者确定。
