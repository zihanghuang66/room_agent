# Stage 3A v0.4.0 状态与学校验收

日期：2026-11-06。代码已接入真实阶段调用；本地 GPU/Codex 完整重建不可用，学校完整验收待执行。未推送 GitHub。

## 当前完成范围

新建全流程任务使用 `full-scan-graph/1`：

prepare → preflight → ingest → reconstruct → seed → author → publish → export_check

prepare-only 在 prepare 后结束，不调用模型或 GPU。旧 `legacy-full-scan/1` 任务仍使用原适配器，不能将它们的完成误算为新图验收。已有场景复评图继续独立工作。

- LangGraph + SQLite 决定大阶段执行和恢复；直接调用固定应用的阶段函数，不调用 PipelineRunner.run/run_stage，也不读取其 pipeline.json 决定跳过。
- pipeline.json 是兼容状态投影，仅用于报告。本地修改该文件不能改变图的恢复位置。
- 每阶段发布不可变场景快照、校验清单、预览索引和收据；发布完成但检查点尚未提交时，恢复复用已校验收据。
- 捕获输入、配置、执行代码、已发布产物、输入元数据和解析后的运行时路径均核验；删除整个快照也拒绝继续报成功。
- 失败阶段工作目录留存，再从上一成功阶段的快照恢复。已完成阶段不重跑；失败阶段内部可能重复模型调用。这不是家具级或 Python 语句级恢复。
- 子进程终止、超时、编排进程强退均清理本任务后代进程，资源锁保留到清理结束，不操作外部训练任务。
- 基本阶段预览：Ingest 参考图、Reconstruct 已有对象图/GLB、Seed 初始房间输出、Author/Publish/Export check 对照图和场景文件。按阶段分组；没有图片时只显示实际文件，不伪造渲染。
- 返回包保留诊断和展示产物，不打包全部中间恢复快照、原始扫描和账号。返回包不是完整检查点备份；持续恢复仍依赖服务器持久目录。

## 保留的算法与边界

作者、材料、质量和碰撞检查使用原阶段实现、原选项和阈值。Author 内部仍有自己的有限细化流程；本次只迁移大阶段。不会为了编排测试跳过原质量门槛。若 Author 因质量不通过停止，查看节点日志和原验收报告；这不自动代表 LangGraph 出错。

快照增加磁盘占用，按阶段复制场景资产但不复制模型缓存和原始捕获链接目标。写入前检查空间；暂不做内容去重。已中断阶段的工作副本也保留在 work/failed。不要在任务运行或准备恢复时手工清理 published/backend/input。

新图输出位于该 run 的 backend 与 published 子目录。现有保存场景来源选择器主要扫描 return_output/artifacts；新图成果自动进入该来源选择器的桥接尚不包含在本次交付。可先从新图下载查看成果；跨流程版本选择与持续局部修改后续补齐。

## 学校验收步骤

1. 更新服务后 New task → Reconstruct a scan，选择已上传的完整扫描。
2. v0.4.1 起直接点击 Start Reconstruction：同一任务先 Prepare 检查输入，再继续其余阶段，不再单独创建 Check scan 任务。
3. 确认 Prepare 通过后自动继续 Environment。完整重建会调用模型/GPU；不等同于继续旧的已保存场景复评。
4. 检查进度是否依次为 Prepare / Environment / Ingest / Reconstruct / Seed / Author / Publish / Export check。
5. Ingest/Seed 等阶段完成后，任务仍在运行时就应能看到对应阶段的参考图或文件。没有生成图片的阶段不要求凭空出现图片。
6. 若自然遇到网络故障，待网络恢复后 Resume 同一任务，已完成阶段不能重跑。不要为了测试停掉其他训练、整个容器或强制杀 GPU 进程。
7. 下载 Download run bundle 回传：核对 workflow_version、事件顺序、检查点恢复位置、stage_artifacts_published 和最终状态。质量未通过与编排故障分别判断。

真实 Linux /proc 清理、共享盘锁、Blender/TRELLIS/Codex 全链路仍需学校运行证明。本地边界替身测试不计为真实重建通过。

## 本地验证证据

- Python 3.10 新架构完整测试：138 passed；真实 SQLite、HTTP/CLI、阶段恢复、收据发布后故障、输入/元数据/产物篡改、准备目录中断重建、进程清理、队列及网页行为。
- 原流程回归：45 passed，2 skipped；两项因未配置专用真实墙体夹具 ROOM_TEST_CAPTURE 跳过，不计为通过。
- 真实 studio_2573 扫描：59 对帧，完整新图 prepare-only、实际隔离 Python 子进程、快照、终态复用验证通过；未调用模型、GPU或渲染。
- 独立代码审查发现并修复：残留后代进程、未发布解压内容被误复用、整个快照被删除仍误报完成。
- 证据保存在项目 return_output/agent_stage3a_validation。包内启动与清单另行实测；这些测试不替代学校环境验收。

## v0.4.1 界面修订验证

141 项本地测试通过；新单按钮请求、时间排序、Resume 启动后的重新排序、当前选择保持、阶段日志不影响排序、排队通知自动清除已验证。全部 agent 执行 Python 文件与 v0.4.0 原交付逐字节相同，未改变检查点或执行指纹。学校真实任务仍待用户运行。

## v0.4.4 update — retained stage workspace

The earlier description of restoring every failed stage from the prior publication is superseded. Stage retries now retain and checkpoint saved workspace; Author already in acceptance resumes at rebuild/review, with the same bounded repair gate. Results shows successful publications, Diagnostics holds unpassed attempts. Exact release compatibility replaces the blanket version mismatch only for verified v0.4.0–v0.4.3 fingerprints. See [v0.4.4 behavior and limits](ui-v044.md).

## v0.4.5 — closeout update

Supersedes the old automatic Author acceptance loop: retained-source review, manual small batches (max 3), post-cap revalidation, no-progress stop, explicit completed-with-issues and separate draft file verification. Missing evidence is not a repair instruction or a pass. Published backend algorithms remain byte-identical; an explicit application adapter retains validators/thresholds and adds strict per-object evidence coverage. See [closeout-v045.md](closeout-v045.md). Implementation/offline verification does not complete school acceptance.

## v0.4.6 — review recovery validation

Implemented per-group content-bound receipts, conservative import of the interrupted v0.4.5 review, operation-bound orphan activation recovery, 10-object groups with bounded timeout splitting, explicit 600-second review calls, and final Publish review reuse after successful compile/bake. UI distinguishes Review incomplete from quality rejection.

Local regression: 235 tests passed on Python 3.10. The suite includes actual pinned Publish function execution with GPU/model boundaries replaced, real filesystem interruption/restart tests, and SQLite graph recovery. Original backend files and acceptance thresholds are unchanged. The supplied real diagnostic reports (3 x 20 objects) validate against the retained 119-object layout; 90 archived source files matched their copied review snapshot. Full server images and live Codex/GPU still need user verification. Stage 3A server acceptance is not claimed complete by local tests alone.

## v0.4.7 — web Finish closeout

Local full regression: 246 passed on Python 3.10. Reproduced the old failure with explicit tests before changing production code: UI offered Finish during worker finalization; stale queued operations dispatched after CLI completion; web-origin CLI automatically packed full archives; inherited stdout kept worker ownership alive. All regression cases now pass.

The isolated real HTTP test completed Repair -> waiting/finalizing -> Finish -> draft_export -> completed using LangGraph persistence and simulated expensive boundaries, with one Finish and unchanged immutable input. The existing school task remains completed/user_finished; no scene reconstruction or additional quality repair is required for this UI validation. User can run verify_web_finish.sh in the school environment for final environment acceptance. Manual live-school browser confirmation is still pending; no claim that scene quality passed.

### School acceptance received — 2026-11-07

`room_web_finish_check_20261107T135803Z_191667.tar.gz` confirms v0.4.7 package verification, 45 tests passed in 4.36 seconds, exit=0 and WEB_FINISH_CHECK_PASS. Together with the subsequent completed-task diagnostics (no new execution events; unchanged request/assessment/export verification), the web Finish recovery fix is accepted within the isolated HTTP/queue/graph/UI test scope. No real scene/model rerun was performed. The retained scene remains quality not_passed, score 4.5; this does not certify the quality-passed Publish branch.
