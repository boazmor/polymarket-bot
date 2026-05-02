---
name: Don't ask yes/no permission questions — the answer is always yes
description: User explicitly said to stop asking confirmation/permission questions; default to acting
type: feedback
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
User said on 2026-05-01: "תפסיק לשאול אותי עס או נו. זה תמיד יס" — stop asking me yes/no, it's always yes.

Re-emphasized 2026-05-02: "תרשום בפניך להוריד את השאלות אלי שאני צריך ללחוץ 1 או 2 ל YES" — also kill the "press 1 or 2 to choose" style prompts. Same rule: just act. Don't put numbered choice menus at the end of replies.

**The rule:** When the next step is obvious from context (next analysis, next bot tweak, next deployment, next file edit), just do it. Don't end messages with "do you want me to do X?" or "should I proceed?"

**How to apply:**
- After delivering a result, **just continue with the next logical action** — don't pause for confirmation.
- If there's a real choice between meaningfully different paths (e.g., revert vs forward, expensive irreversible action), then asking is OK. But that's the exception, not the default.
- Wrap-up sentences should describe what you're doing next ("ממשיך ל..."), not ask permission ("רוצה ש...?").

**Why:** This user trusts my judgment, has limited time, and finds the back-and-forth of confirmation requests friction.  All explicit "save and push" / "stop and continue tomorrow" decisions he's made today have been about high-stakes points like deploying live trading or stopping for the night — not about whether to run the next analysis.

**Edge cases where asking IS appropriate:**
- Switching to live trading with real money (always confirm).
- Deleting/overwriting work without backup.
- Making changes to the strategy parameters (per the "no silent tactic changes" rule).
- Anything that affects the user's wallet, server bills, or data integrity beyond local files.

For everything else: act, then report what you did.
