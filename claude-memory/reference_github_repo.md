---
name: GitHub repo for the Polymarket bot
description: Location of the user's private GitHub repository for the Polymarket BTC 5-min bot
type: reference
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
The user's Polymarket bot code lives in a private GitHub repository:

- **URL:** https://github.com/boazmor/polymarket-bot.git
- **Username:** boazmor
- **Repo name:** polymarket-bot
- **Visibility:** Private
- **Created:** 2026-04-28
- **First commit pushed: 2026-04-29** from the home machine. Initial commit `526a671` contains: `CLAUDE.md` (context bridge), `README.md`, `.gitignore` (protects .env, SSH keys, *.csv, *.jsonl, /server_recordings*/, /data*/), and `claude-memory/` with all 11 memory files copied as a backup/sync mechanism.
- **Local working copy on home machine:** `C:\Users\user\polymarket-bot\`
- **Auth:** Git Credential Manager handled it transparently on first push — no PAT/SSH-key setup needed manually. Future pushes from home work without re-prompt.
- **Bot code is NOT yet in the repo.** It will be added next from the office machine.

This repo is the sync point between the user's home machine, office machine, and the Hetzner server. Office holds the source-of-truth bot code; home contributed the meta files in commit `526a671`. The Hetzner server doesn't pull from this repo yet — code is uploaded there manually via scp.
