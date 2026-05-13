---
name: Infrastructure upgrade trigger — raise AWS PG migration when bot is profitable
description: User pre-approved infrastructure cost upgrade FROM Hetzner Cloud TO AWS Placement Group (Level 3, ~$100-300/month). Trigger: bot proven profitable. Surface this option when cumulative PnL crosses meaningful positive threshold.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
13/05/2026: After explaining the four latency tiers, user said he'll fund a Level-3 upgrade once the bot proves profitable. Quote: "תרשום לך ובוא נדבר על זה כשהבוט יתחיל להרויח ויהיה שווה להשקיע בו. אני אתמוך בתשלום שיביא שיפור ושנעלה לרמה 3".

**Trigger to surface the upgrade conversation:**
- Cumulative live-bot PnL crosses **+$50** sustained over **7 days** (a meaningful signal, not noise)
- OR daily realized win > $5/day average across 7 days
- OR user explicitly asks "is it time to upgrade infrastructure"

**What "Level 3" means:**
- AWS EC2 c6i.large or similar, us-east-1 region, inside a Placement Group co-located with Polymarket/Limitless backend infrastructure
- Estimated monthly cost: **$100-300** depending on networking and dedicated tenancy
- Estimated latency improvement: 80-130 ms shaved off Polymarket+Limitless round trip
- Setup effort: 4-8 hours migration (provision, security groups, copy files, switch DNS/IPs)

**Why we skip Level 2 (regular AWS us-east-1 ~$30-80/month):**
Marginal vs Hetzner Cloud Ashburn — same physical region, only ~30-50 ms saving. Not worth the migration friction unless we're going all the way to PG.

**Why we skip Level 4 (Equinix colo ~$40k/year):**
Capital scale insufficient (~$300 wallet). Only makes sense at $100k+ working capital and 50+ trades/day. Revisit if-and-when the bot reaches those numbers.

**Before upgrading, prerequisites:**
1. Phase 2 (WebSocket direct + asyncio) completed — gets 150-300 ms speed gain WITHOUT moving servers. Highest ROI first.
2. Phase 3 monitoring (Telegram alerts, edge-decay tracking) — so we can verify the upgrade actually helped, not just feels faster.
3. Stable profitability for 7+ days at the trigger threshold above.

The right sequence: Phase 2 first → measure → Phase 3 → measure → if still profitable → Level 3 AWS PG.
