---
name: זמני תגובה ומנגנון פקודות Polymarket Predict.fun
description: Reference data on CLOB API latency, WebSocket events, rate limits, and partial-fill handling for Polymarket and Predict.fun. Sourced from Gemini consultation 09/05/2026.
type: reference
originSessionId: 6fd8bae4-ccf0-4507-96ea-c5a3c0c8b1d1
---
מקור: שאלה שהמשתמש הריץ ב-Gemini ב-09/05/2026 לפנות בוקר במהלך תכנון מנגנון השלמה לבוטי הארביטראז'.

## נתוני Latency

- אישור HTTP על שליחת פקודה: 200ms תחת לוד נורמלי, מ-AWS US-East-1 לפולימרקט
- אישור Fill דרך WebSocket: 100-300ms נוספים אחרי שליחת הפקודה
- מקרה גרוע, שוק תנודתי: 1.5-3 שניות סך הכל
- צוואר הבקבוק הוא ה-API Gateway של הפלטפורמה לאימות EIP-712, לא הבלוקצ'יין

## חובה WebSocket, לא REST Polling

- Polymarket: ערוץ orders ב-WebSocket, אירוע ORDER_FILLED
- Predict.fun: דומה במבנה
- Polling REST מוסיף 300-500ms עיכוב + שורף Rate Limit מהר

## Rate Limits

- Polymarket: 10-20 פקודות לשנייה, חריגה = 429 Too Many Requests
- Predict.fun: לא מתועד רשמית, מומלץ עד 5 לשנייה ללא אישור צוות

## Market Order vs Limit Order

- אין באמת Market Order ב-CLOB
- Market = פקודת Limit במחיר אגרסיבי 0.99 כשהשוק ב-0.50
- Latency זהה לחלוטין בין השניים
- ההבדל היחיד: Market תמיד Taker ותתמלא מיד או תיכשל. Limit עדין יושב בספר כ-Maker

## טיימאאוט מומלץ למנגנון Fallback

- 800ms-1000ms מקסימום המתנה לאישור Fill
- מתחת ל-500ms: סיכון לבטל פקודה שכבר התמלאה
- חובה לחכות לאישור Cancel לפני שמבצעים פעולה אחרת על אותה פוזיציה — אחרת חשיפה כפולה

## פסאודו-קוד מומלץ

```python
async def place_order_with_timeout(side, price, size):
    order = await clob_client.create_order(side, price, size)
    order_id = order['orderID']
    try:
        fill_event = await asyncio.wait_for(
            ws_manager.get_fill_event(order_id),
            timeout=0.8
        )
        return "SUCCESS", fill_event
    except asyncio.TimeoutError:
        # חובה: בקש ביטול וחכה לאישור לפני fallback
        cancel = await clob_client.cancel_order(order_id)
        await ws_manager.wait_for_cancel(order_id, timeout=0.8)
        return "FAILED_TIMEOUT", None
```

## מה זה אומר לסימולציה

- כדי לסמלץ אמין: לדמות WebSocket fill events עם תזמון 200-500ms ראנדומלי
- לדמות race conditions בין cancel ל-fill
- בלי זה, סימולציה לא משקפת מציאות של partial fills
