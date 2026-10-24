# 支持的工具与安装路径

`lib/agents.tsv` 是唯一数据源；下面的表由 `python3 scripts/catalog.py` 生成。
路径表参考 [vercel-labs/skills](https://github.com/vercel-labs/skills) 的
`docs/agents.md`（MIT），并补充了 DeepSeek Harness 等条目。

```sh
./bin/awesome-skills tools              # 全部 80 个别名
./bin/awesome-skills tools --detected   # 只列本机探测到的
./bin/awesome-skills tools --tool claude-code,cursor
```

约定：

- **项目路径**相对于项目根，**全局路径**默认相对于 `$HOME`。
- 全局 hub 是 `~/.agents/skills`，项目 hub 是 `<project>/.agents/skills`。
- 目标目录等于 hub 的工具不会创建额外镜像，装一次就够。
- `N/A` 表示该工具不支持这个作用域（例如 Eve 只有项目级路径）。
- 自动探测：`cmd:` 前缀检查 PATH 里的可执行文件，其余检查路径是否存在。
  自动探测不到时可以显式 `--tool` 强制安装。
- 新增工具：在 `lib/agents.tsv` 加一行（别名、项目目录、全局目录、探测方式），
  然后运行 `sh tests/test-install.sh`。

## 配置目录被环境变量改过怎么办

`lib/agents.tsv` 的单元格可以写 `${NAME}` 或 `${NAME:-默认值}`。安装器在**安装时**
按当前环境解析，所以把 agent 的配置目录用环境变量挪走之后，镜像会跟着落到位：

| 环境变量 | 影响的行 | 说明 |
| --- | --- | --- |
| `CLAUDE_CONFIG_DIR` | `claude-code` | 镜像装进 `$CLAUDE_CONFIG_DIR/skills`，不再是 `~/.claude/skills` |
| `XDG_CONFIG_HOME` | `crush`、`goose`、`opencode` | 基目录从 `~/.config` 换成 `$XDG_CONFIG_HOME` |
| `CODEX_HOME` | `codex`（仅探测） | Codex 的全局 skills 是共享的 `~/.agents/skills`，只有探测路径跟随 |

变量未设置或为空时回落到默认值（`${VAR:-默认}` 的语义）。**没有**接变量的工具
（`amp`、`kimchi`、`devin`、`replit`、`gemini-cli` 等）仍按写死的 `~/...` 安装；
如果你用的工具在官方文档里声明了自己的配置目录变量，把它写进对应单元格即可
（只加在你确认过语义的工具上，不同工具的变量含义并不一致，有的是替换基目录，
有的是替换整个 `$HOME`）。模板不支持嵌套，一个单元格里可以有多个 `${...}`。

安装器只在解析后的路径上建链接，所以 `./bin/awesome-skills tools` 会显示**解析后**
的目录，方便确认变量是否生效。

镜像只在 `install` 时创建，所以改完变量后旧位置的链接不会自动搬走。想清理干净：
先 `./bin/awesome-skills uninstall --all`（按清单里记录的绝对路径删除），再重新
`install`；清单按「技能 + 路径」去重，重复安装不会越堆越多。

## 兼容性

所有列出的工具都支持基本的 `SKILL.md`（`name` + `description` + 正文）。
扩展字段的支持并不一致：

| 字段 | 说明 |
| --- | --- |
| `allowed-tools` | 多数工具支持，但 Kiro CLI、Zencoder 等会忽略；不要把它当安全边界。 |
| `context: fork` / hooks | 仅 Claude Code 等少数工具支持；其他工具会忽略，技能需要能在没有它们时工作。 |
| `metadata` | 通用；`internal: true` 会让本仓库的目录生成器跳过该技能。 |

本仓库自带的技能只使用通用字段。从别处收录技能时，如果它依赖某个
agent 的专有功能，请在 `description` 里写明适用范围。

注意：

- 这些工具对 skills 的支持需要较新的版本；目录建好了但 agent 不识别时，先升级工具。
- 同一个技能不要同时装全局和项目版本：不同 agent 的优先级不同，容易出现
  “改了没生效”的错觉。需要项目级覆盖时，先 `uninstall --tool ...` 再装到项目。
- 自动探测是启发式的：GUI 工具可能还没有配置文件。用 `--tool` 显式指定最稳。

<!-- tools:start -->
| 别名 | 项目目录 | 全局目录 |
| --- | --- | --- |
| `aider-desk` | `.aider-desk/skills` | `~/.aider-desk/skills` |
| `amp` | `.agents/skills` | `~/.config/agents/skills` |
| `replit` | `.agents/skills` | `~/.config/agents/skills` |
| `universal` | `.agents/skills` | `~/.config/agents/skills` |
| `antigravity` | `.agents/skills` | `~/.gemini/antigravity/skills` |
| `antigravity-cli` | `.agents/skills` | `~/.gemini/antigravity-cli/skills` |
| `astrbot` | `data/skills` | `~/.astrbot/data/skills` |
| `autohand-code` | `.autohand/skills` | `~/.autohand/skills` |
| `augment` | `.augment/skills` | `~/.augment/skills` |
| `bob` | `.bob/skills` | `~/.bob/skills` |
| `claude-code` | `.claude/skills` | `${CLAUDE_CONFIG_DIR:-~/.claude}/skills` |
| `openclaw` | `skills` | `~/.openclaw/skills` |
| `cline` | `.agents/skills` | `~/.agents/skills` |
| `codex` | `.agents/skills` | `~/.agents/skills` |
| `dexto` | `.agents/skills` | `~/.agents/skills` |
| `fx` | `.agents/skills` | `~/.agents/skills` |
| `kimi-code-cli` | `.agents/skills` | `~/.agents/skills` |
| `loaf` | `.agents/skills` | `~/.agents/skills` |
| `pi` | `.agents/skills` | `~/.agents/skills` |
| `sarvam-code` | `.agents/skills` | `~/.agents/skills` |
| `warp` | `.agents/skills` | `~/.agents/skills` |
| `zed` | `.agents/skills` | `~/.agents/skills` |
| `dsh` | `.agents/skills` | `~/.agents/skills` |
| `codearts-agent` | `.codeartsdoer/skills` | `~/.codeartsdoer/skills` |
| `codebuddy` | `.codebuddy/skills` | `~/.codebuddy/skills` |
| `codemaker` | `.codemaker/skills` | `~/.codemaker/skills` |
| `codestudio` | `.codestudio/skills` | `~/.codestudio/skills` |
| `command-code` | `.commandcode/skills` | `~/.commandcode/skills` |
| `continue` | `.continue/skills` | `~/.continue/skills` |
| `cortex` | `.cortex/skills` | `~/.snowflake/cortex/skills` |
| `crush` | `.crush/skills` | `${XDG_CONFIG_HOME:-~/.config}/crush/skills` |
| `cursor` | `.agents/skills` | `~/.cursor/skills` |
| `deepagents` | `.agents/skills` | `~/.deepagents/agent/skills` |
| `devin` | `.devin/skills` | `~/.config/devin/skills` |
| `droid` | `.agents/skills` | `~/.factory/skills` |
| `eve` | `agent/skills` | — |
| `firebender` | `.agents/skills` | `~/.firebender/skills` |
| `forgecode` | `.forge/skills` | `~/.forge/skills` |
| `gemini-cli` | `.agents/skills` | `~/.gemini/skills` |
| `github-copilot` | `.agents/skills` | `~/.copilot/skills` |
| `goose` | `.goose/skills` | `${XDG_CONFIG_HOME:-~/.config}/goose/skills` |
| `grok` | `.grok/skills` | `~/.grok/skills` |
| `hermes-agent` | `.hermes/skills` | `~/.hermes/skills` |
| `inference-sh` | `.inferencesh/skills` | `~/.inferencesh/skills` |
| `jazz` | `.jazz/skills` | `~/.jazz/skills` |
| `junie` | `.junie/skills` | `~/.junie/skills` |
| `iflow-cli` | `.iflow/skills` | `~/.iflow/skills` |
| `kilo` | `.agents/skills` | `~/.kilo/skills` |
| `kimchi` | `.kimchi/skills` | `~/.config/kimchi/harness/skills` |
| `kiro-cli` | `.kiro/skills` | `~/.kiro/skills` |
| `kode` | `.kode/skills` | `~/.kode/skills` |
| `lingma` | `.lingma/skills` | `~/.lingma/skills` |
| `mcpjam` | `.mcpjam/skills` | `~/.mcpjam/skills` |
| `minimax-code` | `.minimax/skills` | `~/.minimax/skills` |
| `mistral-vibe` | `.vibe/skills` | `~/.vibe/skills` |
| `moxby` | `.moxby/skills` | `~/.moxby/skills` |
| `mux` | `.mux/skills` | `~/.mux/skills` |
| `opencode` | `.agents/skills` | `${XDG_CONFIG_HOME:-~/.config}/opencode/skills` |
| `openhands` | `.openhands/skills` | `~/.openhands/skills` |
| `ona` | `.ona/skills` | `~/.ona/skills` |
| `posit-assistant` | `.posit/assistant/skills` | `~/.posit/assistant/skills` |
| `qoder` | `.qoder/skills` | `~/.qoder/skills` |
| `qoder-cn` | `.qoder/skills` | `~/.qoder-cn/skills` |
| `qwen-code` | `.qwen/skills` | `~/.qwen/skills` |
| `reasonix` | `.reasonix/skills` | `~/.reasonix/skills` |
| `rovodev` | `.rovodev/skills` | `~/.rovodev/skills` |
| `roo` | `.roo/skills` | `~/.roo/skills` |
| `tabnine-cli` | `.tabnine/agent/skills` | `~/.tabnine/agent/skills` |
| `terramind` | `.terramind/skills` | `~/.terramind/skills` |
| `tinycloud` | `.tinycloud/skills` | `~/.tinycloud/skills` |
| `trae` | `.trae/skills` | `~/.trae/skills` |
| `trae-cn` | `.trae/skills` | `~/.trae-cn/skills` |
| `windsurf` | `.windsurf/skills` | `~/.codeium/windsurf/skills` |
| `zcode` | `.zcode/skills` | `~/.zcode/skills` |
| `zencoder` | `.zencoder/skills` | `~/.zencoder/skills` |
| `zenflow` | `.zencoder/skills` | `~/.zencoder/skills` |
| `neovate` | `.neovate/skills` | `~/.neovate/skills` |
| `pochi` | `.pochi/skills` | `~/.pochi/skills` |
| `promptscript` | `.agents/skills` | — |
| `adal` | `.adal/skills` | `~/.adal/skills` |
<!-- tools:end -->
