---
name: V5 (Polymarket + Predict.fun) launched 07/05/2026 ~03:45 Israel
description: V5 simulator deployed alongside V2 (Poly+Kalshi). User transferred funds to Predict.fun successfully (only platform where deposit was easy). Plan: when API key arrives from Discord, V5 becomes live trading bot.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
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
