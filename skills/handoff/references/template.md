# Handoff — <short task name>

> Date: YYYY-MM-DD · Branch: `<branch>` · Commit: `<sha>` · Author: `<agent/human>`

## Goal

<What "done" means, in one paragraph. Include the user-visible outcome.>

## State

- **Done (verified):** <item> — evidence: `<command>` / `<path>`.
- **In progress:** <item> — current behaviour, expected behaviour, repro.
- **Blocked:** <item> — blocker, owner, what unblocks it.

## Decisions

| Decision | Why | Rejected alternative |
| --- | --- | --- |
| <choice> | <reason> | <option and why not> |

## Key files

| Path | Why it matters |
| --- | --- |
| `path/to/file:123` | <the function/constant that matters> |
| `path/to/test` | <what it proves> |

## Commands

Run from `<working directory>`:

```sh
# reproduce current state
<command>

# run the focused test
<command>
```

## Next actions

1. **<imperative step>** — done when: <observable check>.
2. **<imperative step>** — done when: <observable check>.

## Open questions

- <question> — needs: <person/service>; workaround: <if any>.

## Environment

- Branch: `<branch>` · Commit: `<sha>`
- Runtime: `<version>` · Package manager: `<version>`
- Required env var names: `<NAME>` (values not recorded)
- Services: <running/needed>
