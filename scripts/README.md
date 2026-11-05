最新（2026-11-03）：room_003 imagegen 网络失败修复包 `packages/room-litereality-full-v0.2.3.tar.gz`，入口 `litereality-full-v0.2.3/resume_test_03.sh`。复用指定旧工作目录，校验原始数据身份；不附带 Codex、不后台运行。说明见 `litereality-full-v0.2.3/使用说明.md`。以下条目含历史版本。

# 项目脚本

当前入口（2026-11-03）：`packages/room-litereality-full-v0.2.2.tar.gz` → `bash room-litereality-full-v0.2.2/run_test_03.sh`。固定默认输入 data/raw/room_003/test_03.tar，场景 room_003_test_03。仅前台启动，复用已安装 Codex，不使用旧场景数据；用户已放弃 test_01 测试，停止推进其诊断。内部布局 JSON、参考图和小型序列化数据随新返回包收集，顶层原始扫描仍排除。

运行方式约定（2026-11-03）：用户自行启动、管理 tmux。后续启动脚本只做前台运行，不添加 `--background`、自动创建/附着 tmux 会话等逻辑；交付命令也不包含这些步骤。历史版本的后台参数不影响前台用法。

2026-11-03 用户确认容器中的 Codex/code-mode-host 已修复。后续运行包不再附带或自动安装 Codex 运行时，复用 ROOM_CODEX/PATH 中的现有命令。当前使用轻量版 `packages/room-litereality-full-v0.2.1.tar.gz`（745,870 字节），启动 `bash room-litereality-full-v0.2.1/run_test_01.sh --background`，tmux 名称 `room001-test01-v021`。7 项相关本地回归与包完整性检查通过，保留真实命令/MCP 预检。以下 v0.2.0 已被轻量版替代。

2026-11-03 新场景入口：`packages/room-litereality-full-v0.2.0.tar.gz`，解压后 `bash room-litereality-full-v0.2.0/run_test_01.sh --background`。默认读取 `data/raw/room_001/test_01.tar`，独立场景 `room_001_test_01`。包内含完整 Codex CLI 和已修复应用，无须旧 full/resume 脚本目录；容器基础环境仍需现有 LiteReality 镜像。使用说明：`litereality-full-v0.2.0/使用说明.md`。本地回归 17 项、打包后输入检查通过；学校新扫描 GPU 流程待执行。

room_002 v012 已成功完成真实对照渲染，当前是质量门槛未通过并修模超时，不再是 Blender 路径故障。以下为历史记录。

最新回传（2026-11-03，v0.1.1 续跑）：Codex 真实执行已通过，主 author、对象精修、材质和质量阶段已实际完成；最终对照渲染因 Blender 路径被重复追加 /blender 而失败。请使用 scripts/packages/room-litereality-resume-v0.1.2.tar.gz，仅从验收/发布续跑并保留已有编辑。回归检查通过；Ubuntu 主机暂不可用，实际对照渲染未复测。详见 return_output/reconstruction_reports/room_002对照渲染路径报错与验收续跑-2026-11-03.md。

最新回传与修复（2026-11-03）：上次 capture 路径修复已通过，42 个相机载入成功。本次 author 因 Codex 缺少 `codex-code-mode-host` 空跑，适配器又漏判嵌套错误。当前请使用 `scripts/packages/room-litereality-resume-v0.1.1.tar.gz`，内置同版本完整 CLI、失败即停和真实命令/MCP 预检，复用现有资产续跑。离线 Ubuntu 验证通过，学校实际 author/publish 仍待验证。详见 `return_output/reconstruction_reports/room_002续跑报错与修复-2026-11-03.md`。以下旧记录为历史状态。

当前执行入口（2026-11-02 回传之后）：[room_002 修复续跑包](./packages/room-litereality-resume-v0.1.0.tar.gz)、[命令说明](./litereality-resume/使用说明.md)。保留旧完整包和原运行目录，复用已完成的 11 张参考图及 11 个资产，只修复 capture 路径、补齐相机，再继续 author/publish。详见 [中断排查](../return_output/reconstruction_reports/room_002完整流程中断排查-2026-11-02.md)。

当前完整流程入口（2026-11-02）：[运行包](./packages/room-litereality-full-v0.1.0.tar.gz)、[启动与续跑说明](./litereality-full/使用说明.md)、[验收记录](../return_output/environment_reports/room_002完整流程运行包验收-2026-11-02.md)。使用 room_002 raw，参考图由 Codex 内置 imagegen 生成，执行到 publish 及 GLB/BLEND 重开验证。已完成离线运行链路测试，学校实际全场景推理待执行。源码在 litereality-full/，专用构建入口为 `python3 scripts/litereality-full/build.py`，也已加入统一 build_packages.py。

room_002 单家具 imagegen 回传（2026-11-02）：Storage0 的新参考图已保存且目视检查完成；同会话 generated_images 路径和复制校验构成强间接证据，但 JSONL 无直接 imagegen 事件，仍保留 image_saved_tool_unverified。只使用一个输入视角，侧面/底部不可作为真实测量依据。详见 return_output/reconstruction_reports/room_002单家具imagegen评估-2026-11-02.md。

学校 room_002 最新回传（2026-11-02）：无 API 基线 6 阶段全部通过，42/42 帧融合，110.9 万点、约 56.0 万三角面；GLB 本机复读通过。观测 Mesh 有缺口且家具未分割，不能视为最终重建验收通过。单家具 Codex imagegen 真实调用仍待回传。

新增独立测试：[单家具 Codex 内置 imagegen 包](./packages/room-codex-imagegen-check-v0.1.0.tar.gz)，[说明](./codex-imagegen-check/使用说明.md)。使用 room_002 一个家具、一个 Codex 测试会话；无 API 回退。离线选图和模拟进程验证通过，真实内置图像生成待学校执行。

当前执行入口（2026-11-02，后续更新）：[room_002 无图像 API 基线包](packages/room-litereality-noapi-v0.1.0.tar.gz)。执行扫描解析、全帧 RGB-D 点云/TSDF Mesh、原始 RoomPlan 布局的 Blender/GLB 导出与复载验证。已有 Studio_2573 在网络关闭的 r4 容器实测通过，room_002 待学校回传。学校 Codex 容器隔离模式已通过用户回贴的写入/子进程测试；用户表示完整包安装也已处理。以下旧状态保留作历史记录，不再代表当前执行入口。

最新学校回传（2026-11-02）：RTX 3090 CUDA 运算、扩展导入与 nvdiffrast 光栅化内核、Blender CPU/CUDA/EEVEE 均通过；EEVEE 确认为 NVIDIA 硬件渲染。唯一失败项是 Codex 内层 workspace 沙箱，学校的容器隔离执行模式和模型流程待测。用户选择在学校当前容器就地修复并保存镜像，r5 本地打包已按要求停止。详见[学校验收报告](../return_output/environment_reports/学校环境验收-2026-11-02.md)。 16:18 复测已确认 Codex 登录，其他检查状态不变；容器隔离执行模式仍待学校对照测试。

当前工作入口（2026-11-02）：r4 成品位于 `return_output/images/room-litereality-agent-0.1.3-cuda124-r4.docker.tar.gz`，本地 SHA256 已验证。它修复学校平台常见启动方式下的 SSH 兼容问题，并通过 22 项 SSH 与离线 CPU 验收。学校端实际连接待确认。

参见[r4 修复报告](../return_output/environment_reports/SSH平台兼容修复-r4-2026-11-02.md)、[SSH 修复源码](./litereality-image/ssh-platform-r4/README.md)、[复建包](./packages/room-litereality-image-0.1.3-cuda124-r4-ssh-fix.tar.gz)。学校导入成品无需运行构建脚本；如果可设置启动命令，使用 `/usr/local/bin/start-ssh.sh`。项目仍挂载 `/yinxin/projects/room`。

Codex 容器隔离模式命令执行器已验证，模型与完整场景尚未验证，见[对照报告](../return_output/environment_reports/Codex容器执行模式对照-2026-11-01.md)。此前单文件 Dockerfile、r2/r3 镜像与记录保留作历史参考。

当前 Codex 综合验证包：[`room-codex-integration-check-v0.1.2.tar.gz`](./packages/room-codex-integration-check-v0.1.2.tar.gz)，[完整说明](./codex-integration-check/使用说明.md)。独立执行文本/视觉/MCP/文件读写/Blender 链路检查，写入失败自动加入临时目录对照，统一回传。
补充实测：r3 临时容器中，Codex 的 `:danger-full-access` 执行器在保留 Docker 默认隔离设置下写文件和启动子进程通过；workspace 模式仍无法创建 namespace。未调用模型、未改镜像默认配置，详见[模式对照报告](../return_output/environment_reports/Codex容器执行模式对照-2026-11-01.md)。


Blender 缺 EGL 的修复与复测：[`blender-egl-repair/使用说明.md`](./blender-egl-repair/使用说明.md)，上传包 [`room-blender-egl-repair-v0.1.0.tar.gz`](./packages/room-blender-egl-repair-v0.1.0.tar.gz)。

LiteReality Codex 适配：[`litereality-codex/使用说明.md`](./litereality-codex/使用说明.md)。上传包为 [`room-litereality-codex-v0.1.1.tar.gz`](./packages/room-litereality-codex-v0.1.1.tar.gz)，含启动脚本；先验证 Codex 看图、JSON、MCP 与写文件能力，不启动完整重建。原始 LiteReality 代码保留，图像生成 API 仍需单独配置。

代码、依赖清单和可上传的脚本包统一保存在本目录。

| 路径 | 用途 |
| --- | --- |
| [build_packages.py](./build_packages.py) | 统一生成上传包及 SHA256，固定归档时间 |
| [environment-check/使用说明.md](./environment-check/使用说明.md) | 服务器环境检查脚本 |
| [reconstruction-setup/使用说明.md](./reconstruction-setup/使用说明.md) | 独立重建环境安装与导入检查 |
| [sparse-batch/使用说明.md](./sparse-batch/使用说明.md) | ZIP/视频检查、抽帧、SIFT、顺序匹配、稀疏重建与回传 |
| [vggt-omega/使用说明.md](./vggt-omega/使用说明.md) | VGGT-Ω 独立环境、一次运行预检、50 帧推理及回传 |
| [packages/room-vggt-omega-v0.3.0.tar.gz](./packages/room-vggt-omega-v0.3.0.tar.gz) | 每秒 2 帧、100 帧联合推理；已有环境无需重装 |
| [packages/room-environment-check-v0.1.2.tar.gz](./packages/room-environment-check-v0.1.2.tar.gz) | 可上传的环境检查包 |
| [packages/room-reconstruction-setup-v0.1.2.tar.gz](./packages/room-reconstruction-setup-v0.1.2.tar.gz) | 可上传的依赖安装包 |
| [packages/room-sparse-batch-v0.1.2.tar.gz](./packages/room-sparse-batch-v0.1.2.tar.gz) | 修复 FFprobe 4.4 兼容性的稀疏重建包 |

服务器代码目录固定为 `/yinxin/projects/room/scripts`。可以将本地 `scripts/` 的内容上传到此目录，保持 `environment-check/` 和 `reconstruction-setup/` 子目录结构。

`packages/` 同时保存上传包和 SHA256 校验文件。若使用压缩包，将新版压缩包上传到服务器的 `scripts/` 后解压，所得子目录与直接上传源码一致。运行产物、明细报告、日志、下载缓存及回传压缩包统一放在项目的 `return_output/` 下。已有运行环境 `.venvs/` 保持原位，避免路径失效。

所有后续脚本的回传压缩包统一写入 `/yinxin/projects/room/return_output/`，由脚本自动创建目录，终端继续输出 `Return this archive:` 和完整路径。使用 `--project-root` 指定其他项目目录时，回传路径为该目录下的 `return_output/`。明细日志位于 `return_output/` 内各自的报告目录。旧输出可在任务停止后通过 `sparse-batch/organize_outputs.py` 整理。

安装命令：

```bash
/usr/bin/python3 /yinxin/projects/room/scripts/reconstruction-setup/setup.py
```

当前已提供 `sparse-batch` 执行脚本，具体命令见其使用说明；依赖已安装成功时无需重复安装。稠密重建与 Mesh 尚未实现。

## 上传包生成规则

以后统一使用下列命令生成上传包，不直接继承本机文件时间：

```bash
python3 scripts/build_packages.py
```

从项目根目录执行；脚本也支持从其他目录通过绝对路径调用。新增上传包时将文件清单加入 `build_packages.py` 的 `PACKAGES`。

修复版上传包必须使用新版本文件名，在 `PACKAGE_VERSIONS` 中递增版本，不再用同一文件名发布不同内容。当前稀疏重建修复版为 `room-sparse-batch-v0.1.2.tar.gz`；旧的无版本包不再作为当前下载入口。压缩包内仍为 `sparse-batch/`，解压后更新该目录，Python 运行路径不变。

tar 成员的 mtime 和 gzip 头时间均固定为 Unix epoch（0），不保留 PAX 的 mtime/atime/ctime；中文文件名保留。这样即使打包机器的时钟比学校服务器快，也不会因源文件时间在未来而产生解压警告。只规范压缩包元数据，不改动源文件内容或时间，也不修改服务器时钟。

## 当前诊断包

[room-reconstruction-diagnostics-v0.1.0.tar.gz](./packages/room-reconstruction-diagnostics-v0.1.0.tar.gz)：采集最近一次重建的全部抽帧、数据库和模型，供本地排查；不重新安装或使用 GPU。[完整命令与说明](./reconstruction-diagnostics/使用说明.md)。所有新增输出继续位于 `return_output/`。

运行约定更新：后续启动脚本必须包含在压缩包内，用户上传一个压缩包、解压后直接运行。VGGT-Ω 入口为 `vggt-omega/run_room.sh`；不再要求额外上传包外启动脚本，也不要求用户拼接 Python 参数。


后续运行脚本统一约定：终端显示每项状态与可复制的关键错误，同时保存完整日志并生成 return_output 下的返回 tar；不能只显示 failed 或只把错误藏在日志中。终端错误须先脱敏，注明检查项/阶段，结束打印返回压缩包完整路径。长错误可截短显示，但完整诊断保留到 tar。当前综合包 v0.1.2 已实现此约定。

当前修复包：[room-codex-integration-check-v0.1.3.tar.gz](./packages/room-codex-integration-check-v0.1.3.tar.gz)，合并适配器 v0.1.2。顶层为 scripts/，解压需 --strip-components=1；[完整命令](./codex-integration-check/使用说明.md)。加入直接沙箱预检及依赖跳过，修复事件丢失、恢复判定与失败补丁误报。

本轮入口更新：room-codex-integration-check-v0.1.4.tar.gz，解压后运行 codex-integration-check/run_runtime.sh。修复现代 Codex sandbox 必需 profile 参数；区分参数错误与系统拒绝；深入检查并条件修复已有 NVIDIA EGL 库路径。已通过的文字/视觉/MCP 不再重复调用。
