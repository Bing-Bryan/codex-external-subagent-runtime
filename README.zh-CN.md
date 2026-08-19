# Codex External Subagent Runtime

[English](README.md)

![Codex External Subagent Runtime 架构概览](docs/runtime-overview.png)

让 GPT 与 Kimi、DeepSeek 等外部模型在同一个 Codex 任务中作为受控子 Agent
协作，不依赖原生 V2 的跨 Provider child support。Grok Build/CLI 等集成同样受
路线门禁约束；未完成模型子任务验收前，按受限工具处理。

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
initialize(capabilities.experimentalApi=true)
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

* 全局预检要求 `[features] multi_agent = true` 且
  `multi_agent_v2 = false`。启用 V2 时，Runtime 会在进入 App Server 前以
  `global_v1_required` 停止，不会切换全局开关，也不会静默降级。
* App Server 请求固定 V1 feature set：先以 `gpt-5.6-luna` 启动任务，再对同一
  任务执行 `gpt-5.6-sol` + `ultra` 切换，并复核最终设置。本仓库没有 V2 专用的
  start、update、read 适配器。

`thread/settings/update` 是实验性的 App Server 接口。Launcher 只为当前连接
启用 experimental API，不会写入全局 Codex 配置。如果当前 bundled CLI 拒绝该
能力，启动会 fail-closed 并返回可恢复的 thread ID；不会增加 bootstrap turn，
也不会静默重试。
* V2 的具体限制在于授权边界。当前 Codex Desktop V2 契约可以编排受支持的
  OpenAI 模型角色，但它的 child task 上下文、控制载荷以及 OpenAI 生成的产物，
  不能可靠地授权给第三方 Provider 消费。GPT 主任务不能安全地把同一份 V2 child
  payload 交给 Kimi、MiniMax 等外部模型；当前表现是反复收到 HTTP 400，或创建出的
  子 Agent 收到空任务。
* V1 绕开了这条边界：由 Sol 显式整理有界 brief，再通过已 allowlist 的 Responses
  route、专用 adapter 或 MCP tool 交付。外部路线不会被伪装成原生 V2 child。
  所以跨 Provider 交付必须留在 V1；这是协议兼容性决策，不是笼统宣称 V2 更差。
* 零 bootstrap turn、精确项目绑定、路线 allowlist 注入和禁止 fallback，全部是
  按 V1 顺序验证的结果。V1 证据不能直接当作 V2 生命周期或路线契约的证据。
* 支持 V2 需要单独实现、单独做 Desktop 验收。在此之前，V2 开启的环境会安全停止，
  不创建语义未经验证的任务。

## 架构 / 终端流程

```text
CODEX EXTERNAL SUBAGENT RUNTIME :: TUI 流程图（简体中文）
范围  已部署后的单次新任务启动 | 不展示安装部署 | 不展开任务运行时路由

┌─ VIEW A / 用户启动旅程（用户可见）─────────────────────────────────────
│
│  前置状态（不属于旅程）
│  Runtime 已安装 | 置顶入口已绑定项目 | Provider 由用户自行配置
│
│  [A] 用户进入项目的置顶入口
│       页面仅显示 ENTRY_READY
│                         │
│                         ▼
│  [B] 用户发送精确小写 new
│       其他输入只返回：ONLY_ACCEPTS_NEW
│                         │
│                         ▼
│  [C] Runtime 自动校验
│       用户无需填写 projectId、cwd、Provider 或模型
│                         │
│              ┌──────────┴──────────┐
│              │                     │
│            通过                  失败
│              │                     └─> 返回短错误；不改配置、不自动重试
│              ▼
│  [D] Desktop 创建并打开新任务
│       Multi-Agent V1 | GPT-5.6 Sol / Ultra | 项目绑定已复核
│                         │
│                         ▼
│  [E] 用户在新任务中输入真实需求
│
└─ 到达状态：用户面对 Sol Ultra；Luna 没有处理真实需求

┌─ VIEW B / Runtime 内部操作逻辑（一次精确 new）────────────────────────────
│
│  [1] 确定性入口门
│      trim(input) == "new"；projectId + canonical cwd 由入口固定
│                         │
│                         ▼
│  [2] 范围与安全预检
│      READ  项目白名单、全局 V1 flags、Codex App CLI 版本、model/list
│      GUARD projectId/cwd 精确绑定、全局启动锁、分阶段超时
│                         │
│                         ▼
│  [3] Provider 路线门禁（只读）
│      读取已有 named-agent / MCP 元数据、registry、smoke evidence
│      enabled + smoke passed + fingerprint match -> 脱敏 allowlist
│      单路线失败只会被拒绝；不修复、不切换、不 silent fallback
│                         │
│                         ▼  固定 developerInstructions
│  [4] App Server / 零 bootstrap turn
│      initialize(capabilities.experimentalApi=true) -> model/list
│      -> thread/start(gpt-5.6-luna, V1, allowlist)
│      -> thread/settings/update(gpt-5.6-sol, ultra)
│      -> thread/read(includeTurns=true)
│      不变量：turn/start = 0 | bootstrapTurns = 0 | fallback = false
│                         │
│                         ▼
│  [5] Desktop 收尾
│      设置标题 -> 定位 threadId -> 复核 projectId/cwd -> 导航
│
└─ 输出：一个已绑定项目、由 GPT-5.6 Sol / Ultra 承接的新 V1 任务

┌─ 正常启动到底改变什么？────────────────────────────────────────────────
│  READ ONLY  config.toml、agents/*.toml、MCP 元数据、registry/evidence
│  CREATE     一个持久化 Codex 任务
│  UPDATE     该任务的模型/effort、脱敏 instructions、标题与导航状态
│  TEMP       launch.lock；退出时删除
│  NEVER      不改全局配置、Agent TOML、MCP、Keychain、CC Switch、凭据
│
│  安装仅放置 Runtime 文件；不属于上面的一次 new 流程。
│  smoke recorder 与显式 config apply 是独立操作，launch 永不调用它们。
└─────────────────────────────────────────────────────────────────────────
```

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
