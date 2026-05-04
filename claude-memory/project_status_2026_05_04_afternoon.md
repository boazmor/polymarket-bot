---
name: End of 2026-05-04 office afternoon — BRM stable + office SSH key + analysis blocked on Helsinki
description: סיכום סשן 04/05 בצהריים מהמשרד. BRM יציב בלי Playwright, מסך חדש, מפתח SSH למשרד עצמאי, ניתוח BOT120 חסום בגלל גישה להלסינקי
type: project
---

## מה נעשה היום (04/05 צהריים, מהמשרד)

### 1. BRM (`LIVE_MULTI_COIN_V1.py`) — תיקון Playwright + מסך חדש
- הסשן הקודם ראה ש-BRM קורס מ-`Playwright EPIPE` (שרת עמוס). אותו דפוס שהפיל את V3 פעמיים השבוע.
- **תיקון:** השבתה מלאה של Playwright. `kickoff_render_target()` עכשיו פשוט `return`.
- **תחליף:** `extract_target_from_next_data()` — קוראת JSON מ-`__NEXT_DATA__` של עמוד פולימרקט. הייתה כתובה אבל לא נקראה. עכשיו בשרשרת ה-fallback של `ensure_target_price()` (אחרי eventMetadata/line/strike, לפני question/page_html).
- **Stage 2 לעתיד:** להוסיף מנוי Chainlink WS (כמו הרקורדר) ל-target קנוני. כרגע next_data מספיק. מצוין שיש לנו דאטה איטי (1.2s) אבל נכון.
- **מסך חדש (`bot_engine/master.py:_render_combined_status`):** 6 שורות לכל מטבע במקום שורת תצוגה צרה. כולל BINANCE/TARGET/DIST/UP+DOWN/BOT40+BOT120/TOTAL. גם ניקוי scrollback (`\\033[3J`) שמונע כפילויות בעת רגיש/גלילה.
- **Commits:** `70ac433` (Playwright disable + next_data), `8259d4f` (detailed view), `c31c779` (compact 6-line layout), `51523f5` (full screen clear), `dc6fa58` (scrollback clear).

### 2. V3 הוסר מגרמניה
- הסכמה עם המשתמש ש-V3 לא נדרש יותר. BRM מחליף אותו.
- `screen -S bot -X quit` — V3 מוקטל.
- BRM רץ ב-screen `brm` בגרמניה (PID 1139553 → ייתכן וחודש).

### 3. ממצאים מ-04/05 (יום חמישי, יום חול לאחר סופ"ש קשה)
- BTC הניב יפה ביום חמישי (V3 לפני שכבה — צבר ~$8,689 בכ-30 שעות).
- מאשר את ההנחה שסופ"ש (שבת+ראשון) הוא בעייתי ל-BOT40. דרוש עוד דאטה לקבע (3-4 סופ"שים, יש לנו אחד).
- ראינו שיום חמישי 04/05 גם הניב יפה.

### 4. SSH key למשרד — עצמאי על גרמניה
- במשרד נוצר מפתח חדש: `C:\polybot\keys\office_key` (פרטי) + `office_key.pub` (ציבורי).
- ה-public key נוסף ל-Germany authorized_keys. **המשרד עכשיו עושה `ssh germany` בלי סיסמה.**
- SSH config נכתב ב-`C:\Users\בעז\.ssh\config` עם alias `germany`.

### 5. Helsinki — חסום מהמשרד
- הלסינקי מוגדר עם key-only auth, סיסמה לא עוזרת.
- private key נמצא רק במחשב הבית (`C:\Users\user\.ssh\id_ed25519_hetzner`). גרמניה לא מתפקדת כ-jumphost (אין לה private key חוצה).
- **כן, צריך לסדר:** הערב מהבית, להוסיף את ה-public key של המשרד ל-Helsinki authorized_keys.

## ה-public key של המשרד (להוסיף להלסינקי)

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPd32S2ZlxzomhVggp4tWy3l2SKZ4Gjn4Fcpy6XQ1F2S office
```

(הקבוץ הזה, על שורה אחת בלבד, ציבורי-לחלוטין-בטוח.)

## תכנית להמשך (לסשן הביתי, הערב 04/05)

### צעד 1: Helsinki — להוסיף את מפתח המשרד
מהמחשב הביתי:
```
ssh helsinki "echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPd32S2ZlxzomhVggp4tWy3l2SKZ4Gjn4Fcpy6XQ1F2S office' >> ~/.ssh/authorized_keys"
```

(לאחר זה — המשרד עצמאי גם על הלסינקי. למחרת תוכלו לבדוק במשרד עם `ssh -i C:\polybot\keys\office_key root@62.238.26.145 "hostname"`.)

### צעד 2: ניתוח BOT120 על דאטה מהלסינקי
- הניתוח של היום על גרמניה החזיר אפסים — רק 199 שורות מ-75K יש להן ערכי ask. הרקורדר של 5m בגרמניה הופסק ב-02/05 ערב (כאשר הועברו ל-Helsinki). הדאטה האקטואלי הוא בהלסינקי.
- ב-Helsinki יש 7 רקורדרים של 5m פעילים: BTC, ETH, SOL, XRP, DOGE, BNB, HYPE.
- הערב, אחרי שהמפתח של המשרד עובד גם על הלסינקי, להוריד את `combined_per_second.csv` ו-`market_outcomes.csv` של BTC מהלסינקי. נתחיל מ-BTC לבדו.
- ניתוח: שילובים של (דיסטנס × מחיר מקסימום) — איפה הסחיר נכנס ויותר עסקאות + יותר $.
- BOT120 כיום ב-`bot_config.py`: `bot120_min_distance=60.0, bot120_limit_price=0.50`. המשתמש חושד שאלו מצמצמים יותר מדי.
- שילובים שנבדוק: דיסטנס 30/40/50/60/70/80; מחיר 0.40/0.50/0.60/0.70/0.80/0.95.

### צעד 3: לטפל ב-visualization של BRM
- המשתמש דיווח על "כפילות" במסך כשמשתנה גודל החלון. תיקון `\\033[3J` נדחף, אבל לפי הציפייה שתופענה כפילויות נוספות, נחזור.

## מצב BRM ב-end-of-session

- רץ ב-Germany בתוך screen `brm` (זרק את V3, התקדמותם נצברת מ-04/05 בוקר).
- מצב dry-run, BTC בלבד, max_buy_usd=$1.
- צפויה צבירת דאטה מ-trade_outcomes.csv ב-`/root/data_live_btc_5m_multicoin/` (שם data_dir שלו).

## תזכורת קבצים

- `live/btc_5m/bot_engine/market_manager.py`: שינויים ל-Playwright + next_data
- `live/btc_5m/bot_engine/master.py`: layout חדש פר-מטבע
- `live/btc_5m/bot_engine/screen.py`: aggressive screen clear
