# Spatial Agent v0.10.2

当前交付包含对话规划、确认后执行、场景证据查看、同一 Workspace 内的局部编辑和独立质量复评，以及持久队列、检查点和诊断下载。

## 启动

使用完整交付包的 `start_server.sh`；不要直接把源码目录当成服务器运行包。完整命令见 [使用说明](delivery/使用说明.md)。打包需先在仓库根目录执行：

```bash
python3 scripts/build_room_004.py
python3 scripts/room-agent/delivery/build_delivery.py
```

要求 Python 3.10+。LangGraph 编排环境与 Blender/TRELLIS 应用环境分离。默认持久状态目录为 `/yinxin/projects/room/return_output/agent_state_stage2_v020`。

## 分层

| 层 | 作用 |
| --- | --- |
| `ui/` | 对话意图、计划确认、证据绑定、会话内操作和展示 |
| `src/room_agent/*_graph.py` | 扫描、保存场景评审及编辑的 LangGraph 节点和恢复 |
| `*_executor.py` / `*_driver.py` | 有界子进程、旧后端适配、构建、渲染和模型调用 |
| `skills/` | 选图/评审及编辑规则，实际注入相应模型会话 |
| SQLite、事件和 receipts | 持久检查点、执行审计、已完成产物校验与复用 |

会话 ID、执行 run ID 与当前保留版本分开管理。独立复评产生报告但不替换当前场景；编辑候选仅按明确接受规则提升。原始扫描 ingest/reconstruct 不因复评或局部编辑重复执行。

## 当前版本与验证

见 [v0.10.2 发布记录](docs/release-v0102.md) 和 [修复细节](docs/patch-v0102.md)。学校独立复评已验证，质量不通过与执行失败分别报告。旧完成报告不被新代码偷偷改写。

安装锁定测试依赖后可执行：

```bash
python -m pytest -q scripts/room-agent/tests
```

测试覆盖真实 LangGraph/SQLite/HTTP/队列，模型和渲染使用测试适配器。检查点可以复用已完成节点，但不能恢复正在运行的 Blender/TRELLIS 内部状态。执行前验证源文件、产物和版本兼容性。

## 范围与限制

- 当前为持久串行队列，不包含多 GPU 调度；学校共享文件系统仍须符合数据库和文件锁要求。
- 全扫描内部部分步骤仍使用审定的 legacy backend；不是所有内部动作都拆成独立图节点。
- 尚未交付自动连续修复、对话新相机补图、对话创建新扫描、长期对话摘要或历史版本回退。
- 复评标准保持 score >= 8、无问题和实际对象全覆盖。更多视角不保证遮挡对象可验证，也不保证质量通过。
- 历史阶段文档是当时记录，当前状态以本发布说明为准。
