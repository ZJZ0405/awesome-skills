# installer/ — 可选的 Python 安装器

这是本仓库安装器架构的 Python 实现（Python 3.9+，只用标准库，不联网），
与 `bin/awesome-skills` 并列。它保留同样的 hub + 镜像模型，并额外提供：

- 按内容哈希判断幂等（重复安装不重写）；
- `check` 逐条检查记录漂移（缺失、被替换、目录迁移、copy 过期），有漂移退出码 1；
- 状态清单 `state.json`，卸载只删自己写过的路径；
- 共享目录保护：多个工具共用一个 skills 目录时，最后一个工具卸载才删路径。

```sh
./installer/bin/awesome-skills-py                  # interactive guide (TTY)
./installer/bin/awesome-skills-py install --tool auto
./installer/bin/awesome-skills-py list --installed
./installer/bin/awesome-skills-py check
./installer/bin/awesome-skills-py uninstall --all
```

On a terminal, running it bare (or `install` without `--tool`/`--skill`) opens a
clack-style guide — the same `┌ │ └`, `◆`/`◇`, arrow-key multiselect and note
box that codegraph's installer draws — through scope, agents, skills, mode and
a dry-run plan (popular agents first, the project directory is prefilled, Tab
completes paths). TTY installs with flags and uninstalls use the same frames
without prompting. Use `-y` in scripts/CI; non-TTY sessions keep the
non-interactive `auto` path.

从源码运行，或 `pip install -e installer` 后直接用 `awesome-skills-py`：

```sh
PYTHONPATH=src python3 -m awesome_skills --help
```

完整设计、状态格式、与 shell 安装器的差异见
[../docs/python-installer.md](../docs/python-installer.md)。

测试：

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

主安装器仍然是不需要 Python 的 `../bin/awesome-skills`；这个包是可选实现，
不参与 shell 安装器的运行路径。
