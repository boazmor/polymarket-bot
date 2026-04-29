---
name: Minimize friction in workflows — user dislikes multi-step processes
description: User finds multi-step workflows (especially around sharing screenshots, file transfers, etc.) frustrating and wants simple, low-friction interactions
type: feedback
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
The user explicitly stated that processes like "transferring a screenshot is intensive work" for them, and that things must be "simple and accessible without many unnecessary actions." They are used to web tools (ChatGPT, Gemini) where pasting an image is one keystroke, and finds CLI/terminal friction frustrating.

**Why:** The user is not a developer and processes that a coder would consider trivial (drag-and-drop a file, save-then-reference-by-path, multi-step terminal flows) feel heavy and slow them down. They came to Claude Code hoping it would be *less* friction than ChatGPT, not more.

**How to apply:**
- Always offer the simplest possible path first; only escalate to multi-step workflows if simple doesn't work.
- When a task naturally requires several steps, do as much as possible *for* the user via tools (file reads, automation) rather than asking them to do it.
- If something is genuinely friction-heavy in the CLI (image paste, etc.), be honest about the limitation and suggest using claude.ai web/desktop app for that specific task — don't pretend the CLI is good at everything.
- Default to "describe in words" before asking for a screenshot. Often a description is enough.
