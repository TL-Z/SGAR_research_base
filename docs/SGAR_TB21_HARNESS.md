# SGAR Native Terminal-Bench 2.1 Harness

本适配保留 SGAR native pipeline，使用官方 TB2.1 task image 作为唯一 task-facing execution environment：

```text
宿主机 SGAR runner
  → 一个 task 一个持久官方 Docker container
  → SGAR external substrate 通过 docker exec 执行工具
  → 同一 container 中运行官方 tests/test.sh
  → unified result + native SGAR logs
```

### Resource execution invariant

在 TB2.1 external 模式下，宿主机只负责 SGAR 调度、模型 API、日志和 verifier
控制；所有被正式计划选中的 Tool wrapper 及其同目录 helper 都必须先通过 RPC
写入当前 task container，再使用该 container 内的 `python3`/声明命令执行。资源的
宿主机 `execution.uri` 只用于读取和 staging，绝不会作为容器内的执行路径传入。

`runtime_preparation` 在 external 模式只记录
`external_substrate_owns_task_runtime`，不再调用宿主机 overlay/依赖安装逻辑。
任何未 staging 的宿主机绝对路径、legacy executor、缺失 task-container runtime
或不匹配的 substrate mode 都会 fail-closed；不会回退到宿主机执行。模型 API 是
唯一允许留在宿主机侧的外部调用，Skill 仅作为提示/约束，不是执行器。

资源依赖若不在当前 task image 中，也只能报告明确的 container-side dependency
failure；不能借用宿主机 Python 环境“补跑”。因此正式 batch 前仍需针对 task image
做 dependency preflight，确保选择的资源在该 image 中可执行。

## 运行前检查

```bash
cd /home/zhoutianle/Projects/SGAR_research_base
PYTHONPATH=. /ssd/zhoutianle/envs/sgar/bin/python \
  -m sgar_mvp.benchmarks.terminalbench21.runner inventory

/ssd/zhoutianle/envs/sgar/bin/python scripts/tb21_image_prep.py verify
```

正式运行不允许 task trial 内自动 pull/build；preflight 缺 image 时直接失败。

## 单 task smoke

必须通过统一代理包装器启动。`--network` 仅表示该 task 的官方 `allow_internet=true` 网络权限；模型 API 仍由 SGAR 的 direct HTTP client 直连，task container 内的普通网络和 verifier 下载使用代理。

```bash
cd /home/zhoutianle/Projects/SGAR_research_base
sgar-net-run \
  env PYTHONPATH=/home/zhoutianle/Projects/SGAR_research_base \
  /ssd/zhoutianle/envs/sgar/bin/python \
  -m sgar_mvp.benchmarks.terminalbench21.runner \
  --task-id regex-log \
  --task-root /ssd/zhoutianle/sgar-benchmarks/terminalbench21/official_tb21/terminal-bench-2-1 \
  --output-root /ssd/zhoutianle/runtime/sgar/tb21-runs \
  --config /home/zhoutianle/Projects/SGAR_research_base/sgar_mvp/config.json \
  --network
```

## 批量运行

批量入口先对所有选中 task 做 image digest preflight，再开始第一条模型调用；任何缺 image 都不会开始 batch。

```bash
sgar-net-run \
  env PYTHONPATH=/home/zhoutianle/Projects/SGAR_research_base \
  /ssd/zhoutianle/envs/sgar/bin/python \
  -m sgar_mvp.benchmarks.terminalbench21.runner \
  --task-id all \
  --max-tasks 20 \
  --task-root /ssd/zhoutianle/sgar-benchmarks/terminalbench21/official_tb21/terminal-bench-2-1 \
  --output-root /ssd/zhoutianle/runtime/sgar/tb21-runs \
  --config /home/zhoutianle/Projects/SGAR_research_base/sgar_mvp/config.json \
  --network
```

去掉 `--max-tasks` 才是全量 89 task。首次建议先用 1 task、再 5 task、再 20–30 task。

## 每个 trial 的证据

```text
trial_manifest.json
preflight.json
rpc_actions.jsonl
worker_terminal.json
verifier.json
verifier_stdout.log
verifier_stderr.log
result.json
native/
  run_manifest.json
  model_calls.jsonl
  execution_summary.json
  cost_summary.json
  trace.jsonl
```

`agent` 方法时间不包含 image pull/build，也不包含 verifier；`e2e` 可以包含 setup、agent、verifier 和 cleanup。官方 reward 只来自 task container 内的原始 `tests/test.sh`。

## 当前验证状态

- canonical task loader：已通过 89 task 和 `c31db162da898b59e8f5d827715a65378ca999e4df0dc052fe2a18f4241e0c0b` 校验；
- 现有 runtime/external-sandbox regression：41 tests passed，2 subtests passed；external task
  path discovery is fail-closed and uses only typed `/app` substrate paths；
- 当前服务器 preflight：89/89 images present、0 missing、0 wrong platform、0 digest missing；
- Docker task-container contract smoke：本次复跑在 `sgar-net-run` 的前置检查阶段停止，原因是服务器上的
  `mihomo-flybird` 未运行；代理服务恢复后需要在服务器终端执行上面的单 task 命令完成最终验收。
