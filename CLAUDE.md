# CLAUDE.md — Polymarket BTC 5-min bot

This file is the context bridge for any Claude session joining this project on any machine. **Read it before doing anything.** For deeper detail, see `claude-memory/*.md` (the project's persistent memory files).

## The user (Boaz)

- Learned programming ~40 years ago (COBOL era). Not an active developer today. Strong on the trading-strategy domain; relies on AI for the code itself.
- Started this bot with Gemini, moved to ChatGPT, now also using Claude Code.
- **Reply to the user in Hebrew. Minimize English jargon — when an English term is unavoidable, briefly explain it in plain Hebrew on first use.**
- Email: `morboaz18@gmail.com`. GitHub: `boazmor`.

## The project

**Polymarket Bitcoin 5-minute markets.** Buy-only bot — never sells; holds positions until each 5-minute market resolves.

**Thesis:** Buy at the start of each 5-minute window at low prices implying ~200% potential return. Position size $100. Because winners pay roughly 3x, a low-win-rate strategy can still be net-positive in dollars.

**Three-phase design:**
1. **0–30 s** — limit buys at price ≤ 0.30, even with small "distance".
2. **30–40 s** — top-up buys, willing to pay up to 0.35.
3. **40–120 s** — buy only when "distance" is high (the right threshold is being researched).

**Critical empirical finding (load-bearing rule):**
"Distance" = `target_price - bitcoin_price`. Two BTC sources have been tested:
- **Binance** (`wss://stream.binance.com:9443/ws/btcusdt@trade`) — bot is **profitable**.
- **Polymarket / Chainlink RTDS** (`wss://ws-live-data.polymarket.com`, `crypto_prices_chainlink`, `btc/usd`) — bot **loses money**.

The likely cause (measured 29/04 from `data_ws_chainlink_research/rtds_ticks.csv`): Chainlink feed has a median latency of **~1.2 seconds** (p90 1.5s, p99 1.9s), while Binance is effectively real-time. In a strategy where the first 30 seconds matter, a 1.2s lag means trading on stale prices. **Use Binance for distance.**

## Status

- The bot is **simulation only** — no live trading yet.
- Two pre-trading blockers: (1) wallet swap — Polymarket's built-in wallet can't trade via bot, an external wallet must be set up; (2) joint-feed recorder — both BTC prices need to be recorded simultaneously to validate the latency hypothesis.
- Latest dataset (29/04): 38 hours of recording, 465 markets, 100 % target capture. Of 464 resolved markets DOWN won 68.5 % vs UP 31.5 % — a strongly down-trending period that biases prior loss numbers.

## Three locations

- **Home machine:** development; holds `.env`; has `~/.ssh/id_ed25519_hetzner` for passwordless SSH to the server.
- **Office machine:** development; holds the latest bot code as of 28–29/04/2026; needs its own SSH key set up next time we work there.
- **Hetzner VPS in Germany** — `178.104.134.228`, Ubuntu, Python 3.12, root user. Many bot generations live in `/root/`. Two recorders:
  - `/root/data_ws_binance_poly_research/` — Binance + Polymarket order book (combined_per_second.csv 62 MB; raw_poly_messages.jsonl 40 GB; total 41 GB)
  - `/root/data_ws_chainlink_research/` — Chainlink/Polymarket BTC (~22 MB)

The two recorders **never ran simultaneously** in the existing data — joint re-run is needed for direct two-source comparison.
**Disk on server is 73 % full (52 GB / 75 GB used). Cleanup of the 40 GB raw jsonl file is becoming urgent.**

## Working rules — strict

These have all come from real incidents and are non-negotiable:

1. **Never deliver partial bots.** Always a complete, runnable single file.
2. **Never silently change tactics or strategy.** Explain and get approval before adjusting parameters or logic.
3. **Don't use `nano` to edit files on the server.** Prefer scp/upload of complete files, or `vim` if needed.
4. **Structure every server-related instruction in two sections labelled `מחשב` (computer) and `שרת` (server).** That's the user's mental model.
5. **Always upload to the server first, then run there.** Never run locally as a substitute.
6. **Every bot must include a research unit** that produces CSV reports.
7. **Every run must clean its old report files first** (the existing recorders do `shutil.rmtree(data_dir)` on startup).
8. **Optimization metric is total dollar PnL, NOT win rate.** A low-win-rate, high-upside rule can beat the opposite.
9. **Solo developer.** No PRs, no protected branches, no team CI. Push directly to `main`.
10. **Minimize friction.** Offer the simplest path first; do work via tools rather than asking the user; be honest about CLI limitations and suggest claude.ai web for image-heavy tasks.
11. **Never commit secrets.** `.env` and SSH keys are in `.gitignore`. Never paste private keys / API secrets / wallet seeds in chat or print them in logs.

## How to use the memory

The full memory lives in `claude-memory/*.md`. On a new machine:

```
cp -r claude-memory/*.md ~/.claude/projects/<project-folder>/memory/
```

(On Windows the path is `C:\Users\<user>\.claude\projects\<project-folder>\memory\`.)

After copying, Claude on that machine will pick up the full context automatically in the next session.

## Outstanding work as of 29/04/2026

- Push the latest bot code from the office machine to this repo.
- Build a unified recorder bot that records Binance BTC + Polymarket Chainlink BTC + UP/DOWN order book + market outcome in one CSV per second. Then run it for 24+ hours to get a synchronized dataset.
- Decide what to do with the 40 GB raw_poly_messages.jsonl on the server (delete or archive).
- Validate the Chainlink-latency hypothesis empirically once joint data exists.
- Eventually: wallet swap + go-live for real trading.
