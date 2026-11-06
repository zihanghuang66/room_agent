# 迁移状态与阶段自检

更新时间：2026-11-06。工作分支：`feat/langgraph-foundation`。本阶段仅本地实现与验证，没有推送 GitHub。

| 阶段 | 状态 | 完成判据 |
| --- | --- | --- |
| 1 状态与恢复基础 | **通过本地验收** | 持久化、跨进程恢复、输入/产物完整性、房间隔离、CLI、原有回归 |
| 2 真实保存场景闭环与最小网页 | **已交付，待服务器验收** | 详见 phase2-status.md；学校 Codex/渲染全链路未验证 |
| 3 全流程与网页 | 未开始 | 多房间调度、前端预览、断线/重启及完整输入流程 |
| 4 持续交互 | 未开始 | 对话驱动的版本选择、局部修改、回退与下游失效 |

## 本阶段实际交付

- 独立 `room-agent` 包，CLI 与应用服务分离；现有完整流程入口、官方源码与提示词未修改。
- 真正的 LangGraph + SQLite，按 run 保存检查点；完成输入快照 → 源码语法审计 → 等待确认 → 结束/取消审计。
- room_id、run_id、revision_id 分离；每房间写锁、每任务运行状态；事件有序且原子写入。
- 请求和产物 hash 核验；损坏或版本不兼容时拒绝继续；源码不执行。
- 依赖版本与分发文件 hash 锁定；可安装 CLI 已实测启动。

## 验收证据

| 验证 | 实际结果 |
| --- | --- |
| 新架构测试 | **23 passed**，1.88 秒 |
| 现有当前流程回归 | **45 passed，2 skipped**，1.24 秒 |
| 强退恢复 | 子进程在快照发布后立即退出（91），新进程恢复并复用同一快照 |
| 节点失败恢复 | 审计读取失败后恢复，snapshot 仅执行一次，audit 重试 |
| 数据完整性 | 修改、删除、添加产物文件均拒绝恢复；已完成后删除整个版本也拒绝再次报成功 |
| 状态权威 | 手改 result.json 为 completed/passed 不改变真实检查点状态 |
| 多房间 | 同房间并发拒绝；另一房间可执行；新运行不会把旧中断运行显示成运行中 |
| 真实 room_004 源码 | 276 文件，145,097,992 字节（138.38 MiB），8 个 Python 文件语法通过 |
| 真实跨进程确认 | run `d65bbf756e6f433d80d4815b6ec72824`：waiting_for_input → CLI 新进程 resume → completed |

两个旧测试跳过原因：未设置 `ROOM_TEST_CAPTURE`，缺少真实 walls.pkl / layout_shell.json 回归夹具。没有将它们计为通过。

上述 room_004 操作始终是 `saved_source_audit`，`quality_status=unreviewed`，`build=not_run`，`render=not_run`。

本地完整证据存放在项目的 `return_output/agent_foundation_validation/`：`foundation-tests.txt`、`legacy-tests.txt`、`source_audit.json` 和 `state/`。这些文件及场景快照不进入 Git。

复测命令（从仓库根目录；PYTHON 指向相应独立环境）：

```bash
python -m pytest -q -rs scripts/room-agent/tests
python -m pytest -q -rs scripts/tests/test_batch_review.py scripts/tests/test_batch_integration.py scripts/tests/test_wall_facade.py scripts/tests/test_wall_resume.py scripts/tests/test_source_checkout.py
```

## 自检发现并修复的问题

1. 已完成运行若整个产物目录被删除，原实现会跳过核验。先增加失败测试，再修复为只要检查点引用产物就必须核验。
2. 用房间写锁判断运行状态，会把同房间的旧中断任务误报为运行中。先复现，再引入独立 run 活动锁区分两者。

## 下一关卡与当前限制

下一阶段只接入**已有保存场景的构建、渲染、独立评审和小批次修复**，不重新 ingest/reconstruct。先为旧执行器定义输入、输出、失败及产物指纹契约，再将调用隔离到子进程，随后在学校环境验证恢复和真实输出。

本机通过不能替代学校 GPU 环境验证；共享盘 SQLite/文件锁、容器重建后的自动调度、GPU 中断恢复、网页和视觉质量均未验证。当前没有执行超时控制或完整 GPU 作业管理，不允许将源码审计节点直接当成 GPU 工作进程。

完成阶段 2 前，旧入口仍是实际生产路径。每阶段必须更新这份记录，记录实际通过、失败与跳过，不能仅依据代码数量推进阶段。
