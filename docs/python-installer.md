# Python 安装器（可选实现）

`installer/` 是 agentinstaller 参考项目（codegraph 仓库里的 Python 标准库实现）架构的
移植：把 **registry + 每 agent 一个 target + 只读探测 + 幂等写入 + 自己的状态清单 +
可逆卸载 + 漂移检查** 这套结构，套到本仓库的 `lib/agents.tsv`（80 个工具别名）和
「共享 hub + 每工具镜像」的安装模型上。

> **定位**：主安装器仍然是 [`bin/awesome-skills`](../bin/awesome-skills)——纯 POSIX
> shell、零依赖、离线可用。Python 版是**并列的可选实现**，需要 Python 3.9+，
> 只用标准库，不联网。两者都不改对方的代码，可以同时存在于一个 checkout 里。

## 为什么还要一个 Python 版

shell 安装器已经可靠，但它有三件事做起来别扭：

| 问题 | shell 版 | Python 版 |
| --- | --- | --- |
| 幂等判断 | marker 里写 `installed=<时间戳>`，每次安装都算“变了” | 按内容哈希比较，第二次安装报告 `unchanged` 且不碰文件 |
| 漂移检查 | `doctor` 只做粗检 | `check` 逐条核对记录：缺失、被替换、工具目录改了、copy 过期，退出码 1 |
| 共享目录卸载 | 按 manifest 路径删除 | 同一目录被多个工具共用时，只释放自己的记录，等最后一个工具卸载才删路径 |

另外 Python 版把「预检」做成硬约束：任何目标路径冲突都在第一次写入前报错，
不会留下装了一半的 hub。

## 安装模型

与 shell 版一致，只是记录方式不同：

```
skills/<name>/           仓库里的源
        │
        ▼
~/.agents/skills/<name>/ 共享 hub（真实副本 + .awesome-skills-skill 标记）
        │
        ├── ~/.claude/skills/<name>     symlink（默认）或 copy
        ├── ~/.cursor/skills/<name>
        └── ...                          每个工具自己的 skills 目录
```

- 11 个别名的全局目录都指向 `~/.agents/skills`，它们**直接读 hub**，不建镜像。
- 多个别名共用一个非 hub 目录时（例如 `amp` / `replit` 的
  `~/.config/agents/skills`），目录只写一次，状态里给每个别名各记一条。
- `--project DIR` 把 hub 换成 `<DIR>/.agents/skills`，把相对路径锚定到项目，
  状态写到 `<DIR>/.agents/.awesome-skills/state.json`。

## 命令

```sh
# 从 checkout 直接跑，不需要安装
./installer/bin/awesome-skills-py                  # 终端里进入交互向导
./installer/bin/awesome-skills-py install --tool auto
./installer/bin/awesome-skills-py install --tool claude-code,cursor --skill handoff
./installer/bin/awesome-skills-py install --tool all --copy
./installer/bin/awesome-skills-py install --project ~/code/app --tool auto
./installer/bin/awesome-skills-py install --dry-run --tool auto   # 只打印计划
./installer/bin/awesome-skills-py install --yes --tool auto       # 脚本/CI：不提问

./installer/bin/awesome-skills-py list                # 仓库里有什么 + 装了什么
./installer/bin/awesome-skills-py list --installed
./installer/bin/awesome-skills-py check               # 有漂移时退出码 1
./installer/bin/awesome-skills-py doctor
./installer/bin/awesome-skills-py tools --detected

./installer/bin/awesome-skills-py uninstall --tool cursor
./installer/bin/awesome-skills-py uninstall --all
./installer/bin/awesome-skills-py uninstall --all --skill handoff
```

等价的模块入口（测试和打包时用）：

```sh
PYTHONPATH=installer/src python3 -m awesome_skills --help
```

也可以 `pip install -e installer`，然后直接用 `awesome-skills-py`。安装器本身
不依赖 pip；从源码运行只需要 `PYTHONPATH`。

### 选项

| 选项 | 含义 |
| --- | --- |
| `--tool LIST` | 逗号分隔别名；`auto`（默认）只选探测到的；`all` 全部；`none` 不建镜像 |
| `--skill LIST` | 逗号分隔技能名；默认全部 |
| `--project DIR` | 项目级安装；`--global` 是默认 |
| `--copy` / `--link` | 镜像方式；默认 link（Windows 无符号链接权限时用 `--copy`） |
| `--force` | 替换不是本工具装的路径；替换前移到备份目录 |
| `-y`, `--yes` | 关闭交互向导，按 `auto` 探测结果直接安装（脚本/CI） |
| `--dry-run` | 只打印计划，不写文件、不备份、不清理目录 |
| `--root DIR` | 仓库根（默认：当前 checkout / `AWESOME_SKILLS_ROOT` / cwd） |

## 交互向导

shell 安装器从不提问，`auto` 是脚本和 CI 的安全默认。Python 版在这之上做了
**codegraph 同款的 clack 界面**：`┌ │ └` 引导栏、`◆`/`◇` 步骤符、方向键多选、
绿色提交帧、`note` 计划框和 `└ Done` 收尾。全部用标准库复刻，不依赖 Node、
`@clack/prompts` 或任何第三方包。

触发条件（两者同时满足）：

- `stdin` 和 `stdout` 都是 TTY；
- 没给 `--tool` / `--skill`，也没给 `-y` / `--dry-run`。

两种入口等价：直接运行 `awesome-skills-py`，或 `install` 不带选择参数。

```text
┌  awesome-skills v0.1.0
│
◆  Apply skills to all your projects, or just this one?
│  ● All projects (~/.agents/skills)
│  ○ Just this project
└
│
◇  Apply skills to all your projects, or just this one?
│  All projects
│
◆  Which agents should awesome-skills configure?
│  ...
│  ◼ claude-code (detected)
│  ◻ cursor (detected)
│  ◻ aider-desk (not found)
│  ...
└
│
◆  Which skills should be installed?
│  ◼ handoff
│  ◼ shell-review
│  ...
└
│
◆  How should the per-agent mirrors be written?
│  ● link (symlink to the hub, one copy on disk)
│  ○ copy
└
│
◇  Plan ────────────────────────────────────╮
│  scope:  global                           │
│  hub:    ~/.agents/skills                 │
│  tools:  2  claude-code, cursor           │
│  skills: 2  handoff, shell-review         │
│  mode:   link                             │
│  paths:  4 create, 0 update, 0 unchanged  │
├───────────────────────────────────────────╯
│
◆  Proceed with the install?
│  ● Yes / ○ No
└
│
◇  Installing 2 skill(s) into ~/.agents/skills
│
◆  Installed 2 skill(s) into ~/.agents/skills
│
●  4 created, 0 updated, 0 unchanged
│
◇  Next ─────────────────────────────────────╮
│  Start a new agent session.                │
│  Most agents scan skills only at startup.  │
├────────────────────────────────────────────╯
│
└  Done! Restart your agent to use the new skills.
```

操作：

| 按键 | 作用 |
| --- | --- |
| `↑` / `↓` | 移动光标（跳过不可用项） |
| `Space` | 多选中切换勾选（`◻` / `◼`） |
| `Tab` | 补全项目目录（只列目录；保留 `~/` 与相对路径；多匹配时补到公共前缀） |
| `Ctrl-U` | 清空当前输入（项目目录预填了当前目录，用它改成别的路径） |
| `Enter` | 提交当前选择 |
| `←` / `→` / `y` / `n` | 确认框切换 Yes/No |
| `Ctrl-C` / `Ctrl-D` | 取消，画出红色 `■` 取消帧后退出，不写任何文件 |

细节：

- 顺序与 codegraph 一致：范围 → agent 多选（探测到的预选）→ 技能多选（默认全选，
  描述作为提示）→ 模式 → dry-run `note` → 确认。
- agent 多选**第一页只放 10 个常用别名**：探测到的排最前，然后是 Cursor、Gemini CLI、
  Copilot、DSH、Cline、Windsurf、Zed 等；另外 ~70 个用 `↓` 滚动可见，不再一上来就是
  80 行。
- 不支持当前 scope 的 agent 显示为禁用项；直接读 hub 的 agent 在聚焦时提示
  “reads the shared hub directly”。
- 选择 “Just this project” 后，输入框会**直接预填当前目录**（可编辑文本，不是占位符），
  直接 Enter 就用它；要换路径可以 `Ctrl-U` 清空后输入或粘贴。路径支持 `Tab` 补全：
  唯一匹配直接补全并加 `/`，多个匹配补到公共前缀并在下方列出前几个。
- 范围、工具、技能、方式都会先跑一遍 **dry-run**，把“创建/更新/不变”的数量和
  hub 路径放进 `Plan` note，确认后才真正写入。
- 目标路径冲突时不会直接失败：列出冲突并询问是否 `--force`（先备份）。仍然失败
  （例如目录不可写）就取消，不写任何东西。
- `--copy` / `--link` / `--force` 如果显式给了，向导会跳过对应问题；`-y` 关闭向导。
- TTY 下的 `install --tool …` 和 `uninstall` 也使用同一套 clack 帧（无提示），管道/CI 仍是纯文本。
- 非 TTY（管道、CI、`ssh` 无终端）自动退回无提示的 `auto` 行为；shell 安装器完全不变。
- 颜色遵循 `NO_COLOR` / `FORCE_COLOR` / TTY；`AWESOME_SKILLS_ASCII=1` 切换 ASCII 符号
  （`T | -`、`[ ]`/`[+]`），窄终端按宽度截断选项、折行消息。
- 探测来自 `lib/agents.tsv` 的 `detect` 列（`cmd:` 或路径）；`PATH` 只扫描一次并缓存，
  避免 WSL 的 `/mnt/c` 让每个缺失命令都花 80ms。

## 状态清单

一个 scope 一份，位置与 shell 版相同（便于互相发现），文件名不同：

```
~/.agents/.awesome-skills/state.json                  # 全局
<project>/.agents/.awesome-skills/state.json          # 项目
```

```json
{
  "version": 1,
  "tool": "awesome-skills",
  "scope": "global",
  "hub": "/home/me/.agents/skills",
  "targets": {
    "hub": {
      "handoff": {
        "mode": "copy",
        "version": "0.1.1",
        "path": "/home/me/.agents/skills/handoff"
      }
    },
    "claude-code": {
      "handoff": {
        "mode": "link",
        "version": "0.1.1",
        "path": "/home/me/.claude/skills/handoff"
      }
    }
  }
}
```

规则：

- **每条记录只描述自己写过的那个路径**。安装/卸载的写入目标仍然由
  `lib/agents.tsv` 在调用时解析（`CLAUDE_CONFIG_DIR`、`XDG_CONFIG_HOME` 改了会跟着走）。
- 记录里的 `path` 是“上次实际写在哪”，用于环境变量改掉之后还能清理旧镜像；
  当前解析结果与它不一致时 `check` 报 `tool directory moved`。
- 清单空了就删文件，再尝试删空目录——从零安装可以被完整回滚。
- 例外：如果同目录还有 shell 版的 `manifest.tsv`，清空时保留一个空的
  `state.json` 作为“墓碑”，否则下次加载会把旧 manifest 重新导入、让已删除的条目
  复活。想重新采纳旧 manifest，删掉 `state.json` 即可。
- 清单损坏时按空处理并拒绝 `check`/删除；下次写入前把原文件备份成
  `state.json.corrupt`，不会静默丢掉唯一的归属记录。
- **兼容 shell 清单**：没有 `state.json` 但存在 `manifest.tsv` 时，按只读方式导入
  旧记录（按路径反查别名；共享目录只记第一个别名）。下一次修改会写成
  `state.json`，`manifest.tsv` 保持原样。

## 照搬了哪些原则

| 原则 | 参考项目 | 这里 |
| --- | --- | --- |
| 唯一的 agent 列表 | `registry.py` 里 4 个 target | `lib/agents.tsv` 的 80 行；解析失败是仓库错误 |
| 一个 agent 一个 target，接口只有几个动词 | `AgentTarget` | `AgentTarget.detect/install/uninstall/verify/describe_paths`，一行数据一个实例 |
| `detect()` 只读、安静、每次全量跑 | 契约 + 测试 | `shared.is_detected` / `detect_paths`；测试断言探测前后 HOME 不变 |
| 幂等：字节相同 → `unchanged` | `json_deep_equal` | 目录内容指纹（排除 marker），不靠 mtime |
| 只改自己那一段 | marker 围栏 | 每个技能目录一个 `.awesome-skills-skill` 标记 |
| 已有条目是用户的 | `merge_missing` | 非本工具装的路径默认拒绝；`--force` 先备份 |
| uninstall 只删自己写的 | 清单里的名字 | 清单 + 再验证归属（链接指向 hub / marker 名字） |
| 单个 agent 失败不打断整轮 | try/catch | 每个 target、每个 skill 单独捕获，汇总退出码 |
| 契约测试对全部 target 参数化 | 4 个 target | 直接对真实 80 行跑安装→校验→卸载 |

## 有意没照搬的部分

- **没有每个 agent 一个 `.py`**。80 个别名只有“四个路径列”的差异，写成 80 个模块会
  和 `lib/agents.tsv` 重复，且必然漂移。`AgentTarget` 是数据行上的薄对象，仍然由它
  实现 5 个动词；新增 agent 只改表。
- **payload 只有 skills**。参考项目还装 MCP、指令块、权限和 CLI 链接；本仓库只分发
  skills，所以 `InstallRequest` 只带技能、hub、mode、force、dry-run。要加新载荷时，
  按参考项目的扩展点加字段与 target 能力，而不是把无关配置塞进 skills。
- **没有 `print-config`**。skills 没有要用户手贴的配置片段。
- **写入单位是目录，不是 agent**。多个别名共享一个目录（`~/.agents/skills` 有 11 个，
  `~/.config/agents/skills` 有 3 个），逐个 agent 写会把同一路径写很多次；编排器
  按目录去重，状态里仍按别名记录。这是本仓库的模型差异，不是偷懒。

## 安全规则

1. **预检先于写入**：hub 与所有镜像目标先查冲突/权限，任何冲突在第一次写之前中止。
2. **归属可证明**：链接必须指向 `hub/<name>`；副本必须有 `name=<name>` 的 marker。
   两者都不满足时，`uninstall` 保留路径并保留记录，等用户处理。
3. **共享路径只释放**：卸载一个工具时，如果另一个已记录的工具解析到同一路径，
   保留文件、删除本工具的记录。
4. **`--force` 先备份**：非本工具创建的路径移到
   `~/.agents/.awesome-skills/backups/<时间>/<目录哈希>/<name>`，再替换。
5. **原子与写穿**：状态文件原子写；目标文件是符号链接时写穿链接，不把链接替换成
   普通文件。技能目录先复制到同文件系统的临时目录，再 `rename` 到位。
6. **`--dry-run` 全链路无写**：不建 hub、不备份、不清理空目录、不落状态。

## 与 shell 安装器共存

两者写同一套 hub 和镜像，但状态文件不同：

- Python 版能读 shell 的 `manifest.tsv`（只读导入），因此 `check` / `uninstall` 能看到
  旧安装。
- shell 版不读 `state.json`。用 Python 版新装的条目，shell 的 `uninstall` 不会知道；
  建议一个 scope（全局/项目）固定用其中一个卸载。
- marker 兼容：Python 版写 `name= / source= / version=`，不写时间戳；shell 的归属
  判断只读 `name=`，两边都认。
- 两者对 `--tool` / `--skill` / `--all` / `--project` 的语义一致；Python 版多
  `check`，shell 版多彩色输出和交互提示。

## 测试

```sh
PYTHONPATH=installer/src python3 -m unittest discover -s installer/tests -v
```

覆盖：路径模板展开、探测只读、表解析错误、技能发现（深度/嵌套/重名）、hub 幂等与
备份、link/copy 切换、冲突预检、共享目录、预检原子性、dry-run 无写、卸载选择语义、
漂移检查、legacy 导入、损坏清单、clack 帧（select/multiselect/confirm/note/ASCII）、
交互向导（默认值/多选/冲突转 `--force`/取消），以及**真实 80 行表的全量安装→卸载契约**。

CI 里与 shell 测试并列运行（见 `.github/workflows/validate.yml`）。

## 已知限制

- 只装 skills；没有 MCP/指令/权限。
- 没有进程间锁：两个安装器同时跑仍可能互相覆盖状态（参考项目同样未实现）。
- Windows 上 `--link` 需要开发者模式/权限；否则用 `--copy`。
- legacy 清单导入只能按目录反查别名；一个目录被多个别名共享时只记第一个。
- 卸载后可能留下空的 agent 配置目录（只删到 skills 目录一层），与 shell 版一致。
- 向导是终端行内重绘（clack 风格），不是鼠标 GUI；Windows 用 `msvcrt` 读键。
- `--root` 是给测试和打包场景用的逃生门；正常从 checkout 运行不需要。
- 若 shell 版在 Python 版清空之后又装过东西，Python 版不会自动重新导入
  `manifest.tsv`（有墓碑时）；删掉 `state.json` 再运行即可重新采纳。
