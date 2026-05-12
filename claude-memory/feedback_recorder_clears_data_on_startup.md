---
name: MULTI_COIN_RECORDER מוחק נתונים בהפעלה — חובה לגבות
description: MULTI_COIN_RECORDER.py wipes its data dir on every startup. Always copy combined_per_second.csv elsewhere before any recorder restart.
type: feedback
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
המקליט `/root/research/multi_coin/MULTI_COIN_RECORDER.py` מבצע ניקוי קבצים בהפעלה — הודעה בלוג שלו: `(old files cleared at startup)`. זה מוחק את `combined_per_second.csv`, `binance_ticks.csv`, `poly_book_ticks.csv`, ושאר הקבצים בתיקייה.

**Why:** ב־08/05 איבדתי 54 שעות של נתוני BTC 15 דקות, 196,320 שורות, כשהפעלתי מחדש את `rec_btc_15m`. לא גיביתי לפני ההפעלה. הקובץ נמחק והנתונים אבדו לתמיד.

**How to apply:**
- לפני כל הפעלה מחדש של מקליט קיים — תמיד `cp combined_per_second.csv /root/data_<coin>_<window>_archive/combined_$(date +%Y%m%d_%H%M).csv`
- להפעיל מקליט תמיד עם `--data-dir` מפורש ואבסולוטי. בלי הדגל הוא כותב למיקום יחסי לפי CWD ואז הקובץ נחבא במיקום שהבוטים לא קוראים ממנו
- לפני kill של מקליט שכותב למיקום שגוי — קודם להעתיק את הקובץ למקום בטוח, אחר כך להפעיל מחדש
- במקליט עצמו לחפש את הקוד שמבצע את הניקוי ולהפוך אותו לאופציונלי באמצעות דגל `--no-clear`. עדיף שלא ידרוש זיכרון אנושי
