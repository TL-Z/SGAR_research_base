# SGAR Linux 模型更新与 Opus 5 / Sol 审计报告

日期：2026-09-15

## 范围与起点

- 权威工作区：`/home/zhoutianle/Projects/SGAR_research_base`
- Python：`/ssd/zhoutianle/envs/sgar/bin/python`
- branch / HEAD：`master` / `542f06ea8389df19767596eb9e3b25c64f7fcccd`
- 起点工作区已有未提交修改；本轮未 reset、stash、clean、commit 或 push，也没有用 Windows 文件覆盖服务器源码。
- 本轮没有启动真实 `main.py` case、benchmark、训练、第二份 embedding 服务或整池模型探测。
- 起点状态和拟修改派生资产已记录并备份到 `/ssd/zhoutianle/runtime/sgar/maintenance/resource-coverage-20260915/model-update-opus-audit/`。

## Qwen 替换结果

| 用途 | 旧身份 | 新身份 | Linux 本轮结果 | 正式状态 |
|---|---|---|---|---|
| Coder | `qwen3-coder-30b-a3b-instruct` / `model.qwen3_coder_30b_a3b_instruct.v1` | `qwen3-coder-next` / `model.qwen3_coder_next.v1` | 文本、tool calling、真实 call ID 续接通过；JSON mode 与 native strict schema 均返回非完整合法 JSON | candidate 已登记，但 `blocked`；不进入 effective pool 和索引 |
| Vision | `qwen3-vl-30b-a3b-instruct` / `model.qwen3_vl_30b_a3b_instruct.v1` | `qwen3-vl-235b-a22b-instruct` / `model.qwen3_vl_235b_a22b_instruct.v1` | 文本、JSON mode、native strict schema、合成 PNG vision 全部通过 | `ready`；进入 effective pool 和索引 |

两个旧型号已从 `model_selection.json`、canonical catalog、effective pool 和新索引退出；旧 source JSON、历史失败记录和旧运行保持不变。配套历史证据说明旧 429 正文为 `has no provider supported`，因此不能仅解释为普通 RPM 限流。

新型号价格按用户提供的供应商截图记录，单位 USD/百万 tokens：

- Coder Next：输入 `0.5250`、输出 `3.9370`、缓存未知；canonical `ModelPrice` 解析为 `0.525 / null / 3.937`。
- VL235：输入 `0.4690`、输出 `1.8750`、缓存未知；canonical `ModelPrice` 解析为 `0.469 / null / 1.875`。

缓存价格没有填零。以上不是账户账单，也不证明新型号质量优于旧型号。

Linux 定向检查使用 `https://svip.xty.app/v1`、`LLM_API_KEY`、并发 1、`timeout=90s`、`max_tokens=4096`、SDK/基础设施重试均为 0。Coder Next 最多/实际 6 次物理发送，VL235 最多/实际 4 次，共 10 次。检查器未保存 usage，因此本轮实际费用未知；按 Windows 相同样例曾观察约 USD `0.0011`，按每次输出都达到 4096 token 的保守输出侧上界约 USD `0.13`，均不是实际扣费证明。

定向报告：`/ssd/zhoutianle/runtime/sgar/maintenance/resource-coverage-20260915/model-update-opus-audit/health-qwen-replacements/model_health_qwen_replacements.json`。

## Opus 5 完整消费链

Opus 5 的配置链核验通过，没有进行新的 Opus 在线调用：

1. source：`Pool/resources/models/claude-opus-5.json`，source ID `claude-opus-5`。
2. canonical：`model.claude_opus_5.v1`，在 `models.json` 和 `combine.json` 中各唯一一份。
3. wire identity：`execution.model_id`、`type_specific.model.model_id`、selection `api_model_id` 均为 `claude-opus-5`。
4. selection：单一 candidate 身份，不在 `control_only`，没有配置为系统默认或 Agent base Model。
5. pricing：输入 `7.695`、缓存读取 `0.77`、输出 `38.475` USD/百万 tokens；缓存创建 `9.619` 仅在 source provenance 中记录，当前 `ModelPrice`/账本没有 cache-creation 专用收费字段，不能宣称该项已计费。
6. context：source 标签和 documented native context 均记录 1M；这不构成第三方 gateway 已验证 1M 上限。
7. protocol：source 保留官方 `anthropic_messages` 的 native protocol 描述；实际供应商通过 OpenAI-compatible Chat Completions transport 调用，base URL 为 `https://svip.xty.app/v1`。运行时 Model 执行按全局 transport 和精确 API model ID 解析，不拼接 source URI，因此没有观察到 `/v1/v1` 请求。
8. health/admission：服务器既有 Linux 探测中，文本、JSON mode、generic strict schema、tool calling、tool-result continuation、vision 均为 `live_verified`，正式 applied ready-state 为 `ready`。
9. retrieval：Opus 位于 effective pool 和 2560 维索引；真实 loader/query 返回结果包含 `model.claude_opus_5.v1`。

Opus 既有探测报告：`/ssd/zhoutianle/runtime/sgar/maintenance/resource-coverage-20260915/health-targeted/model_health_targeted.json`。按探测方法是 6 个逻辑分项、7 次物理发送；该旧报告没有独立 model-call ledger/usage，因此其费用未知。本轮没有重复发送。

## Sol 双重用途验收

Sol 继续保持唯一身份：`gpt-5.6-sol` / `model.gpt_5_6_sol.v1`。

- 控制用途：`control_role_policy.json` 的 profiler、planner、plan_compiler、plan_adaptation、evaluator 五个角色均解析为该身份；现有 endpoint-bound control receipt 五条记录也全部匹配。控制角色解析来自完整注册模型定义，但只有显式角色引用会进入控制链。
- candidate 用途：Sol 的 `selection_scope` 为 `candidate`，位于 effective catalog 与当前索引；本轮通过正式 `RetrievalCoordinator.prepare_candidate_pool()` 冻结，成为 Model base candidates 的第一项，并产生精确 resolved identity。
- 冻结证据：candidate pool SHA256 `5db2cfb36d00154df82b05b43af456bad21756fc70e5636fdd239f6584ba5e1a`，readiness authority 为 `applied_ready_state`。
- 两种用途没有创建 `sol-control` / `sol-resource` 别名。控制 receipt 与 candidate ready-state 分别核验，没有把控制调用冒充 `model_execution`。

本轮遵守“不启动真实 case”的边界，因此没有新增 Sol 任务执行调用或 `model_execution` 账目；结论限于控制入口解析、候选检索/冻结及 Compiler 可引用身份，不宣称本轮完成了新的 Executor 端到端 case。

## 派生数据与索引

- 登记 catalog：337 个资源，其中 18 个 Model 定义（17 candidates + 1 control-only Fable）。
- applied model state：17 个 candidates，16 ready，1 blocked（Coder Next）。
- effective pool：55 个资源，其中 16 个 Model、39 个 Skill。
- 新索引 generation：`50be2f72c3ade4a8d69194db1bf2cd60`。
- 索引：55 条 capability 向量 + 55 条 constraint 向量，均为 2560 维 `IndexFlatIP`。
- embedding：现有 `http://127.0.0.1:8103/v1/embeddings`，served model `sgar-embedding-4b`，身份 `Qwen/Qwen3-Embedding-4B`；没有启动第二份服务或回退 0.6B。
- staging 索引：`/ssd/zhoutianle/runtime/sgar/maintenance/resource-coverage-20260915/model-update-opus-audit/index-4b-api-qwen-replacements/`。
- active 索引：`/home/zhoutianle/Projects/SGAR_research_base/Pool/index_meta/`。

真实 loader 加载 55 个资源、16 个 indexed Model。查询“vision-language + strict JSON”返回有效资源 ID，并包含 VL235、Opus、Sol；Coder Next、Fable 和两个旧 Qwen 均不在有效索引。

## 局部代码修正

`sgar_mvp/scripts/merge_targeted_model_health.py` 增加了候选集合替换支持：

- 新增候选必须出现在本轮 targeted report，缺任一新增身份即拒绝；
- 已退出候选的旧健康记录不会带入新集合；
- 未变化候选仅在 endpoint 和精确 resource/API identity 不变时保留原证据；
- 修复直接 CLI 运行时项目根未加入 `sys.path` 的导入错误；
- 默认 catalog 对齐健康检测实际签名的 `models.json`，避免同内容不同排序造成 hash 假不匹配。

离线反例确认：漏掉新增 Coder Next 时返回 `targeted_health_added_population_unprobed`；重复 canonical resource 返回 `model_selection_duplicate`；重复 API model ID 返回 `duplicate_api_model_id`。

## 验证命令

```bash
/ssd/zhoutianle/envs/sgar/bin/python Pool/resources/models/merge_models.py
/ssd/zhoutianle/envs/sgar/bin/python Pool/resources/produce/build_pool.py --check
/ssd/zhoutianle/envs/sgar/bin/python -m py_compile sgar_mvp/scripts/merge_targeted_model_health.py
git diff --check
```

另外运行了：两款新 Qwen 的有界 live health；同一 4B API 的独立索引构建；真实 loader/query；不调用外部 LLM 的正式 candidate freeze；Opus source/canonical/runtime/pricing/readiness/index 审计；Sol 控制 receipt 与 candidate identity 审计。

## 修改文件与回退

本轮直接配置/代码：

- `Pool/resources/models/qwen3-coder-next.json`
- `Pool/resources/models/qwen3-vl-235b-a22b-instruct.json`
- `sgar_mvp/config/model_selection.json`
- `sgar_mvp/scripts/merge_targeted_model_health.py`

同步生成：`models.json`、`combine.json`、`effective_combine.json`、`effective_tools.json`、`resource_readiness_rc1.json`、RC1 Markdown、applied model ready-state、`retrieval_policy.json` 及整套 `Pool/index_meta/`。

回退备份：`/ssd/zhoutianle/runtime/sgar/maintenance/resource-coverage-20260915/model-update-opus-audit/backups/pre-qwen-replacement/`。回退时必须成套恢复 selection、model ready-state、catalog/effective/readiness、retrieval policy 和五个 index 文件；不能只恢复 FAISS 或只恢复 selection。

## 停止边界

- Coder Next 的 Linux strict/JSON 协议不稳定是本轮第一个未通过边界；按既定一次语义检查停止，没有重复抽样刷成功。
- 现有 `gpt-5.3-codex`、`deepseek-v4-pro-0813` 等 ready 模型继续覆盖代码任务，因此没有擅自添加第三个 Coder 候选。
- 未验证长期稳定性、复杂 Schema、编码质量、OCR/视频质量、所有资源类型或 SGAR E2E；没有启动真实 case、benchmark、训练或更多模型扩充。
