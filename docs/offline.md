# 离线部署指南

目标：在**没有网络、没有 Node、没有 Python** 的机器上，把仓库里的技能装进
Claude Code、Codex、Cursor、Gemini CLI、Copilot、OpenCode 等 agent。

`installer/` 里的 Python 安装器是可选实现（需要 Python 3.9+）；本文的主流程
只使用零依赖的 `bin/awesome-skills`。

## 1. 在联网机器上打包

```sh
git clone git@github.com:ZJZ0405/awesome-skills.git
cd awesome-skills
git log -1 --oneline          # 记下版本，方便回滚
./scripts/pack.sh             # 生成 dist/awesome-skills-<version>.tar.gz
```

产物：

```text
dist/awesome-skills-0.2.0.tar.gz
dist/awesome-skills-0.2.0.tar.gz.sha256
```

`pack.sh` 会排除 `.git`、`dist/`、缓存和本机 `.agents/`，不会把开发环境带过去。
加 `--zip` 可以额外生成 zip（给只能解压 zip 的机器）。

## 2. 传输

U 盘、内网共享、scp 都可以。**两个文件一起带**：压缩包和 `.sha256`。
如果中转介质会改写文件（邮件、聊天软件），到离线机器后校验通常会失败——请用
U 盘或 `scp`。

## 3. 在离线机器上校验并解压

Linux：

```sh
sha256sum -c awesome-skills-0.2.0.tar.gz.sha256
tar xzf awesome-skills-0.2.0.tar.gz
cd awesome-skills-0.2.0
```

macOS：

```sh
shasum -a 256 -c awesome-skills-0.2.0.tar.gz.sha256
tar xzf awesome-skills-0.2.0.tar.gz
cd awesome-skills-0.2.0
```

没有 `sha256sum`/`shasum` 时，至少确认文件大小与来源，并在可信介质上传输。

## 4. 先看再装

```sh
./bin/awesome-skills list --available     # 仓库里有哪些技能
./bin/awesome-skills tools --detected     # 本机探测到哪些 agent
./install.sh --tool auto --dry-run        # 只打印计划，不落盘
```

技能是**给 agent 的指令**，带 `scripts/` 的技能还可能执行命令。安装前至少读一遍
`skills/<name>/SKILL.md`，确认没有意外的网络访问或删除操作。

## 5. 安装

```sh
./install.sh --tool auto          # 推荐：探测到的工具
# 或
./install.sh --tool all           # 所有支持的目录（会创建很多空目录）
./install.sh --tool claude-code,cursor,codex
```

安装器做三件事：

1. 把技能复制到 hub：`~/.agents/skills/<name>`（项目安装则是
   `<project>/.agents/skills`）。
2. 给使用独立目录的工具建软链，例如 `~/.claude/skills/<name>`。
3. 在 `~/.agents/.awesome-skills/manifest.tsv` 记录每个路径。

装完**重开 agent 会话**（很多工具只在启动时扫描 skills）。

## 6. 验证

```sh
./bin/awesome-skills list --installed
./bin/awesome-skills doctor
ls -l ~/.claude/skills ~/.cursor/skills       # 软链应指向 ~/.agents/skills/<name>
```

在 agent 里直接问一个会触发技能的问题，例如 “帮我写个交接文档” 或
“review 一下这个 shell 脚本”。

## 7. 项目级安装

只想让某个仓库使用这些技能：

```sh
cd ~/code/my-app
/path/to/awesome-skills/install.sh --project . --tool claude-code,codex
```

会得到 `<project>/.agents/skills/`（hub）、`<project>/.claude/skills/`（镜像）
和 `<project>/.agents/.awesome-skills/manifest.tsv`。项目级安装适合跟着仓库走，
不影响全局环境。

## 8. 更新

离线更新：带新的 tar.gz 过来，解压后重跑同一个命令即可。

```sh
./install.sh --tool auto
```

hub 中的技能会被替换，软链保持不动；清单按“技能 + 路径”去重，不会重复记录。
如果新版本改动了技能目录结构，旧文件不会残留，因为 hub 是整体替换的。

## 9. 回滚 / 卸载

```sh
./bin/awesome-skills uninstall --tool cursor   # 只移除 Cursor 的镜像
./bin/awesome-skills uninstall --all           # 移除本工具装过的全部路径
```

`--force` 覆盖过的旧内容在：

```text
~/.agents/.awesome-skills/backups/<时间戳>/<hash>/
```

需要回滚时把对应目录移回原位即可。

## 10. Windows

推荐 WSL（与 Linux 一致）。Git Bash 也能运行，但 Windows 文件系统对符号链接的
支持取决于权限；安装器在 MSYS/MinGW 环境会自动改用复制模式，也可以显式：

```sh
./install.sh --tool claude-code --copy
```

不提供原生 PowerShell 安装器。原生 Windows 下请用 WSL 或 Git Bash。

## 11. 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| agent 里看不到技能 | 会话未重启；或工具版本太旧 | 重开新会话；确认工具支持 skills |
| `refusing to overwrite` | 同名目录已存在且不是本工具装的 | 先确认内容，再 `--force`（会备份）或手动改名 |
| `symlinks: not supported` | 文件系统/权限不支持软链 | 改用 `--copy` |
| 权限错误 | 目标目录属于 root | 不要用 sudo 装进系统目录，回到用户目录 |
| `python3 not found` | 维护脚本或可选的 `installer/` 需要 | 主安装/卸载不需要 Python；直接用 `./bin/awesome-skills` |
| 移动了 HOME 后软链断了 | 镜像使用绝对路径 | 重新运行 `./install.sh --tool auto` |
| 校验失败 | 传输过程改写了文件 | 用 U 盘/scp 重传，不要用聊天软件转发 |

## 12. 安全检查表

- [ ] 压缩包校验和通过。
- [ ] 安装前读过要装的 `SKILL.md`。
- [ ] 没有用 `sudo` 安装到系统目录。
- [ ] `--force` 前确认旧内容是什么。
- [ ] 安装后用 `doctor` 检查断链和未托管路径。
