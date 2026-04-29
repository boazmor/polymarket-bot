---
name: Solo developer — prefer simplicity over team ceremony
description: User is the only person working on the code; skip multi-user/team workflows and access ceremony
type: feedback
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
The user is the **only person** working on the bot codebase. No collaborators, no team. He explicitly does not want "all kinds of access codes everywhere."

**How to apply — simplify these:**
- No pull requests, no code reviews, no branch protection rules. Push directly to `main`.
- No feature branches unless there's a real reason (e.g., trying a risky experiment without breaking the working version). Default to working on `main`.
- No GitHub Actions / CI for "team" purposes (linting bots, required checks). Only set up automation if it directly helps him.
- SSH with password to the Hetzner server is fine — don't push for SSH key setup unless he asks. Same for any other "best practice" auth that adds steps.
- Don't suggest 2FA, sudo users, separate dev/prod accounts, etc., unless directly relevant.
- Commit messages can be casual / Hebrew / short. No conventional-commits format.

**Why:** He's a solo operator on a personal trading bot. Team-oriented ceremony adds friction without benefit and conflicts with his "minimize friction" preference (see feedback_minimal_friction.md).

**Where I should NOT compromise (silent guardrails):**
Even though convenience comes first, the bot will eventually trade real money. Two security basics still matter and I should keep enforcing them quietly without making a fuss:
1. Never commit `.env` or any file containing secrets to GitHub. Always include in `.gitignore`.
2. Never paste private keys, API secrets, or wallet seed phrases into a chat or print them in logs.

Beyond those two, default to simplicity.
