---
name: V5 (Polymarket + Predict.fun) launched 07/05/2026 ~03:45 Israel
description: V5 simulator deployed alongside V2 (Poly+Kalshi). User transferred funds to Predict.fun successfully (only platform where deposit was easy). Plan: when API key arrives from Discord, V5 becomes live trading bot.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---

## עדכון 07/05/2026 ערב (אחרי הריצה הראשונה)

**הריצה של היום היתה מבוזבזת.** הבוט פתח 120+ עסקאות אבל אפס נסגרו — הקובץ `/root/arb_v5_predict_trades.csv` נשאר עם רק שורת כותרת.

**הסיבה:** באג ב-`lookup_predict_winner()` ב-`arb_virtual_bot_v5.py`. הקוד דרש שגם `yes_bid` וגם `yes_ask` יהיו בקיצון בו-זמנית כדי לזהות סטלמנט. בפועל, אחרי סטלמנט ב-Predict.fun ה-orderbook קורס לחד-צדדי (הצד המנצח רק bids, הצד המפסיד רק asks), ולכן התנאי לעולם לא התקיים.

**התיקון** (קומיט `19b3c49` ב-main, 07/05/2026 ערב): מספיק `yb>=0.97` לבד כדי לזהות "YES ניצח", או `ya>0 and ya<=0.03` לבד כדי לזהות "NO ניצח". הלוגיקה הזו כבר היתה ב-`arb_v5_poly_predict_4seasons.py` (קומיט `e0ceb11`) אך לא הועתקה ל-`arb_virtual_bot_v5.py`.

**סטטוס בסוף הסשן — יום חמישי 07/05/2026 13:00 ישראל:**
- קוד מתוקן מקומית + commit + push ל-GitHub.
- **עדיין לא הועלה לשרת** — המשתמש סיים יום העבודה לפני העלאה. הבוט בשרת ממשיך לרוץ עם הקוד הישן.
- כשהמשתמש יחזור, צריך:
  1. `scp C:\polybot_repo\research\multi_coin\arb_virtual_bot_v5.py root@178.104.134.228:/root/arb_virtual_bot_v5.py` (מ-PowerShell)
  2. SSH לשרת והרצת `screen -S arb_v5 -X quit ; sleep 2 ; screen -dmS arb_v5 python3 /root/arb_virtual_bot_v5.py`
  3. אזהרה: 120 העסקאות הפתוחות בזיכרון יאבדו (חסרון מינורי כי הריצה הקודמת לא הוכיחה כלום בלאו הכי).

**שלוש בדיקות שאישרנו במהלך הריצה (תקפות):**
- `data_btc_15m_research/combined_per_second.csv`: 90.1% מהשורות עם `up_ask` תקין → הקלט פולי בריא.
- `data_predict_btc_15m/combined_per_second.csv`: 65.6% מהשורות עם `no_ask_usd_buyable >= $10` → עומק NO סביר.
- `market_outcomes.csv`: 194 שורות, כותרת תקינה (`market_slug`, `winner_side`) → צד הפולי של ה-settlement עובד.

לילה 07/05/2026 ~03:45 ישראל. המשתמש הלך לישון.

## סטטוס

V5 חדש (`arb_virtual_bot_v5.py`) רץ ברקע במקביל ל-V2:
- V2 (פולי+קלשי): רץ כ-baseline
- V5 (פולי+Predict): חדש, סימולציה
- V4 (3 פלטפורמות): רץ עם נתוני צל
- 30+ recorders רצים: 4 פלטפורמות × מטבעות שונים × טווחי זמן

## למה עברנו ל-V5

המשתמש ניסה להעביר כסף לקלשי דרך כרטיס אשראי — לא הצליח. ניסה Predict.fun — הצליח בקלות.
Predict.fun:
- נזילות גבוהה (פי 10-100 מקלשי)
- אורקל Pyth (מהיר ב-1-2 שניות מ-Chainlink)
- WebSocket ציבורי ללא אימות
- צריך מפתח API מ-Discord למסחר אמיתי (יום-יומיים המתנה)

## V5 לוגיקה

זהה ל-V2 רק עם Predict במקום Kalshi:
- Direction A: PolyUP + PredictNO
- Direction B: PolyDOWN + PredictYES
- Predict.fun כמו Gemini: ספר אחד (YES side), ה-NO נגזר מ-(1 - yes_bid)
- התחשבנות נפרדת לכל אורקל
- Symmetric SHARES (מניות זהות), $100/צד
- סף עלות ≤ 0.90, מחיר בודד ≤ 0.80

## בוקר טוב — מה לבדוק

1. כמה עסקאות פתח V5 בלילה
2. השוואה מול V2 (Poly+Kalshi באותה תקופה)
3. כמה פעמים BOTH_WIN_BONUS (סימן לארביטראז' עובד)
4. כמה פעמים BOTH_LOST_DANGER (סימן לסיכון מסוכן)

## פעולות ממתינות

- מפתח API של Predict.fun (משתמש צריך לבקש דרך הדיסקורד)
- כשהמפתח מגיע: V5 יכול להפוך ללייב — אך ורק אחרי שמסכימים על האסטרטגיה
- בוט תוסיף את Predict.fun ככלי חמישי ל-V4 (אורקל Pyth = רגל רביעית)

## פעולות שצריך לבדוק שעובדות

- Predict.fun recorder אוסף נתוני BTC 15m
- Predict.fun recorder אוסף נתוני BTC 5m (rank=2)
- Gemini ETH 15m (חדש)
- Gemini BTC 5m (חדש)
- כל הסקירים האחרים (Poly + Kalshi 7 מטבעות 15m)
