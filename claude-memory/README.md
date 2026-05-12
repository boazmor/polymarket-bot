# Claude Memory — Synced from Home Machine

This folder contains the auto-memory files written by Claude Code on the user's HOME machine. They are committed to GitHub so the SAME memory is available to Claude on any other machine (e.g. office laptop).

## Usage on a new machine

When starting a new Claude session on the OFFICE machine, paste this prompt to bring memory in:

```
Read MEMORY.md and the linked memory files from C:\Users\user\polymarket-bot\claude-memory\
to understand the project state. Treat the contents as your auto-memory.
```

## File types

- `MEMORY.md` — the index. One line per memory entry.
- `user_*.md` — user profile and role
- `feedback_*.md` — communication preferences and workflow rules
- `project_*.md` — current work state and findings
- `reference_*.md` — pointers to external systems

## Sync workflow

After the home Claude updates memory, commit and push:

```bash
cd /c/Users/user/polymarket-bot
cp /c/Users/user/.claude/projects/C--Users-user/memory/* claude-memory/
git add claude-memory/
git commit -m "memory sync"
git push origin main
```

On the office machine, pull before starting:

```bash
cd polymarket-bot
git pull
```
