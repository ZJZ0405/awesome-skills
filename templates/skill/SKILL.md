---
name: your-skill
description: One or two sentences describing what this skill does and, most importantly, when an agent should load it. Include the words users actually say.
license: MIT
metadata:
  version: "1.0.0"
  author: your-name
---

# Your Skill

One paragraph: the outcome this skill produces.

## When to use

- Trigger 1 (include literal phrases users type, in any language).
- Trigger 2.
- Do **not** use for: <near-miss cases>.

## Workflow

1. Gather the minimum evidence needed. Name the files/commands.
2. Do the work in a deterministic order.
3. Verify against concrete checks.
4. Report in the format below.

## Rules

- Hard constraint 1.
- Hard constraint 2 (what never to do).
- How to handle missing information: ask, or state the assumption.

## Output format

```markdown
## Result
- ...
```

## References

- `references/checklist.md` — load only when the task needs it.
- `scripts/helper.sh` — usage and exit codes.
