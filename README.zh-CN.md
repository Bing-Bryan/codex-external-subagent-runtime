# Codex External Subagent Runtime

[English](README.md)

这是一个在普通 Codex 项目任务中常驻生效的编排政策与确定性路由门禁，用于调用自定义原生 Agent、专项 Skill 和 MCP 工具。

它**不是 Agent Skill**。Codex 从全局 `AGENTS.md` 加载受管政策，让 Runtime 在普通项目任务中保持可用；主 Agent 收到用户 prompt 后，自行判断直接完成，还是拆分成有边界的子任务。

```text
用户 prompt
    │
    ▼
主 Agent 分析、拆分
    ├── 直接完成 ───────────────────────────────┐
    └── 需要委派                                │
          │                                     │
          ▼                                     │
      注册表 + 冒烟 + 指纹门禁                  │
          ├── native-agent → 真实子任务卡片     │
          └── skill-tool  → 主任务内工具调用    │
                         │                       │
                         ▼                       │
                  主 Agent 验收并汇总
```

## 它会修改什么

安装器只修改一个有标记的范围：

- `~/.codex/AGENTS.md` 中的 Runtime 受管区块。

它不会修改 `config.toml`、具名 Agent TOML、Provider、MCP、Keychain、全局模型或认证。写入 `AGENTS.md` 前会加锁、备份并原子替换。

用户自己的运行状态独立保存：

```text
~/.codex/project-agent-runtime/
├── routes.json
└── smoke-evidence.json
```

路线选择只读注册表和证据。`runtime-job` 路线会在当前用户的规范化系统临时目录下，
使用 `codex-project-agent-runtime-jobs-UID/` 原子创建权限为 `0600` 的短时任务包，
目录权限固定为 `0700`，并在子 Agent 关闭后删除。这样短时任务不写入 `~/.codex`，
普通项目沙箱无需申请全局写权限。崩溃残留会在下一次 prepare 时回收。Runtime
不安装 Provider、不修配置、不伪造冒烟证据、不设置优先级，也不静默回退。

## 安装

要求：Python 3.9+；运行在 macOS 或其他 POSIX 宿主（Runtime 使用 `fcntl` 和
POSIX 文件权限）；需要使用的原生 Agent、Skill 和 MCP 已由用户配置完成。目前
不支持 Windows。

```bash
git clone https://github.com/Bing-Bryan/codex-external-subagent-runtime.git \
  ~/.codex/tools/codex-project-agent-runtime

cd ~/.codex/tools/codex-project-agent-runtime
python3 scripts/runtime_admin.py plan
python3 scripts/runtime_admin.py install --allow-agents-write
```

`plan` 零写入；`install` 必须显式带写入许可，并会先备份当前全局 `AGENTS.md`。

仅首次配置时复制默认禁用的样例：

```bash
mkdir -p ~/.codex/project-agent-runtime
cp examples/routes.example.json ~/.codex/project-agent-runtime/routes.json
cp examples/smoke-evidence.example.json \
  ~/.codex/project-agent-runtime/smoke-evidence.json
```

已有状态时不要用样例覆盖。

## 宿主 Multi-Agent 版本

本 Runtime 不会切换或强制要求某个全局 Multi-Agent 版本。它运行在普通项目任务
内部，使用当前 Codex 宿主实际提供的原生 Agent 接口：

- V1 宿主暴露 `fork_context=false`；`message` 是标准传输路径，因为有界任务包会
  直接交给新的子 Agent。
- V2 宿主暴露 `fork_turns="none"`；如果外部 Provider 无法读取宿主加密的委派
  Payload，或者任务名不可见，`runtime-job` 是兼容传输路径。
- Skill 与 MCP 路线不属于原生 V1/V2 子 Agent 生命周期；它们始终显示为主任务内
  的工具调用，不伪造 Agent 卡片。

仓库包含 V2 兼容机制，但这不能证明每个外部 Provider 都已在 V2 跑通。每条路线
仍需针对精确 transport 和配置提供新鲜的本机 smoke evidence。Runtime 永远不会
修改 `config.toml` 中的 `multi_agent` 或 `multi_agent_v2`。

## 路线形态

- `native-agent`：调用真实 Codex `agent_type`，界面显示子任务卡片。
- `skill-tool`：调用 `$skill-name` 或精确的 `mcp__server__tool`，界面显示主任务内工具调用。

当前 `native-agent` 的执行提示为宿主提供的原生 `spawn_agent` 能力。

每条路线还必须声明 `transport`：原生 Agent 使用 `message` 或
`runtime-job`，Skill/MCP 使用 `direct`。

用户可以定义任何符合契约的目标。DeepSeek、Kimi、Luna、Grok 只是样例，不是固定支持清单。

只有同时满足以下条件的路线才能执行：

```text
enabled
本机 smoke passed
配置指纹完全一致
证据未过期
```

作者声明和示例不能代替用户本机证据。

`smokeTtlSeconds` 范围为 60—31,536,000 秒；任务包的
`timeoutSeconds` 范围为 1—3,600 秒；大写 `acceptanceMarker` 长度为
3—128 个字符；序列化后的任务包不能超过 65,536 字节。

## 正常运行方式

Runtime 政策始终可用，但不会强制每个 prompt 都调用子 Agent。主 Agent 负责：

1. 理解用户目标；
2. 判断直接完成还是拆分；
3. 为每个子任务匹配能力并通过路由门禁；
4. 传递最小任务包；
5. 等待真实结果并验收；
6. 关闭原生 Agent；
7. 汇总给用户。

每个任务包只包含：

```text
objective
canonicalCwd
inputs
allowedFiles
writePermission
expectedOutput
acceptanceMarker
timeoutSeconds
```

Desktop Runtime 在 `functions.exec` 内把任务包 JSON 编成小写 UTF-8 十六进制，
再以 `--packet-hex PACKET_HEX` 调用门禁；不创建临时输入文件，也不会单独启动
`--packet -` 后误以为内存对象会自动进入 stdin。十六进制只是安全传输编码，不是
加密，任务包仍然不得包含敏感信息。当前 Desktop JavaScript 隔离环境不保证提供
`TextEncoder`，因此常驻策略会先把非 ASCII UTF-16 code unit 转成 JSON
`\uXXXX` 转义，再对得到的 ASCII JSON 编码。

`inputs` 中的绝对文件路径必须被某个明确的 `allowedFiles` 根路径覆盖。
项目外参考文件只有在明确列入 `allowedFiles` 时才允许使用；Runtime 不会
默认把 `allowedFiles` 限定在项目目录内。
规范化绝对路径不会仅因为随机临时目录名或系统路径中的大小写而被误判成高熵密钥；
路径中明确匹配的 Token、Key、凭据赋值或 URL userinfo 仍会被拒绝。

`runtime-job` 原生路线使用同一份已校验任务包：父 Agent 先执行
`prepare-job`，子 Agent 以自己的精确 `task_name` 执行 `read-job`，父 Agent
等待、验收并确认子 Agent 终态后执行 `cleanup-job`。任务名末尾应包含至少 12 位随机
小写十六进制字符（允许 12—32 位）。Desktop 策略固定通过
`python3 -c 'import secrets; print(secrets.token_hex(8))'` 生成 16 位小写十六进制
后缀并校验，避免不同任务互相碰撞。

若 V2 连可读的 `Task name` 也没有提供，子 Agent 改为执行 `claim-job`。只有全局
恰好存在一个有效、未清理的 `runtime-job` 时才会返回任务包；0 个或多个都会
fail-closed。`read-job` 与 `claim-job` 都会用调用者的 `CODEX_THREAD_ID` 哈希原子
登记首次领取者；其他线程无法重领，原始线程 ID 不落盘、不输出。只有创建 job 的
父线程可以清理。
每个 `runtime-job` 要求宿主提供 `CODEX_THREAD_ID`，且父、子工具进程中都必须
可用。缺失或格式错误时以 `job_identity_unavailable` fail-closed。只有真实宿主
smoke 同时验证身份、领取和清理后，该路线才算可用；不得静默改用 `message`。

为保证任务不会串线，外部 `runtime-job` 委派全局串行：从准备 job 到确认子 Agent
终态并清理前，不得并发运行任何其他原生 Agent。Skill/MCP 不读取 job，但也不应在
领取窗口并发启动。若子 Agent 超时后仍未终态，不能提前清理并准备下一份；保留至
TTL 后由下一次门禁清理并明确失败。job 采用原子落盘；私有队列中的半写临时文件、
损坏 job 与非法命名 job 只会在持锁状态下被移除，不会被执行。

不传完整历史、隐藏 reasoning、Key、Token、环境值或配置内容。Spawn 成功不算完成；路线失败时不自动重试、不换 Provider、不静默 fallback。

原生 Agent 不得继承主任务历史；Runtime 使用宿主实际暴露的禁继承参数：V1 为
`fork_context=false`，V2 为 `fork_turns="none"`。`message` 路线直接传完整任务包；
`runtime-job` 路线先按唯一 `task_name` 写入短时任务文件，子 Agent 读取后才执行，
父 Agent 只在等待、验收并确认终态后清理该文件。
短时 `runtime-job` 位于项目外，属于 Runtime 控制面元数据，不是对用户项目的修改。
因此“不要修改项目文件”的只读任务仍可委派；只有用户明确禁止任何本机控制面写入时
才不能使用这类路线。

每个独立委派都创建新的叶子 Agent；每个 `runtime-job` 还必须使用唯一
`task_name`。外部 `runtime-job` 路线禁止
通过 `followup_task` 或 `send_message` 复用已完成 Agent：V2 会加密这些消息，外部
Provider 可能收不到新任务并重复旧结果。

宿主提供 `close_agent` 时，主 Agent 在验收后关闭叶子 Agent。若宿主只提供
`interrupt_agent`，它只能中断当前 Turn，Agent 仍然存在，不能冒充“已关闭”；
此时使用宿主的列表能力验证终态，不向已完成 Agent 排队消息，并允许宿主回收普通
终态 Agent。若新建 Agent 达到线程上限、生命周期异常或无法确认终态，则明确返回
`agent_lifecycle_unavailable`，不自动重试、不静默换路，也不把旧结果当成新结果。

## 安全边界

`router_registry.py` 会确定性校验路线、冒烟证据、配置指纹和任务包。全局 `AGENTS.md` 让政策在新任务中自动出现，但它不是 Codex 宿主层的 prompt 拦截器或安全沙箱：如果某个宿主忽略 `AGENTS.md`，Runtime 无法硬性强迫它调用门禁。

敏感信息模式检测只是纵深防御，不是完整的凭据扫描器。即使未知格式没有被
启发式规则识别，政策仍禁止传递 Key、Token、环境值或凭据文件。

当前 Multi-Agent V2 可能把跨 Provider 的委派消息表示为 `encrypted_content`，
外部 Provider 最终只能看到空白可读 Payload。`runtime-job` 是这一情况的兼容传输：
保留真实原生 Agent 卡片，只通过本机短时文件传递已校验任务包。该文件是本机
明文，不是端到端加密。外部子 Agent 不继承主任务完整上下文；主 Agent 必须把完成
子任务所需且不敏感的信息显式放入八字段任务包。

专项路线继续遵守自身边界，例如前端 Skill 只写获准目录，公开 X 工具保持只读。

## 更新、检查与卸载

```bash
git pull --ff-only
python3 scripts/runtime_admin.py plan
python3 scripts/runtime_admin.py install --allow-agents-write
python3 scripts/runtime_admin.py status
```

只移除受管政策区块：

```bash
python3 scripts/runtime_admin.py uninstall --allow-agents-write
```

卸载不会删除路线状态，也不会修改 Agent、Provider、MCP 或凭据配置。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q scripts tests
git diff --check
```

## 许可证

[MIT](LICENSE)
