# 上游技能同步设计

> 状态：设计稿（2026-10-24），**尚未实现**。当前仓库仍靠 `skills/` 手工收录和
> [scripts/import.py](../scripts/import.py)。这份文档只保留方案；落地时按第 10 节执行。

## 1. 目标

把“从开源仓库收集技能”变成一条可审计的流水线：

- 只同步**人工审过**的 upstream 白名单，不自动发现任意仓库。
- 每次同步固定到 commit，PR 里能看到 `old -> new`。
- 自动开 PR，但**不自动合并**；许可证、脚本、可移植性由人复核。
- 同步结果仍然是一个普通 `skills/<name>/` 目录，安装器和离线打包不需要改。

非目标：

- 不做 LSP/MCP 运行时，不装语言服务器。
- 不把上游二进制、`node_modules`、`.git` 带进来。
- 不追求“全自动收录所有开源 skill”。

## 2. 不采用的方案

| 方案 | 问题 |
| --- | --- |
| git submodule | 离线包会带 `.git`；安装器要处理嵌套仓库；许可证和路径不可控。 |
| CI 里 `npx skills add` 后直接 commit | 依赖 Node/网络；写入位置由 CLI 决定；缺少 lock、漂移和许可证闸门。 |
| 定时直接 push main | 上游一次破坏性改动就进收藏，没有 review 窗口。 |
| 爬 awesome list | 列表没有稳定的 `repo + path + commit` 契约，解析脆弱。 |

`npx skills` 可以用来**发现**候选，落地仍写进白名单。

## 3. 流程

```text
upstreams.json（人审白名单）
      │
      ▼
scripts/sync_upstreams.py
  1. track -> commit（git ls-remote）
  2. sparse clone 到临时目录
  3. 在 paths 下找 SKILL.md
  4. include/exclude + frontmatter 校验
  5. license gate
  6. 本地漂移检查
  7. 复制到 skills/<name>/，更新 sources.json + upstreams.lock.json
      │
      ▼
validate.py --strict → catalog.py → test-install.sh → 开 PR
```

`upstreams.json` 是输入，`upstreams.lock.json` 是输出。lock 必须提交；它让同步
可复现，也让本地改动不会被静默覆盖。

## 4. 配置：`upstreams.json`

```json
{
  "schemaVersion": 1,
  "upstreams": [
    {
      "id": "anthropic",
      "repo": "anthropics/skills",
      "track": "branch:main",
      "license": "per-skill",
      "licenses": {
        "skill-creator": "Apache-2.0",
        "mcp-builder": "Apache-2.0",
        "webapp-testing": "Apache-2.0",
        "frontend-design": "Apache-2.0"
      },
      "licenseFile": "LICENSE.txt",
      "paths": ["skills"],
      "include": [
        "skill-creator",
        "mcp-builder",
        "webapp-testing",
        "frontend-design"
      ]
    },
    {
      "id": "vercel",
      "repo": "vercel-labs/skills",
      "track": "branch:main",
      "license": "MIT",
      "licenseFile": "LICENSE",
      "paths": ["skills"],
      "include": ["find-skills"]
    }
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 稳定标识，用于 lock 和 `sources.json`；不要改名。 |
| `repo` | 是 | `owner/name`，只接受 GitHub。 |
| `track` | 是 | `branch:main`、`tag:v1.2.3` 或 40 位 commit SHA。 |
| `license` | 是 | SPDX 标识（repo 级）或 `per-skill`。 |
| `licenses` | `per-skill` 时必填 | 技能名 → SPDX。 |
| `licenseFile` | 是 | repo 级许可证相对仓库根；`per-skill` 时相对每个技能目录。 |
| `paths` | 是 | sparse checkout 的目录，也是搜索 `SKILL.md` 的范围。 |
| `include` | 是 | 技能名或 glob；显式白名单，**空数组表示不导入**。 |
| `exclude` | 否 | 优先于 `include`。 |
| `rename` | 否 | 原名 → 本地名；会改写 frontmatter，先不用。 |
| `enabled` | 否 | 默认 `true`；设为 `false` 可暂停某个 upstream。 |

`per-skill` 模式下，`include` 里的每个技能都要在 `licenses` 里有条目；用 glob
时，未列出许可证的技能会被跳过并报告。

`include` 不建议写 `*`。上游新增的技能应在 PR 里列为 “available but not
imported”，人工确认后再加；只有很小的、完全信任的 repo 才考虑 `*`。

## 5. 锁文件：`upstreams.lock.json`

```json
{
  "schemaVersion": 1,
  "upstreams": {
    "anthropic": {
      "repo": "anthropics/skills",
      "track": "branch:main",
      "commit": "0123456789abcdef0123456789abcdef01234567",
      "skills": {
        "skill-creator": {
          "path": "skills/skill-creator",
          "license": "Apache-2.0",
          "files": {
            "SKILL.md": "sha256-hex",
            "LICENSE.txt": "sha256-hex"
          }
        }
      }
    }
  }
}
```

- lock 由 sync 生成，不要手改。
- `commit` 是实际 checkout 的 HEAD，不是 `main` 的别名。
- `files` 是 vendored 之后的哈希（含补上的 `license` 行），不是上游原始文件。
- 不写时间戳，保证 commit 不变时 lock 逐字节稳定。
- `sources.json` 记录来源 URL、commit、SPDX；`source` 用
  `https://github.com/<repo>/tree/<commit>/<path>`，不要写本机绝对路径。
- 同步条目不写 `importedAt`，避免 commit 不变时产生无意义 diff；手工导入
  （`import.py`）仍保留该字段。
- 手工导入的 `source` 仍是本机路径；公开仓库前改成来源 URL，避免泄露目录结构。

## 6. 同步脚本行为

计划中的 `scripts/sync_upstreams.py`，Python 3 标准库 + `git`。只通过 git
访问 GitHub，不调用 REST API，也不下载 release 包。

1. 读配置，校验 schema；拒绝重复 `id`、重复技能名。
2. 解析 `track`：
   - 40 位十六进制：直接当 commit。
   - `branch:` / `tag:`：`git ls-remote` 解析（annotated tag 取 `^{}`），
     clone 后再用 `rev-parse HEAD` 校正。
3. lock 的 `commit` 与解析结果相同：先校验本地哈希；一致则跳过，不一致则按
   漂移处理（见第 8 步）。
4. sparse clone 到临时目录；branch/tag 用 `--branch`，SHA 用 `git init` +
   `fetch --depth 1 origin <sha>`；只 checkout `paths`。
5. 在 `paths` 下递归找 `SKILL.md`，跳过 `.git`、`node_modules`、`__pycache__`、
   `.venv`、`dist`。
6. 解析 frontmatter；按 `include`/`exclude` 过滤；检查 name、description、目录名。
7. license gate：查 SPDX、查许可证文件、复制 `LICENSE`/`NOTICE`、补 frontmatter。
8. 漂移检查：本地文件与 lock 不一致时，默认中止；`--force` 才覆盖。
9. 复制到 `skills/<name>/`，保留可执行位；拒绝符号链接和超大文件。
10. 更新 `upstreams.lock.json` / `sources.json`，输出摘要（added / updated /
    unchanged / skipped / orphaned / available-not-imported）。

| 参数 | 作用 |
| --- | --- |
| `--dry-run` | 只打印计划，不写文件。 |
| `--upstream ID` | 只处理一个 upstream。 |
| `--force` | 覆盖本地漂移；确保已提交，Git 可恢复。 |
| `--prune` | 删除上游已移除、且本地未改动的技能；默认保留并报告。 |
| `--summary PATH` | 写 Markdown 摘要，给 PR body 用。 |
| `--check` | 只解析 commit 并与 lock 比较；过期返回 1。 |

`enabled: false` 的 upstream 不参与正常同步，但 `--prune` 仍可清理它的 lock 记录。

有 `error`（clone 失败、许可证拒绝、名字冲突）时返回 1，workflow 会失败并通知；
只有 skip/warning 时返回 0，继续开 PR。

本地手动流程（脚本落地后）：

```sh
python3 scripts/sync_upstreams.py --upstream anthropic --dry-run
python3 scripts/sync_upstreams.py --upstream anthropic
python3 scripts/validate.py --strict
python3 scripts/catalog.py
sh tests/test-install.sh
```

## 7. CI

计划中的 `.github/workflows/sync-skills.yml`：

```yaml
name: sync-skills

on:
  schedule:
    - cron: "0 3 * * 1"      # 每周一 03:00 UTC = 北京时间 11:00
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

concurrency:
  group: sync-skills
  cancel-in-progress: false

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python3 scripts/sync_upstreams.py --summary "$RUNNER_TEMP/summary.md"
      - run: python3 scripts/validate.py --strict
      - run: python3 scripts/catalog.py
      - run: sh tests/test-install.sh
      - uses: peter-evans/create-pull-request@v8
        with:
          token: ${{ secrets.SYNC_TOKEN }}
          branch: chore/sync-skills
          title: "chore: sync upstream skills"
          body-path: ${{ runner.temp }}/summary.md
          delete-branch: true
```

下面的写法使用 `SYNC_TOKEN`；先配好 secret，否则 action 会直接失败。如果选后面
的第 2 种方案，把 `token:` 行删掉即可。

**Token 坑**：用默认 `GITHUB_TOKEN` 开的 PR 不会触发
[validate.yml](../.github/workflows/validate.yml) 的 `pull_request` 事件
（GitHub 防递归）。两个选择：

1. 配 `SYNC_TOKEN`（fine-grained PAT，`Contents: Read and write` +
   `Pull requests: Read and write`），PR 的正常检查会跑。
2. 不配 token，接受“sync job 自己跑完 validate/catalog/test”，但分支保护要求
   的 `validate` check 不会出现在 PR 上。

分支保护建议：要求 review，禁止 auto-merge；`CODEOWNERS` 覆盖 `skills/`。
第三方 action 至少 pin 到 SHA。

## 8. 四道闸门

| 闸门 | 检查 | 失败处理 |
| --- | --- | --- |
| License | SPDX 白名单、LICENSE/NOTICE、逐技能核对 | 跳过该技能并在 PR 里报告 |
| Portability | frontmatter、命名、链接、脚本、交叉引用 | 跳过并报告 |
| Security | 危险脚本、密钥、二进制、人工 review | 阻止合并 |
| Drift | lock 哈希 | 中止；`--force` 才覆盖 |

### 8.1 License

- 先只收：`MIT`、`Apache-2.0`、`BSD-2-Clause`、`BSD-3-Clause`、`ISC`、`CC0-1.0`。
- 暂不收：GPL/AGPL（避免许可证混杂）、`NOASSERTION`、source-available、没有
  LICENSE 的。
- 必须**逐技能**看许可证文件，不能只看 repo 的 license 字段。
- 按原文件名复制 `LICENSE` / `LICENSE.txt` / `NOTICE` 到技能目录；
  `sources.json` 记 SPDX + commit。
- 上游没有 `license` frontmatter 的，sync 只补一行 `license:`，不改正文；
  否则 [validate.py](../scripts/validate.py) 的 `--strict` 会拦。

### 8.2 Portability

- frontmatter 只允许 `name`、`description`、`license`、`allowed-tools`、
  `metadata`、`version`；拒绝 `context`、`hooks`、`agent`、`model` 等
  Claude Code 扩展。
- `name` = 目录名，小写字母/数字/单连字符，≤64 字符；description ≤1024。
- 检查 `superpowers:writing-plans` 这类命名空间交叉引用；扁平安装会断。
  要么整套导入并改写，要么跳过。
- 相对链接必须能打开；脚本要有可执行位；不写绝对路径；默认不联网。
- `SKILL.md` 超过 ~500 行会触发 warning，`--strict` 下视为失败。

### 8.3 Security

- 扫描 `curl | sh`、`wget | sh`、`eval`、`rm -rf`、`chmod 777`、`base64 -d | sh`、
  常见密钥前缀、二进制文件和符号链接。
- 扫描可以先做成 `scripts/audit_skills.py`，在 sync 后跑；MVP 至少人工看 diff。
- PR 必须人工看 `scripts/`；skill 是给 agent 的指令，存在 prompt injection 风险。
- 不 auto-merge；只接可信 org；`CODEOWNERS` 保护 `skills/`。

### 8.4 Drift

- lock 记录每个文件的 SHA-256；本地改动后下次 sync 默认中止。
- 上游删除技能：默认保留并标记 orphaned；`--prune` 只删未改动的。
- 想本地定制：不要改 `skills/<name>/`，复制成 `skills/<name>-local`，改
  frontmatter 的 `name`，并移出 `include`；否则下次同步会冲突。

## 9. 候选 upstream

许可证核对时间：2026-10-24。上游会变，接入前重新看一遍 `LICENSE`。

| upstream | license | 规模/路径 | 建议 |
| --- | --- | --- | --- |
| `anthropics/skills` | 混合，逐技能 | `skills/` | 先接 4 个 Apache-2.0；排除文档技能 |
| `vercel-labs/skills` | MIT | `skills/find-skills` | 可选；依赖 `npx skills` 和网络 |
| `obra/superpowers` | MIT | `skills/`，15 个 | 整套导入并改写 `superpowers:` 引用，或跳过；不建议零散收 |
| `getsentry/skills` | Apache-2.0 | `skills/`，约 30 个 | `find-bugs` 较通用；其余偏 Sentry |
| `microsoft/skills` | MIT | `.github/skills`，170+ | 按需；Azure/Foundry 专用，别全收 |
| `cloudflare/skills` | Apache-2.0 | `skills/`，约 20 个 | 按需；Workers/Cloudflare 专用 |
| `prisma/skills` | MIT | 仓库根目录 | 按需；Prisma 专用 |
| `openai/skills` | 逐技能 | `skills/` | 跳过；README 已标 deprecated |
| `openai/plugins` | 未核 | — | 暂不接，许可证不明确 |

`anthropics/skills` 的细节：

- 可接：`skill-creator`、`mcp-builder`、`webapp-testing`、`frontend-design`，
  都有 Apache-2.0 `LICENSE.txt`。
- `skill-creator` 目前 485 行，接近 500 行上限；上游再长会被 `--strict` 拦。
- 排除：`docx`、`pdf`、`pptx`、`xlsx` 是 source-available；`doc-coauthoring`
  没有 `LICENSE.txt`。

## 10. 落地顺序

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| A | 只搭骨架：配置、sync 脚本、lock、workflow、docs、测试；不接 upstream | 空跑不报错；本地 git fixture 测试通过 |
| B | 接 `anthropics/skills` 的 4 个 Apache-2.0 技能 | PR 里能看到真实 diff、许可证和来源 |
| C | 加 `getsentry/find-bugs` 等通用技能 | 同上；确认没有命名/引用问题 |

计划新增：

| 文件 | 作用 |
| --- | --- |
| `upstreams.json` | 人审白名单 |
| `upstreams.lock.json` | commit + 每个文件的哈希 |
| `scripts/sync_upstreams.py` | 同步脚本 |
| `.github/workflows/sync-skills.yml` | 定时同步并开 PR |
| `tests/test_sync.py` | 本地 `file://` git fixture 测试 |
| `docs/upstreams.md` | 本文 |

MVP 只做：配置、clone、发现、include/exclude、license、复制、lock、sources、
summary、dry-run、`--prune`。暂缓：`rename`、`--check`、GitHub App、自动发现。

测试用本地 `file://` 裸仓库，不依赖网络：

- 新增技能 → 文件、lock、sources 正确；
- 上游更新 → 覆盖并更新 lock；
- 本地改动 → 报错，`--force` 才覆盖；
- 缺 LICENSE / 名字非法 / 重名 → 跳过并报告；
- 上游删除技能 → 默认保留，`--prune` 删除。

## 11. 维护约定

- vendored 技能视为只读；要定制就 fork 成新名字，并移出 `include`。
- 每周看一次 sync PR：许可证、`scripts/`、diff 大小；没问题再 merge。
- 移除 upstream：先把 `enabled` 设为 `false`，跑
  `sync --prune --upstream <id>`（disabled 的 upstream 仍参与 prune），确认删干净
  后再从配置里删条目；最后 `validate.py --strict` + `catalog.py`。
- `upstreams.lock.json` 必须提交；`sources.json` 的 `source` 用 URL，不写本机路径。
- 定时任务只读默认分支；改完 `upstreams.json` 要先 merge 到 `main`，或者本地
  手动跑 sync 再提 PR。

## 12. 待定

- 导入技能放 `skills/<name>/` 还是 `skills/vendor/<name>/`：安装器都会扁平化，
  但 `name` 仍需全局唯一。先用扁平，数量多了再分类。
- 是否接受 GPL/AGPL：目前不收，避免许可证混杂。
- `rename` 与命名空间：会改 frontmatter、可能断交叉引用，暂缓。
- 有兄弟技能依赖的上游（如 `superpowers`）：整套导入并改写，或直接跳过。
- Token 用 PAT 还是 GitHub App：个人仓库先用 PAT，轮换成本低。

## 13. 参考

- [docs/authoring.md](authoring.md) — 技能编写约定
- [docs/offline.md](offline.md) — 离线打包与安装
- [scripts/import.py](../scripts/import.py) — 当前的手工导入
- [scripts/validate.py](../scripts/validate.py) — frontmatter/链接/卫生检查
- [scripts/catalog.py](../scripts/catalog.py) — README/catalog 生成
- [.github/workflows/validate.yml](../.github/workflows/validate.yml) — 现有 CI
- [Agent Skills](https://agentskills.io)、[skills.sh](https://skills.sh)
- [anthropics/skills](https://github.com/anthropics/skills)、
  [vercel-labs/skills](https://github.com/vercel-labs/skills)
