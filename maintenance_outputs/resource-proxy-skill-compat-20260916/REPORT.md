# SGAR 网络代理与资源协同维护

结论：`SGAR_LINUX_PRE_CASE_NOT_READY`。本轮完成项目级代理作用域与 Skill→Agent 合同的局部修正；现有索引和 readiness 未被重建或降级。一次合成运行在 Planner 严格 wire 校验处失败，因此没有伪造 Agent/Tool/Skill 闭环证据。

## 起点与边界

- 工作区：`/home/zhoutianle/Projects/SGAR_research_base`，branch `master`，HEAD `542f06ea8389df19767596eb9e3b25c64f7fcccd`；保留起点全部 dirty/untracked 修改，未 reset/stash/clean/commit/push。
- 未修改 Docker/GPU、LLM、4B embedding 服务或 `/zhangjie`。本会话无法写入 `/ssd/zhoutianle/runtime/sgar/...`（只读），产物暂存于本目录 `maintenance_outputs/resource-proxy-skill-compat-20260916/`。
- Docker image：`sgar-runtime:rc1`，`sha256:3df4a3e71cdb166638b00b2b1af650d54068abb07c11c05042d3f67c9d699185`。

## 修改

- `sgar_mvp/src/direct_network.py`：新增 `.env` 项目级 `SGAR_TOOL_HTTP_PROXY`、`SGAR_TOOL_HTTPS_PROXY`、`SGAR_TOOL_NO_PROXY` 读取、脱敏指纹和子进程环境投影。默认仍清除继承代理并直连；代理值通过 `env` 传递，不进入 Docker argv。
- `sgar_mvp/src/executors.py`、`sgar_mvp/src/tool_execution_provider.py`：仅 `network_required=true` 且 `network_policy=declared` 的 Tool 注入代理；`network=none`、LLM、embedding、本地 Tool 不注入；代理指纹纳入执行世界身份和审计。
- `sgar_mvp/src/executable_plan.py`、`sgar_mvp/src/plan_compiler.py`：Skill 输出仅在其被 Agent 的 `advisory_profile_refs` 明确引用时免于普通业务 artifact 类型匹配；accepted-plan wire 保留 `advisory_profile_refs`，不放宽材料、权限、operation 或 sealed scope 校验。
- `.env.example`、`sgar_mvp/scripts/validate_skill_package_hashes.py`、`sgar_mvp/tests/test_direct_network.py`：补充配置示例、脚本可执行路径修复和 focused tests。

## 验证

- Skill package：154/154 通过 readiness、ingestion、runtime 三路 hash；catalog 字节未变化，无 manifest diff。
- Effective pool/index：Model 16、Agent 20、Tool 138 ready、Skill 154 ready；5 Tool transient、2 Tool inactive、2 Model blocked、1 Model inactive；effective 328。4B index generation `2cd50d51aabe41a5972c0d41f578727c`，两索引均 328×2560，loader/query 定向验证通过，模型为 `Qwen/Qwen3-Embedding-4B`。
- Proxy focused tests：4 tests passed；未设置代理时保持直连。代理 URL 未进入 audit 字段或 Docker argv（仅环境变量传递与 SHA-256 指纹）。
- 网络定向诊断：Arxiv DNS/TCP/TLS/HTTP 通过但正式 smoke 45 秒超时，保持 transient；Google DNS、Nominatim、Wikipedia、Coingecko 分别出现网络不可达/握手超时/连接重置，保持 transient，未改 readiness。
- 合成运行：`composition-run/20260915T201306Z_9c66d36b3ef2`，Planner 使用 `gpt-5.6-sol` 发送 2 次，均 `planner_wire_v6_payload_invalid`；未调用 blocked 的 Opus 5/Coder Next，未进入 Compiler、ResourceRuntime、Skill 注入、Tool call、evaluate/commit/delivery。
- 后续同一 native strict Planner FORMAT probe 对 `gpt-5.6-sol`、`gpt-6-astra`、`qwen3.5-35b-a3b`、`gpt-5-nano` 各发送 1 次，4/4 通过；结果见 `planner-model-comparison.json`。这证明 Sol 当前接口可返回合法严格 JSON，也说明本次失败与生产合成请求内容/时段相关，而不是全局 Planner endpoint 或代理故障。
- 对同一合成 fixture 的一次完整重试已越过 Planner，但在 Retrieval 被确定性拒绝：模型把用户自然语言资源描述写成了非 canonical 身份（如 `full-stack implementation Agent`、`authorized filesystem read-file Tool`、`verification-before-completion Skill`），触发 `execution_requirement_resource_unavailable`。此前同一 fixture 曾生成 canonical resource IDs 并进入 Compiler；本次差异属于 Planner 语义身份输出不稳定，检索 gate 正确拒绝了不可解析身份，未继续调用执行资源。重试 run：`composition-retry/20260915T202900Z_d0e182473f7f`。
- 现有 `reproduce_candidate_error.py` 候选池回归通过；本轮未运行全量 pytest、benchmark、训练或真实 case。

## 回退

删除 `.env` 中三个 `SGAR_TOOL_*` 变量即可恢复默认直连。代码回退仅涉及本轮标注的代理 helper、执行器注入、执行世界指纹和 Skill advisory projection；不要回退既有 4B index、readiness、模型池或 Sol 双重身份修改。

下一次真实 case 命令模板见 `NEXT_REAL_CASE_COMMAND.sh`；该文件只检查密钥存在并等待用户提供 `REQUEST_MANIFEST` 与 `PUBLIC_INPUT_ROOT`，本轮未执行。
