---
name: ללא סוגריים בטקסט עברי
description: User explicitly said parentheses distort Hebrew sentences in his terminal. Avoid them entirely in Hebrew prose. Use commas, dashes, or separate sentences instead.
type: feedback
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User said exactly: "תרשום לך בזכרון לא לעשות סוגריים. זה מעוות את המשפטים"

**Why:** in his RTL terminal display, parentheses render in confusing positions — they jump to wrong sides of words and break the sentence flow.

**How to apply:**
- NEVER use ( ) in Hebrew prose
- Replace with: commas, em-dashes, colons, or separate sentences
- For inline clarifications, use: " — comma — " or just split into two sentences
- For lists, use bullet lines instead of inline parenthetical lists
- Brackets [] also likely problematic, avoid in Hebrew text
- Code, file paths, function names — these stay in English so brackets there are fine

Examples of replacement:

WRONG: "V5 הפסיד 1,069 דולר על 192 עסקאות (תשואה מינוס 22 אחוז)"
RIGHT: "V5 הפסיד 1,069 דולר על 192 עסקאות. תשואה מינוס 22 אחוז."

WRONG: "בודק 4 פלטפורמות (פולי, קלשי, ג'מיני, Predict)"
RIGHT: "בודק 4 פלטפורמות — פולי, קלשי, ג'מיני, Predict"
