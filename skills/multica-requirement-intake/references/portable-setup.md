# 可移植安装与首次配置

## 1. Skill 目录

优先把完整目录放在跨工具共享位置：

```text
~/.agents/skills/multica-requirement-intake/
```

目录必须至少包含：

```text
multica-requirement-intake/
├── SKILL.md
├── agents/openai.yaml
└── references/
    ├── operating-manual.md
    ├── portable-setup.md
    └── requirement-template.md
```

如果工具没有扫描共享目录，可使用其原生位置：

- Codex：`${CODEX_HOME}/skills/multica-requirement-intake/`；未设置 `CODEX_HOME` 时通常是 `~/.codex/skills/`。
- OpenCode：`~/.config/opencode/skills/multica-requirement-intake/`，也支持 `~/.agents/skills/`。
- Multica Runtime：可在 Multica 的 Settings → Skills 中导入，再附加到需要使用它的 Agent；Multica 会写入 provider 对应目录。

不要只复制 `SKILL.md`。引用文档也是执行规则的一部分。

## 2. CLI 发现顺序

Skill 按以下顺序寻找 CLI：

1. 用户在当前请求中给出的路径。
2. `MULTICA_BIN` 环境变量。
3. `multica` / `multica.exe` 的 `PATH` 命令。
4. Multica Desktop 在当前用户 application-data 中管理的 CLI。
5. Multica Desktop 安装目录中打包的 CLI。

常见候选位置只能用于有限探测，不能当作固定路径：

- Windows managed：`%APPDATA%\Multica\bin\multica.exe`
- Windows bundled：`%LOCALAPPDATA%\Programs\<Multica 安装目录>\resources\app.asar.unpacked\resources\bin\multica.exe`
- macOS managed：`~/Library/Application Support/Multica/bin/multica`
- macOS bundled：`/Applications/Multica.app/Contents/Resources/app.asar.unpacked/resources/bin/multica`
- Linux managed：`~/.config/Multica/bin/multica`
- Linux bundled：只在 Multica 的安装目录或当前 AppImage 挂载目录内查找 `resources/app.asar.unpacked/resources/bin/multica`

不要递归扫描整个系统盘。

候选文件必须通过只读版本检查：

```text
multica version --output json
```

如果 CLI 不可用但 Agent 能控制已登录的 Multica 浏览器，可以走 UI。两者都不可用时只能生成草稿。

## 3. 可选环境变量

这些变量只保存于各电脑本地，不应写回 Skill：

| 变量 | 用途 |
|---|---|
| `MULTICA_BIN` | 指定当前电脑的 Multica CLI 完整路径 |
| `MULTICA_REQUIREMENT_PROFILE` | 指定 CLI profile；不设置时使用默认 profile |
| `MULTICA_WORKSPACE_ID` | 指定当前 workspace，避免多 workspace 歧义 |
| `MULTICA_REQUIREMENT_SQUAD` | 覆盖默认小队名称“开发交付小队” |

不要在这些变量或 Skill 文件中保存 PAT、OAuth Token 或密码。认证由 Multica CLI 自己的配置管理。

## 4. 新电脑首次验证

按顺序执行只读检查：

1. CLI 版本可读取。
2. `config show` 指向预期服务。
3. `workspace list --output json` 能看到目标 workspace。
4. 选定 workspace 后，`squad list --output json` 中存在唯一目标小队。
5. `squad member list <squad-id> --output json` 中存在队长，并且恰好有一个 `member_type=member`、`role=人工审批人` 的成员。
6. `project list --output json` 能看到目标项目。

首次验证不得创建测试 issue，除非用户明确要求。

## 5. 同 workspace 与新 workspace

### 换电脑，但仍使用同一个 workspace

登录同一账号或有权限的账号即可。项目、小队、roster、issue 和审批记录都在 Multica 服务端，Skill 会重新解析它们的 UUID。

### 使用新的 workspace

复制 Skill 不会复制服务器端小队。新 workspace 必须先准备一支符合以下契约的小队：

- 有队长；
- 顶层需求分配给小队；
- 内部角色和 Plan/Implementation 工作流已经配置；
- roster 中恰好有一个成员角色为“人工审批人”；
- 队长会从 roster 自动取得审批人 UUID。

若这些条件不满足，Skill 必须停止创建或启动需求并报告配置问题。

## 6. Profile 与 workspace 选择

- profile 由当前电脑的 CLI 配置决定，不能从旧电脑复制名称后盲用。
- 默认 profile 未配置时，可以只枚举 `~/.multica/profiles/` 的子目录名，并逐个运行 `multica --profile <name> config show`。不要直接读取或输出 `config.json`，其中可能包含 Token。
- 有默认 workspace 时仍应读取并确认它。
- 有多个 workspace 且用户未指定时，必须询问，不能根据旧 UUID 猜测。
- 不应为了执行一次需求创建而修改全局默认 workspace；优先为命令显式传入 workspace ID。

## 7. 打包检查

移动到另一台电脑前，确认包内没有：

- 绝对用户目录；
- 具体 profile 名；
- workspace、squad、project、agent 或 member UUID；
- Token、Cookie、私钥或登录配置；
- 临时 issue 描述和日志文件。

不要把 `~/.multica/` 目录打进 Skill 压缩包。
