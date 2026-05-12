---
name: להיעזר ב־AI אחרים בנושאים שאין לנו עליהם תשובות
description: When stuck on a non-trivial topic where I lack info or can't act (e.g. Discord access, niche service workflows, very recent news), draft a focused question the user can paste into another AI like ChatGPT, Gemini, or Perplexity. The user pastes the answer back.
type: feedback
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User said exactly 08/05: "אתה יכול להעזר ב AI אחרים. תנסח שאלה ושלח כתובות ואני אשאל ואחזיר תשובה. תרשום לך לעשות את זה בכל דדבא שהוא מורכב ואין לנו תשובות לגביו"

**Why:** I have limits — can't login to interactive platforms (Discord, Slack), can't see latest news beyond knowledge cutoff, can't access Cloudflare-blocked sites. Other AIs the user has access to (ChatGPT, Gemini, Perplexity) have different toolsets and live web access. The user can act as a relay.

**How to apply:**
- When I hit a wall on a complex topic where I can't find the answer myself, propose a focused question for another AI
- The question should be **self-contained** — the other AI gets no context from our session, so include all relevant background
- Be **specific** about what kind of answer is useful — e.g., "give me the exact URL" or "give me the form/process to follow"
- Keep it **short** — long questions make pasting awkward
- Phrase in **Hebrew** per user's 08/05 instruction. Keep technical terms — URLs, API names, code — as-is in English inside the Hebrew text
- If multiple URLs or facts are needed, list them as numbered points
- After the user pastes the answer, integrate it into our work without making them repeat anything

Trigger this approach for:
- API key request workflows on niche platforms
- Discord server invites and channel structure
- Recent announcements / changes on services
- Anything blocked by Cloudflare on my side
- Highly specific operational details a generalist AI may know
