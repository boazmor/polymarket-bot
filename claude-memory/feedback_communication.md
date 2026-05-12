---
name: Communication style — Hebrew, simple, minimal English jargon
description: How to communicate with the user in chat — language, tone, vocabulary
type: feedback
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
Reply to this user in Hebrew. Minimize English technical terms — when an English term is unavoidable, briefly explain it in plain Hebrew on first use. Use simple, plain Hebrew explanations rather than dense technical language.

**Why:** The user explicitly requested this. They learned programming 40 years ago (COBOL era) and is no longer fluent in modern coding terminology. Heavy English jargon makes the conversation harder to follow.

**How to apply:** All chat responses to the user — explanations, status updates, questions. Code itself, file paths, command names, and library names stay in English (they have to). When discussing concepts (e.g., "limit order", "API", "git pull"), use the term but add a short Hebrew clarification when it first comes up in a session.

## ⚠️ CRITICAL — RTL display issue (added 2026-05-01)

**Mixing English words inside Hebrew sentences flips the perceived word order on the user's screen** because Hebrew is right-to-left and English-named labels (V3, LIVE, BOT40, etc.) inserted into Hebrew text get rendered in a way that REVERSES the meaning to the reader.

Example of what happened: I wrote "**LIVE מנצח את V3**" (intending "LIVE beats V3"). The user read it as "**V3 מנצח את LIVE**" (V3 beats LIVE) — the OPPOSITE meaning, because the English tokens were positioned ambiguously when displayed.

**The rule:** Use Hebrew names for everything in flowing text:
- "LIVE bot" → "**הבוט החי**" or "**הבוט החדש**"
- "V3" → "**הבוט הניסיוני**" or "**וי-3**" (with hyphen so RTL doesn't reverse it)
- "BOT40" → "**בוט 40**"
- "BOT120" → "**בוט 120**"
- "ROI" → "**אחוז תשואה**"
- "Win rate" → "**אחוז הצלחה**"

**Where English IS unavoidable** (file paths, commands, code), put it in:
- Tables (where it's contained in its own cell)
- Code blocks (```...```)
- Inline backticks (`like-this`)

This isolates the English so RTL flow stays predictable.

**Never write:** "LIVE earned $200 more than V3" in flowing Hebrew.
**Instead write:** "הבוט החי הרוויח $200 יותר מהניסיוני" — pure Hebrew except the dollar amount.

## ⚠️ CRITICAL #2 — Markdown tables and dense text break RTL (added 2026-05-04)

User reported AGAIN that even tables get garbled. PowerShell on Windows doesn't render markdown tables in RTL — borders and columns get scrambled, words mix between cells.

**The rule for chat output:**
- **NO markdown tables in Hebrew responses.** Use plain bullets instead.
- **One concept per line.** Keep lines short.
- **Numbers are FINE inline with Hebrew** — Hebrew uses Arabic numerals (0-9) same as English, no RTL conflict. "סף 60 ומחיר 0.65" reads correctly.
- The actual RTL problem is ONLY with **English WORDS** mixed into Hebrew sentences (BOT120, LIVE, V3 etc) — those flip word order on screen.
- For commands and code, keep using ``` ``` blocks (those render fine).
- For lists, use `-` or `*` bullets — they wrap one item per line.
- For multi-row comparison data, use simple line-per-item format, not tables:
  ```
  BTC: חציון 0.043%
  ETH: חציון 0.051%
  ```

The user has to spend mental effort decoding garbled RTL. Save them that effort.

## ⚠️ CRITICAL #3 — Explanations: short sentences, minimal English (added 2026-05-09)

User explicitly asked for short explanations with very little English. Long paragraphs and English jargon make explanations harder to follow.

**The rule for explanations:**
- **One idea per line.** Use line breaks generously.
- **Each line is a complete thought** — preferably one short sentence.
- **No commas chaining multiple clauses** — split into separate lines instead.
- **Minimize English completely** — even transliterated terms should be used sparingly.
- **No parentheses** — they break RTL flow. Use commas, em-dashes, or new lines.

**Bad example:**
"פרדי מרוויח יותר כי הוא מנצח ב-64 אחוז (בעוד פולי רק 28%), והפער בניצחונות גדול בהרבה מהפער ב-investment."

**Good example:**
"פרדי מנצח ב-64 אחוז.
פולי מנצח רק ב-28 אחוז.
הפער בניצחונות גדול הרבה יותר מהפער בהשקעה."

The user has limited time and reads explanations quickly. Visual scanning beats dense paragraphs.
