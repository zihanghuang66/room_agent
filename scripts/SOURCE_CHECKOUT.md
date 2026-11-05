# Room Agent 源码快照 — 2026-11-05

本仓库只保存源码、运行配置、提示词、许可证及使用说明。不上传扫描、模型权重、Blender/GLB、纹理、日志、Docker 镜像或交付压缩包。本地 `data/`、`return_output/` 和所有大文件继续保留在磁盘上。

## 当前入口

- `litereality-full-v0.3.1/`：完整重建流程与通用墙体碰撞修复。
- `litereality-batch-review-v0.1.0/`：基于保存版本重建、独立评审和分批修复；区分质量未通过与修复超时。
- `scenes/room_004_round_03/`：本次 room_004 最终保存版本的 Room.py 和对象生成代码，不包含场景资产。
- `render_bed_by_window.py`：读取外部 `.blend`，将床向窗台移动约 20 cm，保存独立版本及同角度前后渲染。
- `recheck_window_depth.py`、`inspect_bed_layout.py`、`tests/measure_room004.py`：模型尺寸核查。
- `tests/`：各阶段回归测试。部分集成测试需本地回传报告、Blender、GPU 或服务器环境，不能视为全离线测试。

历史版本保留用于复现旧回传和修复；不代表全部都是当前推荐入口。各版本内的 `app/` 是保留许可证的 LiteReality-Agent 源码副本。

## 源码克隆后的压缩包

压缩包不会上传 Git。完整流程包可以从源码生成：

```bash
python3 scripts/litereality-full-v0.3.1/build.py
```

批次评审的打包入口是 `scripts/build_litereality_batch_review.py`，但需要先从该版本的 `app/` 重新生成其 `app.tar.gz` 和 `app.sha256`；不要将其他版本的压缩包混入。各版本的校验文件记录当时交付包，重打包后须同步更新。

模型渲染脚本需要自行提供原场景 `.blend`；家具生成还依赖外部模型和纹理。源码仓库不构成含数据、权重和运行环境的一键复现包。

## 本版本已知限制

room_004 的最终独立视觉评审仍未通过（4.5/10），运行和导出完成不能等同质量达标。评审渲染器覆盖部分光照/色彩设置的问题已定位，但本次源码归档没有修复它。

当前飘窗台面进深 0.62 m 是模型几何和生成代码的核查值，不能据此证明实地尺寸。床头较床身宽；床头贴窗台时床身侧仍有约 8.5 cm 缝隙。

提交本快照不代表重新跑过完整学校 GPU 流程。
