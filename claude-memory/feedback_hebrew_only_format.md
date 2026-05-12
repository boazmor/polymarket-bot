---
name: כתוב רק בעברית, בלי טבלאות מעורבות
description: User struggles to read mixed Hebrew/English content. Tables get misaligned in RTL terminals when headers contain English. Use Hebrew transliterations for technical terms.
type: feedback
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User explicitly said: "שים לב שאני מתקשה לקרוא את הטבלאות שלך. שורת הכותרת לא מקבילה ליתר השורות. שטרה עם אנגלית ועברית מתבדררקת. נסה לכתוב רק עברית. אפילו תכתוב מילה כמו טרגט בעברית. אני אבין"

**Why:** Markdown tables with mixed Hebrew/English headers and data render misaligned in the user's terminal. Hebrew is RTL; when English columns mix in, the rendering shifts and the user cannot match cell to header. Pure Hebrew (including transliterated technical terms) reads cleanly.

**How to apply:**
- **NO markdown tables** when content is in Hebrew. Use indented Hebrew lists instead.
- **Hebrew transliterations for technical terms** — write the English term phonetically in Hebrew letters. The user will understand:
  - target → טרגט
  - ask → אסק (or "מחיר מבוקש")
  - bid → ביד (or "מחיר מציע")
  - spread → ספרד
  - ROI → תשואה / החזר השקעה
  - PnL → רווח/הפסד
  - bot → בוט
  - arbitrage → ארביטראז'
  - platform → פלטפורמה / אתר
  - threshold → סף
  - cooldown → השהייה
  - bug → באג
  - file/log → קובץ / יומן
  - share → מניה
  - strike → סטרייק / מחיר ההכרעה
  - cost → עלות
  - profit → רווח
  - sizing → גודל עסקה
  - settlement → התחשבנות
- **Numbers/units in Hebrew context** — keep digits, but write currency as "דולר" not "$":
  - "200 דולר" not "$200"
  - "+22 אחוז" not "+22%"  (or use "תשואה")
- **Code blocks (Python, bash) stay in English** — they're code, must remain code. But explain what the code does in Hebrew above/below.
- **Variable names + file paths** stay in English (they're identifiers), but describe their PURPOSE in Hebrew.
- **Format for comparing items** — use indented list with Hebrew labels:
  ```
  בוט V2:
    מספר עסקאות: 256
    רווח כולל: 4,558 דולר
    קצב לשעה: 456 דולר

  בוט V3:
    מספר עסקאות: 13
    רווח כולל: 14 דולר
    קצב לשעה: 19 דולר
  ```
- The only English allowed in regular text: file names (`arb_v3_3way.py`), function names (`detect_spread_arb()`), and ONLY when no Hebrew transliteration exists.
- For PLATFORM NAMES — write them in Hebrew transliteration:
  - Polymarket → פולימרקט or פולי
  - Kalshi → קלשי
  - Gemini → ג'מיני
  - Predict.fun → פרדיק (per user 07/05 explicit request)
  - Pyth → פית
- For oracle/tech names: Chainlink → צ'יינלינק, Kaiko → קאיקו

**Update 10/05:** User reinforced — keep lines SHORT and minimize English words even more. English words inside Hebrew lines break the RTL flow ("מברדק את השורה"). Write each idea on its own short line. Reserve English strictly for code/filenames/variables/column-names that have no meaningful translation. Even names like "BASIC", "4SEASONS", "AMITI" can be written in Hebrew transliteration when referring to the bot in prose.
