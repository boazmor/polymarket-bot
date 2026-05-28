---
name: V3.2 strategy findings 28/05/2026
description: New consensus rule discovered — 3 of 4 fast platforms agree, target gap ≤200, buy cheapest across all 5. Poly typically cheapest due to Chainlink lag, and Poly's final outcome ALWAYS matches fast platforms' consensus when they reach consensus. 100% Poly-to-fast outcome agreement in tested windows. V3.2 deployed in DRY mode 28/05.
type: project
originSessionId: office-session-28may
---

# הממצא הקריטי — Chainlink lag is opportunity, not risk

## המצב

V2 ראשון לרוץ. 81 עסקאות. 69% win rate. תוצאה -5.6%. הסיבה: מחירי קנייה גבוהים מדי (avg 0.731).

V3 (original) דורש: Poly+Predict consensus + third (Lim/Kal/Gem) + target close. 0 עסקאות ב-6 שעות. מחמיר מדי.

## הגילוי

Poly וכל היתר משתמשים באורקלים שונים:
- **Poly**: Chainlink — מאחר ב-1.2 שניות, מסלקת במחיר ישן
- **Predict, Limitless, Gemini**: ביננס/פית — מהיר, מסלקות במחיר נוכחי
- **Kalshi**: אורקל פנימי, לפעמים חריג

**ההפרש הקבוע בין poly_target ל-predict_target הוא בערך 126 דולר.** זה לא רעש. זה lag עקבי.

## האסטרטגיה המוצלחת לפי הנתונים

**סיגנל**: 3 מתוך 4 פלטפורמות מהירות (pred, lim, kal, gem) מסכימות:
- אסק על אותו צד ≥ 0.50
- טרגטים שלהן בתוך 200 דולר זה מזה

**ביצוע**: סורקים את כל 5 הפלטפורמות, קונים על הזולה ביותר. ברוב המקרים זאת Polymarket בגלל הפיגור באורקל.

**תוצאה במדגם (~7 שעות, 20 עסקאות)**: 
- 60% win rate
- מחיר קנייה ממוצע 0.533 (min 0.12, max 0.77)
- השקעה $40, החזר $57.32, רווח $17.32
- **ROI 43.3%**

## הבדיקה המכרעת — Poly settles like the fast platforms

**ב-6 חלונות שבהם כל ה-4 המהירות סיימו באותו צד, Poly הצטרפה 6 מתוך 6 פעמים. 100% התאמה.**

זה הוכחה ש**הפיגור של Chainlink לא משבש את ההכרעה הסופית**, רק את ההתעדכנות הרגעית. ברגע שהשוק מכריע, גם Poly מגיעה לאותה תוצאה.

הסיכון היחיד: כש-4 המהירות מתפצלות בסיום (6/12 מקרים), הסיגנל בכניסה לא בטוח. לכן יש לחזק את הסיגנל.

## V3.2 פרמטרים מומלצים

- consensus_threshold: 0.70 (במקום 0.50 — חזק יותר)
- min_fast_agree: 3 (מתוך 4)
- target_gap_max: 200 דולר
- buy_platform: זולה מבין 5
- hold: עד פקיעה
- invest_per_trade: $2 DRY mode

## מה לבדוק כשתחזור הביתה

1. **V3.2 רץ על Helsinki ב-screen `consensus_v3_2`** מ-28/05 שעות הצהריים. cwd `/root/live/consensus_v3_2/`.
2. **הקבצים שייווצרו**: 
   - `consensus_v3_2_decisions.csv` (כל החלטה)
   - `consensus_v3_2_trades.csv` (עסקאות שהופעלו)
   - `consensus_v3_2_outcomes.csv` (תוצאות סיום)
   - `consensus_v3_2.log` (stdout)
3. **אחרי 24 שעות**: לרוץ `python3 analyze_v3_2.py` שיציג: עסקאות, win rate, PnL, חלוקה לפי שעות.
4. **אם win rate ≥ 65% וב-PnL חיובי**: מעבר ל-LIVE עם invest_per_trade ≤ $5.

## הסתייגויות

- מדגם 7 שעות = 20 עסקאות. סטטיסטית רעוע.
- הסיגנל 100% Poly-fast agreement מבוסס על 6 חלונות בלבד.
- לא חישבנו עמלות מסחר בפועל לכל פלטפורמה.
- ביטקוין היה בשוק תנודתי באותה תקופה, אולי משפיע על תוצאות.

## הצעה לאחרי 48 שעות

אם נראים אותות חיוביים, להוסיף:
- כניסה רק כשמרחק מסטרייק > 100 דולר (יותר ביטחון בכיוון)
- יציאה לפני פקיעה אם השוק התהפך (פוזיציה שורט שיורה במחיר 0.10 אחרי שקנינו ב-0.50 = stop-loss)
- ניטור כל הפלטפורמות בלייב כדי להבטיח שהמחיר הזול קיים בעומק מספיק
