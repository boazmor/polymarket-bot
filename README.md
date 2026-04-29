# polymarket-bot

Personal repo for Boaz's Polymarket BTC 5-minute trading bot.

The actual bot code is uploaded to this repo from the office machine. Right now (29/04/2026) the repo holds only the project's Claude memory and the context-bridge file `CLAUDE.md`. Bot code will be added next.

## Layout

- `CLAUDE.md` — read this first. Project context for any AI assistant joining the project.
- `claude-memory/` — Claude memory files. To use them on a new machine, copy into `~/.claude/projects/<project-folder>/memory/`.
- (forthcoming) bot code — Python files that run on the Hetzner server.

## Working machines

- **Home:** development.
- **Office:** development; holds the latest code as of 29/04/2026.
- **Hetzner (Germany), `178.104.134.228`:** runs the bot in simulation. Connect with `ssh hetzner` (passwordless via key on the home machine).

Secrets (`.env`, SSH keys) are gitignored and never leave the machine they belong to.
