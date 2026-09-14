# SGAR local research distribution

这是从当前 Windows 工作区逐字节筛选的普通内容目录，**尚未创建 Git 仓库，也未在 Linux 验证**。

保留生产流程：Planner → Profiler/检索与冻结 → Compiler → 执行/恢复 → 评价 → 提交 → 正式交付，以及现有科研运行和结果检查入口。生产源码、提示、资源和索引没有改写。

请先阅读 [Linux 迁移说明](docs/LINUX_MIGRATION.md)。其中列明必须的外置资产、环境版本差异、历史准入资料的适用边界和手动启动方法。当前不是无前置条件的一键运行包。

```text
sgar_mvp/
  main.py                    单任务正式入口
  real_case_batch.py         科研批处理入口
  src/                       完整生产实现（含协议、提示、评价和校验）
  config/                    原有非敏感策略、注册和运行声明
  config.example.json        原有模板（原字节）
  config.linux.example.json  本轮路径模板，保留当前运行准备策略
  docker/                    原有运行镜像构建定义
  scripts/                   必要资源维护、准入与研究入口
Pool/
  resources/                 完整当前资源定义和执行包
  index_meta/                当前实际索引及映射
requirements-*.txt           原项目依赖声明，非 Linux 已验证锁
.env.example                凭据变量示例，无真实密钥
tests/fixtures/              独立公开样例判分器所需的最小输入
docs/                       迁移说明、观察环境、第三方来源说明
```

根工程回归测试、历史专项测试、旧运行和维护材料已实际排除。资源包自身的 test 文件、判分器和 Agent 准入脚本直接引用的少量公开 fixture 保留，不能将它们等同于历史测试垃圾。

最近任务曾完成执行与正式交付，但人工发现任务转换偏差和内部评价漏判。该事实保留；本次整理未修正语义，也不证明独立任务正确或所有未来运行稳定。外部 benchmark 仍须使用其自己的独立检测器。

第三方内容见 [来源说明](docs/third-party-resources.md) 和资源包原有 LICENSE/NOTICE。来源目录没有项目根 LICENSE，本轮没有代为授予新的许可证；公开上传前由项目所有者确认发布权限。
