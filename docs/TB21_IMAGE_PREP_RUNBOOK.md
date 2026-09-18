# TB2.1 镜像准备操作手册

脚本：`/home/zhoutianle/Projects/SGAR_research_base/scripts/tb21_image_prep.py`

默认输入：

```text
/ssd/zhoutianle/sgar-benchmarks/terminalbench21/official_tb21/terminal-bench-2-1
```

默认状态目录：

```text
/ssd/zhoutianle/sgar-benchmarks/terminalbench21/preflight
```

脚本不会删除镜像，不会重拉已经存在的镜像，并拒绝 `-vfready` 镜像。下载分为两步：

1. `sgar-net-run skopeo copy` 下载为临时 Docker archive，使 registry 请求明确经过实验代理；
2. 使用服务器上的新版 `docker load` 导入本地 daemon。

不能直接使用当前 `skopeo 1.4.1` 的 `docker-daemon:` transport，因为它使用 Docker API
1.22，而服务器 daemon 要求最低 API 1.44。成功导入后临时 archive 会自动删除；下载成功但
导入失败时会保留 archive，下次运行直接重试导入。

## 1. 生成清单

```bash
cd /home/zhoutianle/Projects/SGAR_research_base
/ssd/zhoutianle/envs/sgar/bin/python scripts/tb21_image_prep.py inventory
```

预期看到 `tasks=89 unique_images=89`。

## 2. 检查当前缺失量

```bash
/ssd/zhoutianle/envs/sgar/bin/python scripts/tb21_image_prep.py status
```

主要输出文件：

```text
inventory.json
images.tsv
images.txt
status.json
present_images.txt
missing_images.txt
```

## 3. 先做 dry-run

```bash
/ssd/zhoutianle/envs/sgar/bin/python scripts/tb21_image_prep.py pull \
  --limit 2 \
  --jobs 1 \
  --dry-run
```

该命令只显示队列并写 dry-run journal，不下载镜像。

## 4. 前台拉取两个镜像作为 canary

```bash
/ssd/zhoutianle/envs/sgar/bin/python scripts/tb21_image_prep.py pull \
  --limit 2 \
  --jobs 1
```

完成后重新执行 `status`，确认 present 增加、missing 减少。每个镜像的完整输出位于：

```text
/ssd/zhoutianle/sgar-benchmarks/terminalbench21/preflight/logs/
```

## 5. 后台拉取剩余镜像

推荐并发度为 2：

```bash
cd /home/zhoutianle/Projects/SGAR_research_base
nohup /ssd/zhoutianle/envs/sgar/bin/python -u \
  scripts/tb21_image_prep.py pull \
  --jobs 2 \
  > /ssd/zhoutianle/sgar-benchmarks/terminalbench21/preflight/pull.nohup.log \
  2>&1 &
echo $! > /ssd/zhoutianle/sgar-benchmarks/terminalbench21/preflight/pull.pid
```

查看进度：

```bash
tail -f /ssd/zhoutianle/sgar-benchmarks/terminalbench21/preflight/pull.nohup.log
```

查看进程：

```bash
ps -fp "$(cat /ssd/zhoutianle/sgar-benchmarks/terminalbench21/preflight/pull.pid)"
```

脚本有进程锁，重复启动第二个 pull 会直接退出。若进程中断，重新执行相同 `pull`
命令即可；本地已经存在的镜像会自动跳过。

## 6. 最终校验

```bash
/ssd/zhoutianle/envs/sgar/bin/python scripts/tb21_image_prep.py verify
```

通过条件：

```text
present=89
missing=0
wrong_platform=0
```

最终证据位于：

```text
status.json
verification.json
pull_results.jsonl
logs/*.log
```

## 注意事项

- 不要运行 `docker system prune`、`docker image prune` 或删除共享镜像。
- 不要把 `--jobs` 提高到 4 以上；建议保持 2，降低 registry 限流和磁盘竞争。
- 每个并发任务会暂时在 `preflight/archives/` 保存一个 archive；建议额外预留约 10–20 GB 临时空间。
- 不要把 `-vfready` 镜像写入正式清单。
- 不需要批量构建 task Dockerfile；TB2.1 的 89 个 task 都声明了固定官方镜像。
- 镜像准备完成后，Codex、SWE-Agent、Terminus-2 等方法在容器内安装自身依赖的行为仍需分别预检或经代理执行。
