# 上游技能同步

> 状态：已实现（2026-10-24）。白名单在 [upstreams.json](../upstreams.json)，
> 同步脚本是 [scripts/sync_upstreams.py](../scripts/sync_upstreams.py)，锁文件是
> [upstreams.lock.json](../upstreams.lock.json)。当前接入了
> `anthropics/skills` 的 4 个 Apache-2.0 技能和 `tt-a1i/archify`（MIT）；
> 新增上游按第 8 节操作。

## 1. 目标

把“从开源仓库收集技能”变成一条可审计的流水线：

- 只同步**人工审过**的 upstream 白名单，不自动发现任意仓库。
- 每次同步固定到 commit，PR 里能看到 `old -> new`。
- 自动开 PR，但**不自动合并**；许可证、脚本、可移植性由人复核。
- 同步结果仍然是一个普通 `skills/<name>/` 目录，安装器和离线打包不需要改。
- 第三方技能视为只读；本地定制另起名字，避免下次同步覆盖。

非目标：

- 不做 LSP/MCP 运行时，不装语言服务器。
- 不把上游二进制、`node_modules`、`.git` 带进来。
- 不追求“全自动收录所有开源 skill”。
- 不替代一次性的 [import.py](../scripts/import.py)：本地目录仍可用它导入。

## 2. 文件与数据流

| 文件 | 作用 |
| --- | --- |
| `upstreams.json` | 人审白名单；唯一需要手写的同步配置。 |
| `upstreams.lock.json` | 实际 commit + 每个 vendored 文件的 SHA-256；由同步脚本生成，必须提交。 |
| `scripts/sync_upstreams.py` | 同步脚本；Python 3 标准库 + `git`。 |
| `tests/test_sync.py` | 用本地 `file://` 裸仓库跑的离线测试。 |
| `.github/workflows/sync-skills.yml` | 每周同步并开 PR。 |
| `sources.json` | 每个技能的来源 URL、commit、SPDX。 |

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

`upstreams.json` 是输入，`upstreams.lock.json` 是输出。lock 让同步可复现，也让
本地改动不会被静默覆盖。

## 3. 配置：`upstreams.json`

当前配置（节选）：

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
        "frontend-design": "Apache-2.0",
        "mcp-builder": "Apache-2.0",
        "skill-creator": "Apache-2.0",
        "webapp-testing": "Apache-2.0"
      },
      "licenseFile": "LICENSE.txt",
      "paths": ["skills"],
      "include": [
        "frontend-design",
        "mcp-builder",
        "skill-creator",
        "webapp-testing"
      ]
    },
    {
      "id": "archify",
      "repo": "tt-a1i/archify",
      "track": "tag:v3.2.0",
      "license": "per-skill",
      "licenses": { "archify": "MIT" },
      "licenseFile": "LICENSE",
      "paths": ["archify"],
      "include": ["archify"],
      "excludePaths": ["examples/*.html"]
    }
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 稳定标识，用于 lock 和 `sources.json`；不要改名。 |
| `repo` | 是 | `owner/name`，只接受 GitHub 风格路径。 |
| `track` | 是 | `branch:main`、`tag:v1.2.3` 或 40 位 commit SHA。 |
| `license` | 是 | SPDX 标识（repo 级）或 `per-skill`。 |
| `licenses` | `per-skill` 时 | 技能名 → SPDX；显式 `include` 的技能必须有条目。 |
| `licenseFile` | 是 | repo 级时相对仓库根；`per-skill` 时相对每个技能目录。 |
| `paths` | 是 | sparse checkout 的目录，也是搜索 `SKILL.md` 的范围；`"."` 表示仓库根。 |
| `include` | 是 | 技能名或 glob；显式白名单，**空数组表示不导入**。 |
| `exclude` | 否 | 优先于 `include`。 |
| `excludePaths` | 否 | 技能目录内的相对路径 glob；命中的文件不 vendor，用于排除上游生成产物。 |
| `rename` | 否 | 暂未实现；写了非空值会被脚本拒绝。 |
| `enabled` | 否 | 默认 `true`；设为 `false` 可暂停某个 upstream。 |

规则：

- `include` 不建议写 `*`。上游新增的技能会在摘要里列为 “available but not
  imported”；人工确认后再加。只有很小、完全信任的 repo 才考虑 `*`。
- `per-skill` 模式下，显式 `include` 的技能缺少 `licenses` 条目是配置错误；
  glob 匹配到但没列许可证的技能会被跳过并报告。
- 同一技能名不能出现在两个 upstream 的显式 `include` 里；glob 产生的重名在
  同步时检测并中止。
- 上游 frontmatter 里的 `license` 不是 SPDX（例如
  `license: Complete terms in LICENSE.txt`）时，同步脚本会把它**改写**为配置
  里的 SPDX；这是唯一会改动的 frontmatter 行。
- 同步只接受 `MIT`、`Apache-2.0`、`BSD-2-Clause`、`BSD-3-Clause`、`ISC`、
  `CC0-1.0`。GPL/AGPL、`NOASSERTION`、source-available、没有许可证的仓库
  一律跳过。
- `excludePaths` 用 `fnmatch` 匹配技能目录内的 POSIX 相对路径，命中的文件
  不复制、也不进 lock。用来排除上游签入的生成产物（如演示 HTML），避免把
  “源码”和“构建结果”一起 vendor；不要用它排除技能运行所需的文件。

## 4. 锁文件与来源

`upstreams.lock.json` 形如：

```json
{
  "schemaVersion": 1,
  "upstreams": {
    "anthropic": {
      "repo": "anthropics/skills",
      "track": "branch:main",
      "commit": "dbd4588f9e1033efb41dad4bef2f7947c8993d44",
      "skills": {
        "frontend-design": {
          "path": "skills/frontend-design",
          "license": "Apache-2.0",
          "files": {
            "LICENSE.txt": "0d542e...",
            "SKILL.md": "e385a2..."
          }
        }
      }
    }
  }
}
```

- lock 由 sync 生成，不要手改。
- `commit` 是实际 checkout 的 HEAD，不是 `main` 的别名。
- `files` 是 vendored 之后的哈希（含补写/改写的 `license` 行和复制的许可证），
  不是上游原始文件。
- 不写时间戳，commit 不变时 lock 逐字节稳定；JSON 键按 id/技能/文件名排序。
- 上游删除技能、或从 `include` 移除技能时，默认保留本地文件并在摘要里标记
  orphaned；`--prune` 只删除与 lock 完全一致的目录。保留的 orphan 仍留在 lock
  里，方便下次继续提示或清理。
- `sources.json` 的 vendored 条目：

```json
{
  "source": "https://github.com/anthropics/skills/tree/<commit>/skills/frontend-design",
  "license": "Apache-2.0",
  "upstream": "anthropic",
  "commit": "<commit>",
  "path": "skills/frontend-design"
}
```

- 同步条目不写 `importedAt`，避免 commit 不变时产生无意义 diff；手工导入
  （`import.py`）仍保留该字段。
- 手工导入的 `source` 仍是本机路径；公开仓库前改成来源 URL。

## 5. 运行同步

```sh
python3 scripts/sync_upstreams.py --dry-run          # 只打印计划
python3 scripts/sync_upstreams.py --upstream anthropic
python3 scripts/sync_upstreams.py                    # 全部 enabled 的 upstream
python3 scripts/sync_upstreams.py --prune            # 清理上游已移除的技能
python3 scripts/sync_upstreams.py --check            # 只比较 lock 与上游 commit
```

`--dry-run` 也会 clone 并跑许可证/可移植性检查，所以能看到真实 skip 原因，但
不写 `skills/`、lock 或 sources。

一次正常同步：

1. 读配置，校验 schema；拒绝重复 `id`、未知字段、非白名单许可证、显式技能
   重名。
2. 解析 `track`：40 位 SHA 直接用；`branch:`/`tag:` 用 `git ls-remote` 解析
   （annotated tag 取 `^{}`）。
3. `git init` + `fetch --depth 1`（按 commit 或 ref）+ sparse checkout 到临时
   目录；repo 级许可证和 `NOTICE` 也会加入 sparse 范围。
4. 在 `paths` 下递归找 `SKILL.md`，跳过 `.git`、`node_modules`、`__pycache__`、
   `.venv`、`dist`；技能目录里的嵌套 `SKILL.md` 视为示例，不再向下发现。
5. 按 `include`/`exclude` 过滤；检查 frontmatter 允许字段、`name`/目录一致、
   description 长度、相对链接、`SKILL.md` 行数。
6. license gate：SPDX 白名单、许可证文件存在且非空、复制 `LICENSE`/`NOTICE`，
   必要时补写或改写 `license:`。
7. 漂移检查：本地文件与 lock 不一致时默认中止；`--force` 才覆盖。
8. 复制到 `skills/<name>/`，先应用 `excludePaths`，保留可执行位；给
   `scripts/` 下的 `.sh`/`.bash`/带 shebang 的文件补 `+x`；拒绝符号链接和
   超大文件。
9. 更新 lock/sources，输出 added / updated / unchanged / skipped / orphaned /
   available-not-imported。

| 参数 | 作用 |
| --- | --- |
| `--dry-run` | 只打印计划，不写文件。 |
| `--upstream ID` | 只处理一个 upstream；也用于对 disabled 的 upstream 执行 `--prune`。 |
| `--force` | 覆盖本地漂移；先提交，Git 可恢复。 |
| `--prune` | 删除上游已移除、且本地未改动的技能；默认保留并报告。 |
| `--summary PATH` | 写 Markdown 摘要，给 PR body 用。 |
| `--check` | 只解析 commit 并与 lock 比较；过期返回 1。 |
| `--repo-base URL` | 覆盖 git base（默认 `https://github.com`；测试/fork 用）。 |

`--check` 不 clone、不检查本地文件，只回答“上游 HEAD 是否还是 lock 里的
commit”。本地漂移由正常同步检查。

有 fatal error（clone 失败、配置错误、本地漂移、重名/未托管路径）时返回 1，
且**不写 `skills/`、lock 或 sources**（`--summary` 仍会写）；只有 skip/warning
时返回 0。

本地手动流程：

```sh
python3 scripts/sync_upstreams.py --upstream anthropic --dry-run
python3 scripts/sync_upstreams.py --upstream anthropic
python3 scripts/validate.py --strict
python3 scripts/catalog.py
python3 tests/test_sync.py
sh tests/test-install.sh
```

## 6. 四道闸门

| 闸门 | 检查 | 失败处理 |
| --- | --- | --- |
| License | SPDX 白名单、LICENSE/NOTICE、逐技能核对 | 跳过该技能并在摘要里报告 |
| Portability | frontmatter、命名、链接、脚本、交叉引用 | 跳过并报告 |
| Security | 危险脚本、密钥、二进制、人工 review | 警告；PR 必须人工看 |
| Drift | lock 哈希 | 中止；`--force` 才覆盖 |

### 6.1 License

- 只收：`MIT`、`Apache-2.0`、`BSD-2-Clause`、`BSD-3-Clause`、`ISC`、
  `CC0-1.0`。
- 必须**逐技能**看许可证文件，不能只看 repo 的 license 字段；`licenseFile`
  对 `per-skill` 是相对技能目录，对 repo 级是相对仓库根。
- 按原文件名复制 `LICENSE`/`LICENSE.txt`/`NOTICE` 到技能目录根；如果许可证
  文件本来就在技能目录里，同步不会重复复制。
- 上游没有 `license` frontmatter 的，sync 补一行；已有但不是 SPDX 的，改写
  成配置值。正文不动。
- `sources.json` 记 SPDX + commit；`license` 字段和目录里的许可证文件是给人
  复核的，不是法律意见。

### 6.2 Portability

- frontmatter 只允许 `name`、`description`、`license`、`allowed-tools`、
  `metadata`、`version`；`context`、`hooks`、`agent`、`model` 等会跳过。
- `name` = 目录名，小写字母/数字/单连字符，≤64 字符；description ≤1024。
- 相对链接必须能打开；`references/`、`scripts/`、`assets/` 下的断链会跳过。
  行内代码和 fenced code 块里的链接示例不算链接，不会被误报。
- `SKILL.md` 超过 500 行、技能目录超过 25 MB、单文件超过 10 MB、包含符号
  链接会被跳过。
- `scripts/` 下的 shell/带 shebang 脚本没有可执行位时，同步补 `+x`；没有
  shebang 的 Python 模块不强制可执行（`validate.py` 也按这个规则检查）。
- 交叉引用：如果正文出现 `superpowers:writing-plans` 这类“上游 id/技能名:
  技能名”的命名空间引用，而本仓库是扁平安装，脚本会跳过并报告。整套导入并
  改写，或直接不收。

### 6.3 Security

脚本会扫描 `curl | sh`、`wget | sh`、`eval`、递归 `rm`、`chmod 777`、
`base64 -d | sh`、私钥、AWS key 和二进制文件，把命中写进摘要；这些是
**warning，不会自动阻止**。合并前必须人工看：

- 新增/修改的 `scripts/`；
- PR 里 diff 较大的技能；
- 来源是否可信（优先官方 org）。

不 auto-merge；`CODEOWNERS` 覆盖 `skills/`。

### 6.4 Drift

- lock 记录每个文件的 SHA-256；本地改动后下次 sync 默认中止。
- 想修本地漂移：`git diff` 确认后 commit，或 `--force` 让上游版本覆盖。
- 上游删除技能：默认保留并标记 orphaned；`--prune` 只删未改动的。
- 想本地定制：不要改 `skills/<name>/`，复制成 `skills/<name>-local`，改
  frontmatter 的 `name`，并移出 `include`；否则下次同步会冲突。
- `--force` 只覆盖 lock 里登记过的技能，不会覆盖未托管的同名目录。

## 7. CI

[.github/workflows/sync-skills.yml](../.github/workflows/sync-skills.yml) 每周一
03:00 UTC（北京时间 11:00）运行，也支持手动触发：

```yaml
on:
  schedule:
    - cron: "0 3 * * 1"
  workflow_dispatch:
```

流程是：同步 → `validate.py --strict` → `catalog.py` → 安装器测试 → sync 测试
→ `peter-evans/create-pull-request` 开/更新 `chore/sync-skills` PR。没有变更时
不建 PR。

**Token 坑**：用默认 `GITHUB_TOKEN` 开的 PR 不会触发
[validate.yml](../.github/workflows/validate.yml) 的 `pull_request` 事件
（GitHub 防递归）。workflow 用 `secrets.SYNC_TOKEN || github.token`：

1. 配 `SYNC_TOKEN`（fine-grained PAT，`Contents: Read and write` +
   `Pull requests: Read and write`），PR 的正常检查会跑。
2. 不配 token，退化为 `GITHUB_TOKEN`：sync job 自己跑完 validate/catalog/test，
   但 PR 上不会出现 `validate` check。

分支保护建议：要求 review，禁止 auto-merge；`CODEOWNERS` 覆盖 `skills/`。
第三方 action 已 pin 到 `peter-evans/create-pull-request` v8.1.1 的 SHA。

## 8. 接入新上游

1. 在候选仓库里确认许可证：读实际 `LICENSE` 文件，确认技能目录也有许可证；
   不接受 GPL/AGPL/source-available。
2. 在 `upstreams.json` 加一个条目。先用 `--dry-run`：

   ```sh
   python3 scripts/sync_upstreams.py --upstream getsentry --dry-run
   ```

3. 看摘要里的 skipped/orphaned/security；确认 `include` 只含审核过的技能。
4. 正式同步后检查 `git diff`、`sources.json`、`upstreams.lock.json`。
5. 跑：

   ```sh
   python3 scripts/validate.py --strict
   python3 scripts/catalog.py
   python3 tests/test_sync.py
   sh tests/test-install.sh
   ```

6. 提交 PR。不要 auto-merge。

上游新增技能时，**不需要改脚本**：把技能名加进现有 upstream 的 `include`，
重新同步；没加进 `include` 的技能会出现在摘要的 “available but not imported”。

## 9. 移除或定制上游

**暂停**：

```json
{ "id": "foo", "enabled": false }
```

正常同步会跳过它，本地技能保持不动。**清理**：

```sh
python3 scripts/sync_upstreams.py --prune --upstream foo
```

disabled 的 upstream 仍参与 `--prune`；未改动的技能和 lock/sources 记录会被
删除，改过的会保留并报告。确认删干净后再从配置里删条目。

**完全移除**：先从配置删条目，再 `--prune`；脚本会把 lock 里孤立的 upstream
记录也当作 orphan 处理。

**本地定制**：复制成新目录和新 `name`，移出 `include`，不要改 vendored 目录。

## 10. 当前状态

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| A | 配置、sync 脚本、lock、workflow、docs、测试 | 已完成 |
| B | 接入 `anthropics/skills` 的 4 个 Apache-2.0 技能 | 已完成 |
| B2 | 接入 `tt-a1i/archify` v3.2.0（MIT），排除 15 个生成的演示 HTML | 已完成 |
| C | 接入 `getsentry/find-bugs` 等通用技能 | 待按需 |

暂缓：`rename`、GitHub App、自动发现、`skills/vendor/` 分层。当前扁平
`skills/<name>/`，安装器会照常扁平化。

候选 upstream（许可证核对时间 2026-10-24，接入前重新看一遍）：

| upstream | license | 规模/路径 | 建议 |
| --- | --- | --- | --- |
| `anthropics/skills` | 混合，逐技能 | `skills/` | 已接 4 个 Apache-2.0；文档技能是 source-available，排除 |
| `vercel-labs/skills` | MIT | `skills/find-skills` | 可选；依赖 `npx skills` 和网络 |
| `obra/superpowers` | MIT | `skills/`，15 个 | 整套导入并改写 `superpowers:` 引用，或跳过 |
| `getsentry/skills` | Apache-2.0 | `skills/`，约 30 个 | `find-bugs` 较通用；其余偏 Sentry |
| `microsoft/skills` | MIT | `.github/skills`，170+ | 按需；Azure/Foundry 专用 |
| `cloudflare/skills` | Apache-2.0 | `skills/`，约 20 个 | 按需；Workers/Cloudflare 专用 |
| `prisma/skills` | MIT | 仓库根目录 | 按需；Prisma 专用 |
| `openai/skills` | 逐技能 | `skills/` | 跳过；README 已标 deprecated |

`tt-a1i/archify` 细节：

- 锁定 `tag:v3.2.0`（MIT）；技能在仓库的 `archify/` 子目录，运行时需要
  Node.js ≥ 18。
- 上游每个版本签入 15 个约 12 MB 的演示 HTML（`examples/*.html`）；用
  `excludePaths` 排除后技能包约 5.5 MB，JSON 示例、schema 和渲染器完整保留。
- `renderers/shared/diagnostics.mjs` 用字面 NUL 字节拼接去重键，安全扫描会
  报 “binary file”；已人工确认只是 JS 字符串分隔符，不是可执行载荷。

`anthropics/skills` 细节：

- 已接：`skill-creator`、`mcp-builder`、`webapp-testing`、`frontend-design`。
- `skill-creator` 的 `SKILL.md` 约 486 行，接近 500 行上限；上游再长会被
  `--strict` 拦。
- 排除：`docx`、`pdf`、`pptx`、`xlsx` 是 source-available；
  `doc-coauthoring` 没有 `LICENSE.txt`。

## 11. 测试

[tests/test_sync.py](../tests/test_sync.py) 用本地 `file://` 裸仓库，不访问
网络、不需要 token。覆盖：

- 新增技能 → 文件、lock、sources 正确；
- 第二次同步 → unchanged，lock 逐字节稳定；
- 上游 commit 更新 → `--check` 返回 1，同步后 updated；
- `tag:` 与 40 位 commit 都能解析并固定到正确版本；
- 本地改动 → 报错，`--force` 才覆盖；
- 缺许可证 / 不可移植 frontmatter → 跳过并继续；
- 重名 → 中止且不写文件；
- 上游删除技能 → 默认保留，`--prune` 删除未改动的；
- disabled upstream → 跳过，`--prune` 可清理；
- `--dry-run` → 不写文件，摘要仍生成；
- 非法配置 → 失败关闭。

CI 在 Ubuntu/macOS 上都会跑这套测试。

## 12. 维护约定

- 每周看一次 sync PR：许可证、`scripts/`、diff 大小；没问题再 merge。
- vendored 技能视为只读；要定制就 fork 成新名字，并移出 `include`。
- `upstreams.lock.json` 必须提交；`sources.json` 的 `source` 用 URL，不写本机
  绝对路径。
- 定时任务只读默认分支；改完 `upstreams.json` 要先 merge 到 `main`，或者本地
  手动跑 sync 再提 PR。
- 手工导入的本地技能用 `import.py`，不要在 `upstreams.json` 里假装是上游。

## 13. 待定

- 导入技能放 `skills/<name>/` 还是 `skills/vendor/<name>/`：安装器都会扁平化，
  但 `name` 仍需全局唯一。先用扁平，数量多了再分类。
- 是否接受 GPL/AGPL：目前不收，避免许可证混杂。
- `rename` 与命名空间：会改 frontmatter、可能断交叉引用，暂缓。
- 有兄弟技能依赖的上游（如 `superpowers`）：整套导入并改写，或直接跳过。
- Token 用 PAT 还是 GitHub App：个人仓库先用 PAT，轮换成本低。

## 14. 参考

- [docs/authoring.md](authoring.md) — 技能编写约定
- [docs/offline.md](offline.md) — 离线打包与安装
- [scripts/import.py](../scripts/import.py) — 手工导入本地目录
- [scripts/sync_upstreams.py](../scripts/sync_upstreams.py) — 上游同步
- [scripts/validate.py](../scripts/validate.py) — frontmatter/链接/卫生检查
- [scripts/catalog.py](../scripts/catalog.py) — README/catalog 生成
- [tests/test_sync.py](../tests/test_sync.py) — 离线 git fixture 测试
- [.github/workflows/validate.yml](../.github/workflows/validate.yml) — 现有 CI
- [.github/workflows/sync-skills.yml](../.github/workflows/sync-skills.yml) — 定时同步
- [Agent Skills](https://agentskills.io)、[skills.sh](https://skills.sh)
- [anthropics/skills](https://github.com/anthropics/skills)
- [tt-a1i/archify](https://github.com/tt-a1i/archify)
