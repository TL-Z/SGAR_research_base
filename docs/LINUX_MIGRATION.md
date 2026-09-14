# Linux 迁移说明（本地准备版，2026-09-14）

## 1. 交接范围与当前状态

本轮只整理本地目录与资产；未创建 Git 仓库、上传、登录 Linux、安装依赖、运行 Provider、真实任务或 benchmark。

- 原来源：`E:/SGAR-CLEAN-RELEASE-CANDIDATE`，`master`，HEAD `21ef3369ce2a65239dcb89873131c4042868f75c`，包含当时未提交的实际修改；此 HEAD 不是整个分发目录的身份。
- 纯净目录：`E:/SGAR-LINUX-CLEAN-20260914-154415`。
- 项目归档：`D:/SGAR/maintenance/linux-local-prep-20260914-154415/SGAR-LINUX-CLEAN-20260914-154415.tar.gz`。
- 外置资产包：同一维护目录下 `SGAR-LINUX-ASSETS-20260914-154415.tar`。不要将约 1.2 GB 权重提交到普通源码 Git 仓库。
- 本地验证、筛选清单及整理报告留在维护目录，不进入项目包。

所有保留的原文件均保持相对位置和字节。新增的分发文件仅为根 README、`.gitignore`、`.gitattributes`、本文、观察环境 JSON 和 Linux 示例配置。没有改动执行逻辑、模型选择、提示、响应协议、资源池、索引或验收门槛。

现有本地目录不是 sealed release。索引是原有 local/unsealed generation；带 `require_release_sealed` 的正式发布路径不能将它当作已封存索引。普通 Git authority 和 sealed release 是不同入口，不能用放宽校验互相冒充。

## 2. 用户手动准备目标位置与资产

先确认：Linux 发行版、x86_64/其他架构、Python 3.11 可用性、GPU/驱动/CUDA、BF16 支持、磁盘、Docker 权限与联网政策。原 Docker 定义面向 `linux/amd64`；本轮未证明 ARM 可用。使用自己的目录与环境，不改共享服务器全局环境。

以下全是供用户之后手动执行的 Bash 示例；尖括号占位符必须换成实际绝对路径。

```bash
export SGAR_PROJECT_ROOT='<LINUX_PROJECT_ROOT>'
export SGAR_ASSET_ROOT='<LINUX_ASSET_ROOT>'
mkdir -p "$SGAR_PROJECT_ROOT" "$SGAR_ASSET_ROOT"
tar -xzf '<TRANSFER_DIR>/SGAR-LINUX-CLEAN-20260914-154415.tar.gz' -C "$SGAR_PROJECT_ROOT"
tar -xf '<TRANSFER_DIR>/SGAR-LINUX-ASSETS-20260914-154415.tar' -C "$SGAR_ASSET_ROOT"
cd "$SGAR_PROJECT_ROOT"
```

项目归档以项目相对路径为成员，不再多包一层根目录。资产包有两个前缀：

| 前缀 | 内容和用途 |
|---|---|
| `hf_cache/hub/models--Qwen--Qwen3-Embedding-0.6B/snapshots/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/` | 现存固定版本的 12 个完整文件，含真实 safetensors、tokenizer、配置；不含下载令牌、缓存锁或其他模型 |
| `project/sgar_mvp/runtime_state/` | 两个原样历史准入文件 `model_ready_state.json`、`control_role_probe_receipt.json`，不包含真实任务日志 |
| `ASSET_MANIFEST.json` | 包内文件大小与 SHA-256，属于分发核对清单，不是新的运行时门槛 |

配置现有程序真正读取的环境变量：

```bash
export HF_HOME="$SGAR_ASSET_ROOT/hf_cache"
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export SGAR_EMBEDDING_OFFLOAD_DIR="$SGAR_ASSET_ROOT/embedding-offload"
export SGAR_RELEASE_STORAGE_ROOT='<LINUX_USER_OWNED_STORAGE_ROOT>'
mkdir -p "$SGAR_EMBEDDING_OFFLOAD_DIR" "$SGAR_RELEASE_STORAGE_ROOT"
# 这两个是 HF 库的离线开关，只用于本地资产预检；不是 SGAR 的 Provider 禁用开关。
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
```

SGAR 的快照解析优先读 `HUGGINGFACE_HUB_CACHE`，其次 `HF_HOME/hub`。固定 revision 已提供，不需要 `refs/main` 或联网寻找替代权重。`SGAR_PROJECT_ROOT` / `SGAR_ASSET_ROOT` 只是本文 shell 路径变量，不是 SGAR 自带的配置入口。

历史准入资料先核对再应用：

```bash
python - <<'PY'
import os
from pathlib import Path
from sgar_mvp.src.local_control_readiness import load_git_control_probe_receipt
from sgar_mvp.src.retrieval_runtime import load_applied_model_ready_state
from sgar_mvp.src.model_transport import production_model_endpoint_identity
root = Path(os.environ['SGAR_ASSET_ROOT']) / 'project'
endpoint = production_model_endpoint_identity(base_url=os.environ['LLM_BASE_URL']).identity_sha256
_, receipt = load_git_control_probe_receipt(root, config={}, expected_endpoint_identity_sha256=endpoint)
state = load_applied_model_ready_state(root, expected_endpoint_identity_sha256=endpoint)
print('Offline identity checks passed:', len(receipt['records']), 'roles;', len(state.models), 'candidate model records')
PY
# 只在上面核对通过、且决定使用该端点与现有证据后执行：
mkdir -p sgar_mvp/runtime_state
cp "$SGAR_ASSET_ROOT/project/sgar_mvp/runtime_state/model_ready_state.json" sgar_mvp/runtime_state/
cp "$SGAR_ASSET_ROOT/project/sgar_mvp/runtime_state/control_role_probe_receipt.json" sgar_mvp/runtime_state/
```

上面脚本需要先完成第 3 节依赖准备并设置第 4 节的 `LLM_BASE_URL`；它不读取密钥、不发请求、不产生新 receipt。本地现有证据对应 `https://svip.xty.app/v1`。5 个控制角色和 16 个候选 Model 的现有资料仅证明历史记录及当前离线身份一致，不证明目标 Linux 的 liveness/网络/账号可用。候选资料中的 operator admission 不应改称在线能力实测。端点、请求或角色身份发生变化时必须走原有重新验证流程，不伪造、延长或清空身份。

### 尚未随包提供的必要条件

- **Docker 镜像实体未交付**：源码包含 `sgar_mvp/docker/Dockerfile.runtime`、`sgar_mvp/config/rc1_runtime_lock.json` 与运行配置，未获得镜像归档，也未启动 Docker 导出/构建。旧锁中的 `sgar-runtime:rc1` 镜像 ID 为 `sha256:3df4a3e71cdb166638b00b2b1af650d54068abb07c11c05042d3f67c9d699185`。Docker 资源路径需要用户提供匹配镜像，或另行构建并完成既有身份/清单核验；不能宣称重新构建一定得到同一镜像 ID。不要只改 tag 或 hash 绕过要求。
- **目标 Python/embedding 环境未确定**：版本约束差异见下一节。权重齐全不等于该索引在任意新环境可准入。
- 其他任务的用户输入、外部 benchmark 与其独立检测器由用户提供，不混入本包；目录内只保留现有公开样例的最小依赖。

## 3. 环境：观察值、声明值、Linux 待定值分开

当前观察为 Windows AMD64 / Conda Python 3.11.15。详见 [观察记录](WINDOWS_ENVIRONMENT_OBSERVED.json)，**不是 Linux 安装锁，也不是 Tool 容器环境**。不复制 Windows 虚拟环境。

| 项目 | 现有 Windows / 索引相关观察 | `requirements-index.txt` 声明 |
|---|---|---|
| torch | 2.11.0+cu128 | 2.11.0+cpu，CPU wheel 源 |
| transformers | 5.4.0 | 5.14.1 |
| sentence-transformers | 5.3.0 | 5.6.0 |
| faiss-cpu | 1.13.2 | 1.14.3 |
| numpy | 2.4.3 | 2.5.1 |
| openai | 2.29.0 | 2.49.0 |
| certifi | 2026.2.25 | 2026.7.22 |
| accelerate / bitsandbytes | 1.14.0 / 0.50.1 | 该文件没有完整声明 |

项目声明和实际环境原样保留，没有替用户升级或修改。索引包含精确 embedding runtime package identity，因此**直接安装声明版本可能被原有索引兼容检查拒绝**。不能把这个文件当作迁移复现锁。

用户确认目标硬件与版本矩阵后，才采用候选安装步骤：

```bash
cd "$SGAR_PROJECT_ROOT"
python3.11 -m venv .venv
. .venv/bin/activate
# 以下是原项目依赖声明的安装入口，不是已经验证的复现配方。
# 只有决定采用声明版本并解决它与现有索引的关系之后才执行：
python -m pip install -r requirements-index.txt
# 当前生产导入还依赖的宿主包（观察版本；同样需目标环境确认）：
python -m pip install pydantic==2.12.5 loguru==0.7.3
```

若选择严格复现现有索引，应先按观察记录和索引内身份确认一套 Linux 可安装组合，尤其 torch/CUDA、transformers、accelerate、bitsandbytes；不要先执行上面的声明安装再假定兼容。目标环境未知，本轮没有编造完整 Linux lock 或指定 CUDA wheel，也没有授权重建索引。`requirements-dev.txt` 是原开发依赖，不是运行必需步骤；分发目录没有工程回归套件，不要以空测试树验收系统。

根目录无 `pyproject.toml`/`setup.py` 的可安装项目定义；从项目根执行入口，不使用未经支持的 `pip install .`。Tool 所需的 Python/Node/系统包以各资源 manifest 与 Docker/runtime 清单为准；部分包只在容器需要，不能用宿主 `pip freeze` 代替。

## 4. 手动配置与 Git 身份

```bash
cd "$SGAR_PROJECT_ROOT"
cp sgar_mvp/config.linux.example.json sgar_mvp/config.json
export LLM_BASE_URL='https://svip.xty.app/v1'
# 由你通过安全环境变量或项目根 .env 提供 LLM_API_KEY；不要写进 Git。
```

新模板从原 example 派生，补入当前实际 `runtime_preparation_enabled=true`；其余模型、超时、重试、恢复策略与当前值保持一致。运行路径使用现有支持的相对 `runs`、`tmp`、`.sgar_cache/pip`。若指定其他路径，请只编辑现有 `runtime_settings.output_root/temporary_directory/pip_cache_directory`，并遵守所用 authority 的存储根要求。`main.py --config` 的相对路径基于 `sgar_mvp/`，不是 shell 工作目录。

程序需要真实 Git HEAD/工作区身份。CLEAN 没有 `.git`，这不是可直接绕过生产准入运行的目录。建仓、分支、提交、上传由用户手动完成；需要 approved branch 的原有路径接受 `master`。新仓库具有新 HEAD，不能复用旧 source seal 伪装同一发布。旧 sealed-release 专用工具仍需要各自完整前置条件。

`.gitattributes` 使用 `* -text` 防止自动换行转换；没有将任何现有源码转换为 LF。后续 Git 建仓/检出之后仍应按文件字节核对，并确认本机或全局 attributes/filter 没有重写内容。不要把 Windows 复制或本次 tar 验证当作已经验证过 Git/Linux 检出。

## 5. 先离线预检，再单独真实运行

在目标环境中手动进行以下无任务、无 Provider 的轻量检查：

```bash
cd "$SGAR_PROJECT_ROOT"
python -B sgar_mvp/main.py --help
python -B -m sgar_mvp.real_case_batch --help
python -B -m sgar_mvp.src.run_validator --help
python -B -m Pool.resources.produce.build_pool --check
```

这些入口已在本地审查并由禁止网络/子进程的隔离检查执行：`--help` 在配置、运行创建前退出，`build_pool --check` 只比较资源目录而不写入。它们不证明真实模型/Tool执行、embedding加载或完整启动准入通过。

不要默认所有 `probe`、`prepare_*`、`smoke_*` 都离线。现有格式探测/资源 smoke/真实运行脚本可能外呼或执行资源，本轮都没有运行。`prepare_local_pool_index.py` 仍从 `embedding_release.json` 读取 Windows cache_root 并设置 `HF_HOME`，不能当作 Linux 无副作用预检；该配置/工具本輪未改，后续确需使用时另行对齐路径和身份。`pre_real_case_readiness.py` 的开发验收依赖完整工程测试套件，应在保留的原开发项目使用；不在精简目录靠跳过套件伪造完整发布验收。

**下面才是真实任务，会调用模型并产生费用，用户在全部前置条件满足后自行启动：**

```bash
cd "$SGAR_PROJECT_ROOT"
python -B sgar_mvp/main.py \
  --request-manifest '<PUBLIC_INPUT_ROOT>/request.json' \
  --public-input-root '<PUBLIC_INPUT_ROOT>'
```

也支持 `--query-file`、重复 `--input NAME=PATH` 和显式 `--public-input-root`；使用已有 `TaskInvocation` 公开输入协议，不能用给每个节点全部材料的方法绕过授权。`--network-policy disabled` 只限制 Tool 网络，不代表 Provider 被禁用。主程序没有通用 `--offline`/`--dry-run` 参数。

科研批处理入口保留：

```bash
python -B -m sgar_mvp.real_case_batch \
  --suite '<PUBLIC_SUITE_JSON>' --output-root '<BATCH_OUTPUT_ROOT>'
# 对真实终止运行检查清单、完整性和生产一致性：
python -B -m sgar_mvp.src.run_validator '<RUN_DIR>'
```

批处理 suite 必须符合当前 `sgar_mvp/real_case_batch.py` 的既有协议。本轮未运行批处理或修改其策略。`run_validator` 不替代独立语义判分；外部 benchmark 仍用其自己的检测器。现有 `sgar_mvp/scripts/run_live_golden_cases.py` 的公开合成样例独立 oracle 和最小输入已保留，但该 runner 的封存/执行权限要求仍在，不能因文件存在就认为可以直接绕过运行。

## 6. 跨平台与功能边界

- 本地静态检查未发现项目文件大小写冲突、内部导入缺失、manifest 的文件路径悬空、LFS pointer、外部链接或 Windows `.exe/.dll/.pyd` 二进制。不是完整 Linux 系统工具可用性证明。
- 有 3 个原有 CRLF shell 文件：`Pool/resources/skills/vendor/openai_plugins/11c74d6ba24d3a6d48f54a194cd00ef3beea18f9/` 下 `brainstorming-4625fbfa/scripts/start-server.sh`、`stop-server.sh` 和 `systematic-debugging-530855df/find-polluter.sh`。对应 Skill 是资源能力的一部分，文件保留且未转换；若 Linux 需要执行这些 shell 内容，存在换行兼容风险。不能在封存资源内直接 dos2unix 再假称原包未变，相关修正需单独处理。
- tar 中现有 Git 100755 文件使用可执行模式，其余普通文件按 0644；未跟踪脚本没有可推断的 Git executable 位。无链接需要复建。Windows 无法完整证明 POSIX 权限/执行行为。按文档用 `bash script.sh` 也不能解决 CRLF 本身的问题。
- `embedding_release.json` 的 Windows 路径保留；运行时快照/offload 使用第 2 节真实环境变量，维护工具是否支持该覆盖要分别核对。`SGAR_RELEASE_STORAGE_ROOT` 在非 Windows 需要显式绝对路径。
- 原 336 个目录资源及当前 331 个有效索引资源、154 个 Skill 包保持。`Pool/reserve` 不是当前有效池输入，历史 reserve 不随包；没有减少当前召回候选集合。
- 所有阶段的结构/权限/来源/语义校验和 staged → evaluate → verify/commit → delivery 顺序保留。本轮未重构，不处理模型随机方案或任务语义漂移。

## 7. 保留的最小“测试”依赖与已知限制

根 `tests/` 仅保留现有独立公开样例检查器直接使用的 G1/G2/G3 request 和输入，共 6 文件；`sgar_mvp/tests/` 仅保留 Agent 准入脚本直接引用的 `fixtures/agent_composition_input.md`。资源包内部测试、样例和 LICENSE/NOTICE 按原包保留，以满足原有包完整性和可用能力。除此之外工程回归套件、历轮补丁测试、临时 harness、旧运行/缓存/审阅包不进入 CLEAN。

当前已知语义状态：最近运行完成执行与正式交付，但人工检查发现任务转换偏差及内部评价漏判。这不等于独立任务正确。本轮没有重跑该任务、调提示或改变依赖/Profiler 顺序。

本地验收只证明筛选、字节、静态资源与独立导入没有发现漏项。Linux 包版本、镜像实体、资源 shell、Git身份/准入和真实端到端输出仍待目标机器核验。本轮没有测试被 skip 后记为通过；未重跑旧回归或历史失败，旧结果不计入本轮。
