# Room Agent — room_004 当前运行代码

本仓库只保留 room_004 所需的最新完整流程、已有结果的分批评审/修复、打包入口和相关测试。历史排查包、旧版本、场景专用建模示例均不在当前目录树中。

## 在学校现有容器里运行

前提：使用已准备好的 LiteReality 容器环境，包含 `/opt/room/envs/app`、GPU 依赖、Blender、TRELLIS 和已登录且网络可用的 Codex。预检要求可见 `cuda:0` 空闲显存至少 18 GiB，不会停止其他任务。本仓库不包含容器镜像、扫描、模型权重或账号信息。

原始扫描位置：`/yinxin/projects/room/data/raw/room_004/test_04.tar`。

首次下载并完整运行：

```bash
cd /yinxin/projects/room/scripts
git clone https://github.com/zihanghuang66/room_agent.git
cd room_agent
bash scripts/run_room_004.sh
```

如果已经克隆：

```bash
cd /yinxin/projects/room/scripts/room_agent
git pull --ff-only
bash scripts/run_room_004.sh
```

启动脚本先从源码生成校验一致的运行包，再执行 room_004 全流程。运行在前台，由使用者自行管理 tmux。所有场景产物、诊断与返回 tar 写入 `/yinxin/projects/room/return_output/`，错误同时显示在终端。

不要为了更新代码重复启动已经在运行的同一任务。需要继续已有运行时，使用下面的显式续跑入口。

## 继续已有结果

完整流程的断点续跑（将路径替换为服务器上确实存在的运行目录）：

```bash
bash scripts/run_room_004.sh resume /yinxin/projects/room/return_output/artifacts/你的运行目录
```

仅对已保存场景重新构建和独立评审，不重新 ingest/reconstruct：

```bash
bash scripts/run_room_004.sh review /yinxin/projects/room/return_output/artifacts/你的运行目录 --max-batches 0
```

对已保存结果进行最多 3 批修复，每批编辑预算 900 秒：

```bash
bash scripts/run_room_004.sh review /yinxin/projects/room/return_output/artifacts/你的运行目录 --max-batches 3 --repair-seconds 900
```

续跑和评审都需要完整服务器工作目录及原始 capture，Mac 返回的精简 tar 不能替代它。脚本核对应用版本及输入，不会自动猜选“最新目录”。`quality_not_passed`、`repair_timeout` 与技术执行错误分别报告。

## 目录

| 路径 | 用途 |
| --- | --- |
| `scripts/run_room_004.sh` | 统一完整运行、续跑和评审入口 |
| `scripts/build_room_004.py` | 从源码重新生成两个当前运行包 |
| `scripts/litereality-full-v0.3.1/` | 当前完整流程，包含通用墙体碰撞修复 |
| `scripts/litereality-batch-review-v0.1.0/` | 当前分批保存、重建及独立评审流程 |
| `scripts/build_litereality_batch_review.py` | 分批评审包构建器 |
| `scripts/tests/` | 当前流程的回归与源码交付测试 |

两个 `app/` 是各自独立交付所需的同一审定应用源码，保留上游许可证和运行提示词。它们不是两个历史版本；应用指纹保持一致，供已有结果的兼容性检查使用。

## 只打包或检查输入

```bash
bash scripts/run_room_004.sh build
bash scripts/run_room_004.sh full --prepare-only
```

生成的上传包在 `scripts/packages/`，不会提交 Git；应用负载也从仓库中的 `app/` 重建，不依赖旧 tar。归档时间统一固定，避免“文件时间在未来”的解压提示。

`--prepare-only` 只验证/解压输入，不表示 GPU、模型、渲染或视觉验收通过。

## 本地回归测试

```bash
python3 -m venv /tmp/room-agent-tests
/tmp/room-agent-tests/bin/pip install -r scripts/requirements-test.txt
python3 scripts/build_room_004.py
/tmp/room-agent-tests/bin/python -m pytest -q scripts/tests
```

测试涵盖打包校验、输入启动、迁移续跑、墙体碰撞及分批修复控制。两项原始场景回归需额外设置 `ROOM_TEST_CAPTURE` 指向含 `walls.pkl` 和 `layout_shell.json` 的服务器回传 scene_data 目录；未提供时明确跳过。其他测试不需要扫描或旧脚本。

## 结果与范围

本仓库提供完整流程所需的代码，运行仍依赖上述现有容器、扫描、模型下载和 Codex 服务。此轮清理只做离线回归及干净源码副本验证，没有重新在学校 GPU 上跑一遍。

已有 room_004 独立视觉评审仍是 4.5/10，不能把运行和导出完成写成质量通过。评审渲染器覆盖部分光照/色彩设置的问题尚未修复；本次没有改动重建算法或质量门槛。
