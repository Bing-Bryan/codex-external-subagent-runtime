# 项目内自定义 Multi-Agent Runtime

- 本 Runtime 始终生效，不需要 Skill 激活词，也不要求用户记住命令或触发词。
- 收到用户 prompt 后，先判断由主 Agent 直接完成，还是拆成有边界的子任务；无需委派时由主 Agent 直接完成。
- 主 Agent 负责分析、拆分、选路、下发、等待、验收和汇总。Runtime 不预设 Provider 优先级，也不强迫每个 prompt 使用子 Agent。

## 确定性路由门禁

- 调用任何自定义 Agent、专项 Skill 或 MCP 前，先运行：
  `python3 "{{RUNTIME_ROOT}}/scripts/router_registry.py" resolve --capability CAPABILITY`
- 用户明确选择路线时才增加 `--route-id ROUTE_ID`；否则只允许唯一符合条件的路线通过。
- 只有 `enabled + 本机 smoke passed + 配置指纹一致 + 证据未过期` 的路线可以执行。
- `route_ambiguous` 时让用户选择；其他错误立即报告。禁止静默 fallback，禁止自动重试，禁止运行中修改或修复注册表。

## 最小任务包

- 每次委派只传以下字段，不传完整历史、隐藏 reasoning、Key、Token、环境值或配置内容：
  `objective`、`canonicalCwd`、`inputs`、`allowedFiles`、`writePermission`、`expectedOutput`、`acceptanceMarker`、`timeoutSeconds`。
- 在 `functions.exec` 的 JavaScript 中构造任务包对象。当前隔离环境不保证提供 `TextEncoder`，先把 JSON 的非 ASCII UTF-16 code unit 转成 JSON `\uXXXX` 转义，再生成只含小写十六进制字符的 ASCII/UTF-8 传输值：
  ```javascript
  const packetJsonAscii = JSON.stringify(packet).replace(
    /[^\u0000-\u007f]/g,
    character => "\\u" + character.charCodeAt(0).toString(16).padStart(4, "0")
  );
  const packetHex = Array.from(
    packetJsonAscii,
    character => character.charCodeAt(0).toString(16).padStart(2, "0")
  ).join("");
  ```
- 不创建临时输入文件，不把原始 JSON 插入 shell。`packetHex` 必须匹配 `^[0-9a-f]+$`，再作为单独参数传给门禁；十六进制只是安全传输编码，不是加密，任务包仍禁止包含敏感信息。
- `message` 或 `direct` 路线使用：
  `python3 "{{RUNTIME_ROOT}}/scripts/router_registry.py" validate-packet --packet-hex PACKET_HEX --route-write-mode ROUTE_WRITE_MODE`
- 不得单独启动 `--packet -` 后期待 JavaScript 变量自动进入 stdin。
- 任务包校验失败时不得调用路线；写权限不得超过路线声明的 `writeMode`。
- `inputs` 中的绝对文件路径必须由 `allowedFiles` 明确覆盖；项目外参考文件也必须显式列入。
- 规范化绝对路径不会仅因随机目录名或系统路径中的大小写而被当成高熵密钥；路径中明确匹配的 Key/Token 签名仍会被拒绝。敏感信息检测只是纵深防御；无论启发式规则是否识别，均禁止传递 Key、Token、环境值或凭据文件。

## 两种真实执行界面

- `native-agent`：使用返回的真实 `agent_type`，保持叶子节点；界面显示真实子任务卡片。
- 原生 Agent 不得继承主任务历史；按宿主实际暴露的参数设置：V1 使用 `fork_context=false`，V2 使用 `fork_turns="none"`。若宿主没有等价的禁继承参数，返回 `agent_lifecycle_unavailable`，不得猜测参数名。
- 按路线返回的 `transport` 下发：
  - `transport=message`：把完整八字段任务包放进非空 `message`；这是当前 V1 的标准路径。
  - `transport=runtime-job`：先单独运行 `python3 -c 'import secrets; print(secrets.token_hex(8))'`，严格校验输出匹配 `^[0-9a-f]{16}$`，再生成 `TARGET_` 加该后缀的唯一 `task_name`；直接运行 `prepare-job --task-name TASK_NAME --route-id ROUTE_ID --capability CAPABILITY --packet-hex PACKET_HEX`。`prepare-job` 本身完成任务包门禁，成功后才用同一 `task_name` spawn。Multi-Agent V2 的 `encrypted_content` 不算外部 Agent 已收到任务。
- 如果你是收到空白或不可读 NEW_TASK Payload 的子 Agent，必须从该消息的 `Task name` 取最后一段，并先运行：
  `python3 "{{RUNTIME_ROOT}}/scripts/router_registry.py" read-job --task-name TASK_NAME`
  如果消息连 `Task name` 也没有，则运行：
  `python3 "{{RUNTIME_ROOT}}/scripts/router_registry.py" claim-job`
  `read-job` 和 `claim-job` 都会用当前工具进程的 `CODEX_THREAD_ID` 哈希原子登记首次领取者；同一子线程可重复读取，其他线程只会得到 `job_already_claimed`。原始线程 ID 不写入 job，也不输出。`claim-job` 只有在全局恰好存在一个有效、未清理的 `runtime-job` 时才返回任务包；0 个或多个都会 fail-closed。只有成功读到八字段任务包后才能执行；失败则原样报告短错误，不猜任务。
- 父 Agent 在等待、验收并确认子 Agent 终态后必须运行 `cleanup-job --task-name TASK_NAME`；helper 只允许创建该 job 的父线程清理。任务文件权限固定为 `0600`、默认 300 秒过期，禁止写入凭据。
- 同一时刻全局只允许一个未清理的 `runtime-job`。从准备该 job 到确认子 Agent 终态并清理前，不得并发运行任何其他原生 Agent；下一次原生委派只能在上一次清理后开始。Skill/MCP 路线不读取 `runtime-job`，但也不要与领取窗口并发启动。
- 若子 Agent 超时后仍未终态，不得提前清理并准备下一份 job；保留到 TTL，由下次门禁清理，然后报告 `agent_lifecycle_unavailable`。helper 使用原子落盘，并在持锁状态下移除私有队列中的半写临时文件、损坏 job 与非法命名 job，绝不执行其内容。
- 这些位于当前用户规范化系统临时目录中的短时 `runtime-job` 文件属于 Runtime 控制面元数据，不写 `~/.codex`，也不算修改用户项目；用户要求“不要修改项目文件”时仍可用于只读委派。只有用户明确禁止任何本机控制面写入时才不得创建。
- `skill-tool`：返回 `$skill-name` 时显式调用该专项 Skill；返回 `mcp__server__tool` 时调用该 MCP；界面显示主任务内工具调用。
- 调用 `$kimi-code-frontend` / `run_frontend_with_kimi_code` 前，必须先满足它自己的非幂等参数契约：`requestId` 必须是规范 UUID，可用 `python3 -c 'import uuid; print(uuid.uuid4())'` 单独生成并校验；不得把临时目录后缀冒充 UUID。`allowedWriteRoots` 只接受注册项目 `cwd` 内的相对路径，禁止传绝对路径或项目外系统临时目录。需要一次性演示时，只能使用项目根目录内唯一、窄范围的相对目录并在独立验收后清理；若用户禁止任何临时项目写入，则不得调用这个写入型 MCP。参数校验失败算本次失败，禁止改参自动重试。
- 如果用户仅验证 MCP 界面与连通性，使用零写入 transport canary：任务明确禁止调用工具和修改文件，只要求精确返回验收标记；`allowedWriteRoots` 仍传一个唯一的项目内相对占位目录，但不得要求创建文件，主 Agent 必须确认该目录从未出现。这只证明 MCP/Kimi 消息交付，不证明写入能力。真正的前端实现不得用 canary 冒充完成。
- 不把 Skill/MCP 包装成假 Agent，不伪造子任务卡片。

## 并发与验收

- 延续全局并发约束：默认最多 3 个子 Agent、硬上限 5；同一工作区最多 1 个写入执行器。由于 V2 可能同时隐藏 Payload 和 `Task name`，存在 `runtime-job` 时不得并发运行任何其他原生 Agent。
- spawn 成功不算完成。主 Agent 必须等待真实结果、核对输出与 `acceptanceMarker`，并独立验收。
- 每个独立委派都创建一个新的叶子 Agent；每个 `runtime-job` 还必须使用新的唯一 `task_name`。对外部 `runtime-job` 路线，禁止用 `followup_task` 或 `send_message` 复用已完成 Agent：V2 会加密这些消息，外部 Provider 可能看不到新任务并回放旧结果。
- 若宿主暴露 `close_agent`，完成或失败后调用它。若宿主只暴露 `interrupt_agent`，它只能中断当前 Turn 且 Agent 仍保留，不能冒充关闭；此时用宿主的列表能力验证终态，不向已完成 Agent 排队消息，并允许宿主回收普通终态 Agent。
- 新建 Agent 前必须遵守默认并发与硬上限；若 spawn 返回线程上限、生命周期异常或无法确认终态，明确报告 `agent_lifecycle_unavailable`，禁止自动重试、静默 fallback 或把旧结果当成新结果。
- `runtime-job` 仅在子 Agent 已进入终态时清理，无论最终验收成功或失败；非终态超时不得清理，保留至 TTL。专项 Skill/MCP 失败时直接报告，不换路。
