# Codex External Subagent Runtime

[English](README.md)

这是一个面向 Codex Desktop 的 Bootstrap、Launcher 和 Runtime Route
Contract 独立仓库。

Runtime 使用 GPT-5.6 Luna 创建项目绑定的 Multi-Agent V1 任务，再在不产生
bootstrap turn 的情况下切换到 GPT-5.6 Sol Ultra，并且只注入经过本机验证的
脱敏路线白名单。仓库不提供 Provider 凭据、Endpoint、Adapter、私有 MCP 实现，
也不会自动修复配置。

## 安装与置顶入口

将仓库 clone 到稳定路径，并把代码与运行状态分开：

```bash
git clone https://github.com/Bing-Bryan/codex-external-subagent-runtime.git
cd codex-external-subagent-runtime
python3 scripts/pinned_entry.py --ready
```

在 Codex Desktop 中配置一个固定 project ID 与 canonical cwd 的 pinned entry，
并参考 [`templates/pinned-entry.developer-instructions.md`](templates/pinned-entry.developer-instructions.md)。
入口负责传入固定值，用户消息不得携带或覆盖它们。

入口协议是字面匹配的：

* 初始化只输出 `ENTRY_READY`；
* 只有 trim 后精确等于小写 `new` 才能通过；
* 其他输入只输出 `ONLY_ACCEPTS_NEW`；
* 活跃 launch lock 直接失败，不静默重试。

## Runtime 状态

用户状态位于仓库外的 `$CODEX_HOME/codex-external-subagent-runtime/`：

* `projects.json`，参考 `examples/projects.example.json`；
* `providers.json`，参考 `examples/providers.example.json`；
* `smoke-evidence.json`，只在用户批准并完成真实 smoke test 后写入。

只验证 registry 结构，不调用 Provider：

```bash
python3 scripts/validate_registry.py
```

默认采用 `adopt-existing`：只读取已有 named-agent 与 MCP 元数据的必要结构，
计算 fingerprint，不读取凭据，不修复、覆盖或切换配置。

## 启动契约

App Server 顺序固定为：

```text
initialize
model/list
thread/start(model=gpt-5.6-luna, V1, developerInstructions=allowlist)
thread/settings/update(model=gpt-5.6-sol, effort=ultra)
thread/read(includeTurns=true)
```

不发送 Luna prompt、Sol handoff prompt 或 `turn/start`。成功必须同时满足 V1、
零 bootstrap turn、Sol/Ultra 设置已验证、项目绑定准确以及 thread ID 为 UUID。
这里将 Multi-Agent V1 作为兼容性边界；V2 的限制见下一节。

启动成功后，再使用 Desktop 控件设置标题、定位返回的 thread、复核 project ID
与 canonical cwd，全部通过后才导航。

## 为什么当前方案基于 Multi-Agent V1

选择 V1 是因为当前实现和测试只覆盖了完整的 V1 启动与验收链路：

* 全局预检要求 `[features] multi_agent = true` 且
  `multi_agent_v2 = false`。启用 V2 时，Runtime 会在进入 App Server 前以
  `global_v1_required` 停止，不会切换全局开关，也不会静默降级。
* App Server 请求固定 V1 feature set：先以 `gpt-5.6-luna` 启动任务，再对同一
  任务执行 `gpt-5.6-sol` + `ultra` 切换，并复核最终设置。本仓库没有 V2 专用的
  start、update、read 适配器。
* 对跨 Provider 场景，当前 V2 契约本质上是面向 OpenAI 自家模型栈封闭定制的
  高级协议，不能提供本 Runtime 所需的 child task 语义。GPT 主控与 Kimi、MiniMax
  等外部模型混用时必须使用 V1；在 V2 下运行这类组合，当前表现通常是反复收到
  HTTP 400，或创建出的子 Agent 收到空任务。
* 零 bootstrap turn、精确项目绑定、路线 allowlist 注入和禁止 fallback，全部是
  按 V1 顺序验证的结果。V1 证据不能直接当作 V2 生命周期或路线契约的证据。
* 支持 V2 需要单独实现、单独做 Desktop 验收。在此之前，V2 开启的环境会安全停止，
  不创建语义未经验证的任务。

## 路由契约

启用前阅读 [`docs/provider-routing.md`](docs/provider-routing.md)。路线只有在以下
三项同时成立时才会暴露：

1. registry 中 `enabled` 为 true；
2. 本机 evidence 记录了目标 delivery kind 的 passed 结果；
3. 当前配置 fingerprint 与记录值完全一致。

支持的类别是 `responses-direct`、`responses-adapter-dedicated` 和有界只读的
`mcp-tool`。即使工具背后使用 CLI 或 SDK，也仍然是工具而不是 child model。
没有 Provider 优先级、自动修复、共享代理切换或静默 fallback。

每次真实外部 smoke call 都要单独获得用户确认。Recorder 只记录已经观察到的
结果，不执行调用：

```bash
python3 scripts/record_smoke_evidence.py \
  --provider-id PROVIDER_ID \
  --confirm-observed-delivery
```

## 配置助手

`scripts/runtime_config.py plan` 只读。`apply` 是独立、显式授权的整文件操作，
必须同时提供新鲜 plan SHA 与 `--allow-global-config-write`。launch 和 smoke
recording 永远不会调用它，测试使用临时 `CODEX_HOME`。

## 文档

* [`docs/architecture.en.txt`](docs/architecture.en.txt) — 英文终端流程。
* [`docs/architecture.zh-CN.txt`](docs/architecture.zh-CN.txt) — 中文终端流程。
* [`docs/provider-routing.md`](docs/provider-routing.md) — 路线与证据边界。
* [`MIGRATION.md`](MIGRATION.md) — 从原 monorepo 拆出的迁移说明。

## 测试

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q scripts tests
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
```

CI 覆盖 Python 3.9 与 3.11。静态测试和 fake App Server 不能证明真实 Desktop
项目绑定或 Provider delivery；二者仍是独立验收门。

## 许可证

[MIT](LICENSE)
