---
name: Predict.fun — פלטפורמה רביעית פוטנציאלית 06/05/2026 לילה
description: 4th BTC binary platform discovered overnight 06/05. BNB Chain, Pyth oracle, 15min BTC up/down, public WebSocket verified working from Israel. Could become 4th arb leg.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
נמצא בלילה 06/05/2026 על ידי סוכן חיפוש מקיף. דורג ראשון מתוך 12 מועמדים.

## למה Predict.fun

- **שוקי BTC up/down של 15 דקות פעילים 24/7** — מתאים לאסטרטגיה הקיימת
- **אורקל Pyth Network** — שונה מהשלושה הקיימים (Chainlink/Kalshi-פנימי/Kaiko)
  - Pyth מעדכן כל 400ms — מהיר ב-1-2 שניות מ-Chainlink
  - הסטרייק שנקבע ב-Predict.fun עשוי "לחזות" את הסטרייק של פולי ב-Polymarket
- **WebSocket ציבורי** עובד בלי אימות — `wss://ws.predict.fun/ws`
  - אומת בפועל מהמחשב הביתי בישראל
  - הירשמתי לספר פקודות, קיבלתי success+heartbeats
- **REST דורש מפתח API** (חינם, מבקשים דרך Discord, יום-יומיים המתנה)
- עמלה 2% (`feeRateBps:200`)
- שילוב עם Binance Wallet רשמי
- SDK בPython (`predict-sdk`) ו-TypeScript (`@predictdotfun/sdk`)

## איפה זה משלים את הקיים

3 פלטפורמות קיימות עם 3 אורקלים שונים, 4 = הוספת Pyth.

ב-V4 ראינו שכש-Gemini היא הקיצונית (Kaiko איטי) → הפסדים, וכש-Poly הקיצונית (Chainlink תופס מוקדם) → רווחים.
Pyth הוא **המהיר ביותר מכל הארבעה**. צפוי לבד ל"חזות" אילו פלטפורמות יהיו הקיצוניות בעתיד.

הזדמנות: אם Predict.fun מציגה הסטרייקים שלה זמן-אמת לפי Pyth, אנחנו יכולים לראות מה Polymarket עומדת לקבוע 1-2 שניות לפני שזה נקבע.

## ארביטראז' פוטנציאלי חדש

עם 4 פלטפורמות:
- 6 צירופים של זוגות → 12 כיוונים → אם רק חיוביים = 6 הזדמנויות לכל רגע
- צל לכיוונים השליליים = עוד 6
- Pyth-נמוכה (ההפך מ-Gemini-נמוכה) — צפוי להיות הזדמנות זהב

## מועמדים שנדחו

- **Opinion.trade** — מקרו בלבד, אין BTC קצר
- **Myriad** (BNB) — BTC רק יומי, ה-5min רק BNB
- **SnapMarkets** Blockchain.com — אסור EU retail
- **MEXC Predictions** — בטא, אין API
- **Coinbase Predict** — רק re-sell של Kalshi (אותם נתונים)
- **Hyperliquid HIP-4** — עדיין יומי בלבד
- **Bybit/Bitget/Bitfinex** — אין BTC up/down בינארי
- **PolyRouter/Dome/Oddpool** — אגרגטורים. Dome נרכשה על ידי Polymarket.

## דוח מלא נשמר

`C:\tmp\4th_platform_research.md` (16KB, 12 מועמדים)

## צעדים מיידיים אפשריים

1. **התחיל לאסוף נתונים מ-WebSocket עכשיו** — לא ממתין למפתח API
2. **פתח חשבון Discord ובקש מפתח API** — בשביל REST + מסחר
3. **בדוק Israel/Germany access למסחר** (קריאה כבר עובדת)

## מה לעשות בבוקר 07/05

לפי בקשת המשתמש: דוח מלא על V2+V4. לאחר מכן להציג את הגילוי של Predict.fun ולשאול האם להוסיף recorder.
