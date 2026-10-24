# 编写技能

一个技能就是一个目录，核心只有 `SKILL.md`。agent 先看到 `name` + `description`，
判断是否相关；相关时才加载正文。所以**描述决定它会不会被用上**。

## 最小示例

```markdown
---
name: pr-description
description: Write a pull request description from the current branch diff. Use when the user asks for a PR description, PR 描述, or to summarize changes for review.
license: MIT
metadata:
  version: "1.0.0"
---

# PR Description

Produce a reviewer-oriented PR description from the branch diff.

## Workflow

1. Run `git diff --stat origin/main...HEAD` and read the full diff.
2. Group changes by intent, not by file.
3. Write: What / Why / How to test / Risks.

## Rules

- Never invent test results.
- Link the issue when the branch name or commits contain one.
```

## 目录约定

```text
skills/<name>/
├── SKILL.md              # 必需
├── references/           # 可选：按需加载的长文、检查表
├── scripts/              # 可选：可执行辅助脚本
├── assets/               # 可选：模板、图片、示例文件
└── LICENSE               # 第三方技能必须保留
```

- 目录名必须等于 frontmatter 的 `name`。
- 只允许小写字母、数字和单个连字符（`^[a-z0-9]+(-[a-z0-9]+)*$`），最长 64 字符。
- 仓库可以按分类嵌套（`skills/<category>/<name>/SKILL.md`），安装器会按技能名
  扁平化到目标目录；为避免重名，分类只用于组织，不进入技能名。
- 不要放 `.git`、`node_modules`、`__pycache__`、`.DS_Store`。

## Frontmatter 字段

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `name` | 是 | 唯一标识，小写连字符，≤64 字符，必须与目录名一致。 |
| `description` | 是 | 做什么 + **什么时候用**；≤1024 字符。写进用户真实会说的词。 |
| `license` | 建议 | 例如 `MIT`。第三方技能必须保留原许可证。 |
| `allowed-tools` | 否 | 允许的工具列表。不同 agent 支持程度不同，不要依赖它做安全边界。 |
| `metadata` | 否 | 自由映射，常用 `version`、`author`、`internal: true`。 |
| `version` | 否 | 顶层版本号也兼容；建议放 `metadata.version`。 |

`metadata.internal: true` 的技能不会出现在 README 目录里，也不会被
`catalog.py` 收录；适合仓库内部依赖（例如共享的参考资料包）。

## 写好 description

差：

```yaml
description: A skill about code review.
```

好：

```yaml
description: Review a diff for correctness, security, and missing tests before merge. Use when the user asks for a code review, PR review, 代码审查, or says "look over this change".
```

要求：

1. 第一句说清产出（review a diff…）。
2. 第二句说触发条件，包含用户可能说的原话（中英文都写）。
3. 写清**不适用**的场景，减少误触发。
4. 不要写 "This skill helps you…" 这类空话；agent 需要的是判断依据。

## 正文结构

推荐顺序：

1. **When to use** — 触发场景 + 不适用场景。
2. **Rules** — 硬约束、禁止事项、缺失信息时怎么办。
3. **Workflow** — 有顺序的步骤，每步说清输入和产出。
4. **Output format** — 用代码块给出可复制的报告结构。
5. **References** — 指向按需加载的细节。

写法：

- 用祈使句（"Read the diff"），不要用 "You should…"。
- 给判断标准，不要只给口号。例如 "blocker = 会删错目录或执行不可信代码"。
- 给一个短例子，比三段解释有效。
- 不要把整篇文档塞进 `SKILL.md`；超过 ~500 行就拆分。

## 渐进披露

| 文件 | 何时加载 | 放什么 |
| --- | --- | --- |
| `SKILL.md` | 技能被激活时 | 决策、流程、输出格式 |
| `references/*.md` | agent 判断需要时 | 检查表、长表格、领域细节 |
| `scripts/*` | 需要执行时 | 确定性辅助工具 |
| `assets/*` | 需要模板/样例时 | 模板、示例输入输出 |

在正文里明确写 "load `references/checklist.md` when …"，否则 agent 可能不知道
它存在。

## 脚本要求

- `#!/bin/sh` + POSIX 语法，或明确 `#!/usr/bin/env bash`。
- `set -eu`；用法写在头部；`--help` 可用。
- 退出码有意义：0 成功，1 发现问题，2 用法错误。
- 默认不联网；需要联网时在 `SKILL.md` 里显式说明。
- 可执行位要提交：`chmod +x scripts/foo.sh`。
- 不写入技能目录之外的位置，除非参数明确指定。
- 不在脚本里放密钥；从环境变量读取，且不要打印。

## 跨 agent 可移植性

- 只用标准字段。`context: fork`、hooks、`agent` 等是 Claude Code 扩展，别的
  agent 会忽略。
- `allowed-tools` 在部分工具里不生效，不要把它当安全机制。
- 不要假设某个 agent 有特定工具名；用通用说法（"read the file"），需要时再给
  命令示例。
- 路径用相对技能目录的写法，便于安装到任意位置。

## 收录第三方技能

```sh
python3 scripts/import.py /path/to/skill

# 批量收录本机已安装的技能（同名已存在时会报错并跳过）
for d in ~/.agents/skills/*/; do
  [ -f "$d/SKILL.md" ] && python3 scripts/import.py "$d"
done
```

- 先确认许可证允许再分发；保留原 `LICENSE` / `NOTICE`。
- `sources.json` 记录来源路径和许可证。
- 如果技能依赖同仓库的另一个技能（例如 `*-shared`），两个都要收录，并在
  `description` 里写清依赖关系。
- 不修改原作者的行为，除非在 `metadata` 里注明；改名时同步改 frontmatter。

## 验证与发布

```sh
python3 scripts/validate.py        # frontmatter、链接、卫生
python3 scripts/catalog.py         # 更新 README / catalog.json / docs/tools.md
sh tests/test-install.sh           # 安装器回归
```

手工验证：

1. 在临时 HOME 里装一次：`HOME=$(mktemp -d) ./install.sh --tool claude-code`。
2. 用真实 agent 触发一次，确认它加载了正文而不是只看到描述。
3. 如果技能带脚本，分别测试成功路径和错误路径。
4. 检查 `references/` 里的相对链接。

## 版本与变更

- 行为变更（流程、输出、脚本参数）时提升 `metadata.version`。
- 只改错别字可以不升版本，但仍要跑校验。
- 破坏性变更在技能正文顶部加 "Migration" 小节，说明旧用法怎么迁移。

## 检查清单

- [ ] `name` 与目录一致，描述包含触发词和不适用场景。
- [ ] `SKILL.md` 不超过 ~500 行，细节在 `references/`。
- [ ] 相对链接都能打开，脚本有可执行位。
- [ ] 没有密钥、内网地址、客户数据。
- [ ] 许可证明确，第三方来源记录在 `sources.json`。
- [ ] `validate.py`、`catalog.py`、`test-install.sh` 全绿。
