# v0.8.1：规划格式一致性修复

## 根因

v0.8.0 的提示词与应用校验要求计划包含 target_ids、finding_ids，但 planner_cli.py 传给 Codex 的 --output-schema 没有这两个字段，并设置 additionalProperties=false。遵守 CLI 格式的计划必然缺字段，最终触发 Plan must explicitly preserve target and evidence bindings。普通问答的 plan=null 不受影响。

上版模拟规划器测试直接返回含字段的字典，绕过了 CLI 输出格式，因而漏掉跨层冲突。不是用户回答错误，也不是登录、网络或场景重建错误。

## 修复

- ui/planner_contract.py 集中定义结构化输出格式和计划字段；CLI 与应用校验共同引用。
- 模拟遵守实际 --output-schema 的传输，再走真实 plan() 和 validate()，复现并防止格式冲突。保留对象/证据真实性检查，不以自动填空绕过校验。
- 规划角色区分用户意图与可由源码检查的技术事实。缺材质分配信息时可先提出条件计划，确认后由既有只读检查核验；用户同意不等于能力证据。
- 已经检查证实的能力缺口不能靠一句“可以”解除，必须有可行方法或相关范围/证据变化，避免反复提交相同失败方案。

## 保留范围

执行 Worker、Skill 内容及其摘要、渲染/验收条件和检查点语义与 v0.8.0 相同。没有启动重建、改写旧任务或改变现有成果。

本地测试覆盖格式传递和规划指引，真实 Codex 是否正确解释具体场景仍需服务器验证。
