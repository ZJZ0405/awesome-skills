# awesome-skills

> 一个仓库，带走你所有的 agent 技能。拉到任意一台机器，离线安装到 80 个工具别名（Claude Code、Codex、Cursor、Gemini CLI……）。

这是一个**可离线分发**的个人 skills 收藏：

- **零依赖安装器** — 纯 POSIX shell，不需要 Node、Python、npm 或网络。
- **一次安装，多端生效** — 先装进共享 hub，再按工具软链/复制到各自的 skills 目录。
- **标准格式** — 每个技能是 `skills/<name>/SKILL.md`，与 [Agent Skills](https://agentskills.io) 生态和 `npx skills` 兼容。
- **可审计** — 安装清单、备份目录、`--dry-run`、`doctor`；绝不静默覆盖别人的文件。
- **可搬运** — `scripts/pack.sh` 生成带 SHA-256 的 tar.gz，U 盘/内网拷贝后直接安装。

仓库目前带 2 个原创示例技能，用来演示目录约定和安装链路；把 `skills/` 换成你自己的收藏即可。

## 30 秒上手

**联网机器（打包）**

```sh
git clone git@github.com:ZJZ0405/awesome-skills.git
# 没有 SSH key 时：git clone https://github.com/ZJZ0405/awesome-skills.git
cd awesome-skills
./scripts/pack.sh
# dist/awesome-skills-0.1.0.tar.gz
# dist/awesome-skills-0.1.0.tar.gz.sha256
```

把这两个文件拷到离线机器，然后：

```sh
shasum -a 256 -c awesome-skills-0.1.0.tar.gz.sha256   # Linux 用 sha256sum -c
tar xzf awesome-skills-0.1.0.tar.gz
cd awesome-skills-0.1.0
./install.sh --tool auto
```

`auto` 会探测本机已安装的 agent；想一次铺满所有工具路径，用 `--tool all`。装完重开一个 agent 会话即可。

> 不想打包？直接 `git clone` 后把整个目录拷过去，运行 `./install.sh --tool auto` 也一样。安装器不会联网。

## 常用命令

```sh
./install.sh --tool auto                      # 安装到探测到的工具
./install.sh --tool claude-code,cursor,codex  # 指定工具
./install.sh --tool all                       # 所有支持的目录
./install.sh --skill handoff                  # 只装某个技能
./install.sh --project ~/code/my-app          # 装进项目（.agents/skills + .claude/skills）
./install.sh --copy                           # 不建软链，复制文件
./install.sh --dry-run                        # 只打印计划
./install.sh --force                          # 覆盖非本工具安装的路径（先备份）

./bin/awesome-skills list                     # 仓库里有什么 + 装了什么
./bin/awesome-skills list --available
./bin/awesome-skills tools                    # 80 个工具别名与路径
./bin/awesome-skills doctor                   # 自检：链接、清单、探测
./bin/awesome-skills uninstall --all          # 卸载本工具安装的全部内容
./bin/awesome-skills uninstall --tool cursor  # 只移除某个工具的镜像
```

## 安装到哪些工具

安装器内置 80 个工具别名，覆盖 Claude Code、Codex、Cursor、Gemini CLI、GitHub
Copilot、OpenCode、Cline、Windsurf、Zed、Amp、Kimi Code CLI、Warp、DSH 等。

完整路径表见 [docs/tools.md](docs/tools.md)，或在终端运行：

```sh
./bin/awesome-skills tools
```

| 工具 | `--tool` | 全局 skills 目录 |
| --- | --- | --- |
| Claude Code | `claude-code` | `~/.claude/skills` |
| Codex | `codex` | `~/.agents/skills` |
| Cursor | `cursor` | `~/.cursor/skills` |
| Gemini CLI | `gemini-cli` | `~/.gemini/skills` |
| GitHub Copilot | `github-copilot` | `~/.copilot/skills` |
| OpenCode | `opencode` | `~/.config/opencode/skills` |
| Cline | `cline` | `~/.agents/skills` |
| Windsurf | `windsurf` | `~/.codeium/windsurf/skills` |
| Zed | `zed` | `~/.agents/skills` |
| Amp | `amp` | `~/.config/agents/skills` |
| Kimi Code CLI | `kimi-code-cli` | `~/.agents/skills` |
| Warp | `warp` | `~/.agents/skills` |
| DeepSeek Harness | `dsh` | `~/.agents/skills` |

共享 `~/.agents/skills` 的工具装一次就够；其余工具会从 hub 软链过去。

## 这个仓库里有什么

<!-- catalog:start -->
| 技能 | 用途 | 许可证 |
| :--- | :--- | :--- |
| [`handoff`](skills/handoff/) | Write a compact, source-linked handoff note so another session, agent, or machine can resume the work without re-reading the whole conversation. Use when the… | MIT |
| [`shell-review`](skills/shell-review/) | Review shell scripts for destructive commands, quoting and portability bugs, and offline-install safety before running or shipping them. Use when the user… | MIT |
<!-- catalog:end -->

## 目录结构

```text
awesome-skills/
├── install.sh                 # 入口：install 包装器
├── bin/awesome-skills         # 安装/卸载/列表/自检 CLI（POSIX sh）
├── lib/agents.tsv             # 工具别名 → 项目/全局路径（唯一数据源）
├── skills/                    # 你的收藏：每个技能一个目录
│   └── <name>/
│       ├── SKILL.md           # 必需：YAML frontmatter + 指令
│       ├── references/        # 可选：按需加载的细节
│       └── scripts/           # 可选：可执行辅助脚本
├── scripts/
│   ├── validate.py            # 校验 frontmatter / 链接 / 目录约定
│   ├── catalog.py             # 生成 README 表格、catalog.json、docs/tools.md
│   ├── import.py              # 从本地目录收录技能，记录来源
│   └── pack.sh                # 生成离线 tar.gz + SHA-256
├── templates/skill/SKILL.md   # 新技能模板
├── tests/test-install.sh      # 安装器回归测试
├── docs/                      # 离线部署、工具路径、编写指南
└── catalog.json               # 机器可读的技能目录
```

## 添加自己的技能

**方式 A：从零写**

```sh
mkdir -p skills/my-skill
cp templates/skill/SKILL.md skills/my-skill/SKILL.md
$EDITOR skills/my-skill/SKILL.md
python3 scripts/validate.py
python3 scripts/catalog.py
sh tests/test-install.sh
./install.sh --tool claude-code --skill my-skill
```

**方式 B：收录现成目录**

```sh
python3 scripts/import.py ~/Downloads/some-skill
python3 scripts/import.py ./vendor/some-skill --name my-skill
```

`import.py` 会保留原目录的 `LICENSE`，把来源写进 `sources.json`，并拒绝覆盖同名技能（除非 `--force`）。

只允许小写字母、数字和单个连字符；`name` 必须和目录名一致；`description` 最多 1024 字符。详细规则见 [docs/authoring.md](docs/authoring.md)。

## 工作原理

```text
repo/skills/<name>  ──copy──▶  ~/.agents/skills/<name>        （hub，唯一真实副本）
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
             ~/.claude/skills    ~/.cursor/skills   ~/.config/opencode/skills
                （symlink）         （symlink）          （symlink）
```

- **全局**：hub 是 `~/.agents/skills`；**项目**：hub 是 `<project>/.agents/skills`。
- 读取 `~/.agents/skills` 的工具（Codex、Cline、Zed、Warp、DSH……）不需要镜像。
- 每个技能目录里有 `.awesome-skills-skill` 标记；安装清单在
  `~/.agents/.awesome-skills/manifest.tsv`（项目安装则在 `<project>/.agents/.awesome-skills/`）。
- `--force` 覆盖非本工具安装的路径时，原内容会移到
  `~/.agents/.awesome-skills/backups/<时间戳>/<hash>/`，不会丢失。
- 卸载只删除清单里记录、且带标记或指向 hub 的路径；不认识的文件一律跳过。

## 更新与卸载

```sh
# 更新：拉取新版本后重跑 install。hub 会被原子替换，软链保持不动。
git pull && ./install.sh --tool auto

# 卸载某个工具
./bin/awesome-skills uninstall --tool cursor

# 全部卸载（包括 hub）
./bin/awesome-skills uninstall --all
```

## 离线机器说明

- 安装器全程**不访问网络**，也不调用 `npm`/`pip`。
- 支持 macOS、Linux、WSL、Git Bash。Git Bash/MSYS 下会自动改用 `--copy`。
- 文件系统不支持软链时，先 `./bin/awesome-skills doctor` 看提示，再用 `--copy`。
- 校验包完整性：
  - Linux：`sha256sum -c awesome-skills-0.1.0.tar.gz.sha256`
  - macOS：`shasum -a 256 -c awesome-skills-0.1.0.tar.gz.sha256`
- 没有 Python 也能安装；Python 3 只在**维护仓库**（校验/生成目录/导入）时需要。

更细的搬运流程见 [docs/offline.md](docs/offline.md)。

## 与官方 skills CLI 的关系

仓库布局遵循 Agent Skills 约定，联网机器上也可以直接：

```sh
npx skills add ZJZ0405/awesome-skills
```

离线机器用本仓库的 `install.sh`：它不依赖 Node，并且会维护自己的清单和备份。
两个安装器可以共存；同名技能已存在时，本安装器会拒绝覆盖（除非 `--force`）。

## 安全

- 只安装本地仓库里的内容，没有 `curl | sh`，没有远程清单。
- 非本工具安装的路径默认不动；`--force` 先备份再替换。
- 技能本身是**指令和脚本**，安装前请读一遍 `SKILL.md`，尤其是带 `scripts/` 的技能。
- `doctor` 可以检查断链、缺失和未托管路径。

## License

安装器、脚本和文档使用 [MIT](LICENSE)。每个技能保留自己的许可证，见其
`SKILL.md` 的 `license` 字段和目录内的 `LICENSE` 文件。
