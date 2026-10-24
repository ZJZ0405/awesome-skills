---
name: handoff
description: Write a compact, source-linked handoff note so another session, agent, or machine can resume the work without re-reading the whole conversation. Use when the user says handoff, 交接, 换会话, 继续做, resume later, or when a long session is ending.
license: MIT
metadata:
  version: "1.0.0"
  author: ZJZ0405
---

# Handoff

Produce one durable note that lets a fresh agent continue the work with the
same context — no transcript archaeology required.

## When to use

- The user is ending a long session, switching agents, or moving to another machine.
- The user says "handoff", "交接", "写个总结给下一个会话", "continue later", "resume tomorrow".
- Work is mid-flight and the next step depends on decisions that are not in the code yet.

Do **not** use this for a status update that fits in the chat reply. If the user
only asks "what did we do?", answer inline. This skill is for a file that will
outlive the session.

## Rules

1. Default output path: `HANDOFF.md` in the repository root. Honor a different
   path if the user names one.
2. Never include secrets, tokens, cookies, private URLs, or customer data.
   Quote environment variable *names* instead of values.
3. Separate **verified** facts from **assumptions**. If you did not run a command
   or read a file in this session, mark it as unverified.
4. Cite concrete paths, functions, commands, and commit hashes. "The parser" is
   not a location; `src/parser.ts:142` is.
5. Keep it under ~100 lines. A handoff that takes longer to read than the work
   is a failed handoff.
6. Write for a competent stranger: no "as discussed above", no pronouns that
   depend on the chat.

## Workflow

1. **Collect evidence.**
   - `git status --short`, `git log --oneline -10`, and the diff of uncommitted work.
   - The task state: goal, acceptance criteria, what is done, what is blocked.
   - Commands that were actually run and their outcomes.
   - Any decision made for a non-obvious reason.
2. **Pick the next action.** Name one primary next step and 1–3 follow-ups,
   each small enough to start without asking a question.
3. **Write the note** with the sections below. Omit a section only if it would
   be empty; do not leave placeholder text.
4. **Verify before reporting.**
   - Every command is copy-pasteable from the stated working directory.
   - Every path exists (or is clearly marked as "to be created").
   - No secret values slipped in.
   - The next action is unambiguous.
5. **Report** the file path and the single most important next action.

## Required sections

| Section | Content |
| --- | --- |
| Goal | One paragraph: what "done" means and why it matters. |
| State | Done / In progress / Blocked, with evidence for each. |
| Decisions | Choices made and the reason; include rejected options when they will be re-litigated. |
| Key files | Path + why it matters. Include line numbers for the hot spots. |
| Commands | Exact commands to reproduce the current state, in order. |
| Next actions | Numbered, imperative, each with a definition of done. |
| Open questions | Only real blockers; say who can answer them. |
| Environment | Branch, commit, tool versions, services, credentials *names* needed. |

See [references/template.md](references/template.md) for a ready-to-fill skeleton.

## Example (abbreviated)

```markdown
# Handoff — offline installer

## Goal
Ship `install.sh` that works on an air-gapped macOS machine with no Node/Python.

## State
- Done: agent path table, hub+link install, tests for link/copy (verified: `sh tests/test-install.sh`).
- In progress: uninstall leaves empty tool dirs (repro: install then `uninstall --all`).
- Blocked: none.

## Decisions
- POSIX sh over Python: target machines may have neither Node nor Python.
- Hub at `~/.agents/skills`: shared by Codex/Cline/Zed/Warp; per-tool dirs symlink to it.

## Next actions
1. Remove empty dirs after uninstall; add regression test. Done = test fails before, passes after.
```

## Anti-patterns

- A chronological diary ("first I tried…, then…"). Write state, not history.
- Copying the whole diff into the note. Link to the commit or file instead.
- "Continue the task" with no definition of done.
- Hiding uncertainty. Mark it; the next agent can resolve it faster than you think.
