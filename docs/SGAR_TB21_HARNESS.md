# SGAR Native Terminal-Bench 2.1 Harness

本适配保留 SGAR native pipeline，使用官方 TB2.1 task image 作为唯一 task-facing execution environment：

```text
宿主机 SGAR runner
  → 一个 task 一个持久官方 Docker container
  → SGAR external substrate 通过 docker exec 执行工具
  → 同一 container 中运行官方 tests/test.sh
  → unified result + native SGAR logs
```

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
