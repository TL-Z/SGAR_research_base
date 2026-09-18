# Uno-Orchestra 负责人任务书

请先阅读 [共同任务说明](/home/zhoutianle/Projects/SGAR_research_base/docs/method_handoffs/COMMON_TASK.md)。Uno 继续使用 native router、worker pool、primitive 和当前正式 policy/checkpoint；本轮不改 router、prompt、worker 行为或重新训练。

## 需要检查

1. 确认正式使用的 launcher、router 类、policy/checkpoint、worker pool、模型 endpoint 和配置版本；旧 base-model pilot 与正式 SFT/native policy 必须分开。
2. 核实实际加载的 TB2.1 task tree、task ID 和 manifest hash，不能只看目录名或版本字符串。
3. 检查 router、worker、callback、retry/fallback 的请求是否都带有同一 `experiment_id / trial_id`，并能关联 predictions、verification、trajectory 和 summary。
4. 对一个真实 task 对账请求数、input/cache/output/reasoning token、latency、native cost、route_count 和 verifier reward；不要把 route 数、LLM 调用数或 task 数混作一个指标。
5. 检查失败/取消/timeout、verifier 拷贝或执行失败、resume/pass-k 是否会覆盖或误复用旧结果；未知 usage 不得填 0。
6. 核对 `run_tb_eval.sh` 与 SFT launcher 的统一外层启动、原始官方 verifier 和 proxy 传递；正式结果不得使用 `-vfready` task/image。镜像预热由基建负责人完成。

## 最小交付

- 一个真实 task 的 router+worker+callback+verification 逐项对账；
- token、native cost、retry/fallback、pass-k/resume 的覆盖说明；
- 需要修改时，只提交 trial context、日志关联、结果映射或 launcher wrapper 的最小方案，不改 learned policy；
- 一个固定 smoke 的预期日志和验收结果。

## 参考位置

- `/ssd/uno-orchestra/run_tb_eval.sh`
- `/ssd/uno-orchestra/run_tb_unosft.sh`
- `/ssd/uno-orchestra/Uno-Orchestra/eval_pipeline/run.py`
- `/ssd/uno-orchestra/Uno-Orchestra/eval_pipeline/benchmarks/terminalbench.py`
- `/ssd/uno-orchestra/Uno-Orchestra/eval_pipeline/executors/docker_executor.py`
- `/ssd/uno-orchestra/uno_cost_logger.py`
