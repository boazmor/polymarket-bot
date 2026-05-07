---
name: Limitless Exchange — פלטפורמה חמישית מצוינת 07/05/2026 לילה
description: 5th BTC binary platform discovered overnight 07/05. Base + USDC, 5min/15min/1h BTC up/down all live, public API, public WebSocket, no KYC, MetaMask only, Israel not blocked. Same model as Predict.fun.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
נמצא בלילה 07/05/2026 על ידי סוכן חיפוש. דורג ראשון מתוך 7+ מועמדים שנבדקו.

## למה Limitless Exchange

- **5min, 15min ושעה של BTC up/down — כל השלושה חיים** (אומת חי מה-API)
- **ללא KYC** — חיבור MetaMask בלבד
- **ישראל לא חסומה** במפורש (ארה"ב, סין, רוסיה, איראן חסומים)
- אורקל: Chainlink Data Stream + Pyth (שני אורקלים מהירים)
- **מימון USDC על Base** — להעביר ישירות מביננס/קוינבייס/Predict
- API ציבורי עובד ללא מפתח: `https://api.limitless.exchange/markets/active`
- WebSocket ציבורי: `wss://ws.limitless.exchange/markets`
- **CLOB אמיתי** (orderbook, לא AMM) עם GTC/FOK
- מטבעות נוספים: ETH, SOL, XRP, DOGE, גם זהב וכסף

## למה זה משלים את הקיים

עם Limitless יהיו לנו **5 פלטפורמות** של BTC 15min, כל אחת עם אורקל שונה:
- Polymarket — Chainlink RTDS
- Kalshi — אוראקל פנימי
- Gemini — Kaiko (איטי)
- Predict.fun — Pyth
- **Limitless — Chainlink Data Stream + Pyth (היברידי)**

זה גם מאפשר אסטרטגיות חדשות:
- 5 דקות BTC עכשיו עם 3 פלטפורמות (Gemini + Predict + Limitless)
- ETH 5min וכו' אם נרצה

## פעולות מיידיות (פילוט)

1. **בנה recorder ל-Limitless** — דומה לאחרים, על Base USDC
2. **התחל לאסוף נתונים** — לפי ה-WebSocket הציבורי, ללא צורך באימות
3. **כשהמשתמש יעביר USDC ל-Base** (אותם צעדים כמו Predict.fun, רק רשת אחרת) — אפשר לסחור גם

## מועמד שני: PancakeSwap Prediction

- BNB Chain — אותו ארנק כמו Predict.fun
- 5min BTC up/down חי
- מינימום $0.30 (זול מאוד)
- עמלה 3% מהקופה
- חיסרון: parimutuel pool, לא orderbook — אין מחיר רציף לכל רגע
- חוזה: `0x48781a7d35f6137a9135Bbb984AF65fd6AB25618`

## דוח מלא

`/tmp/more_platforms_search.md`

## פעולה מומלצת לבוקר

1. הצג למשתמש את הגילוי
2. שאל אם להתחיל לבנות recorder ל-Limitless
3. אם כן — בנה ופרוס
4. כשהמשתמש מוכן — הסבר לו איך להעביר USDC ל-Base (אותם צעדים כמו Predict.fun רק עם Base במקום BNB Chain)
