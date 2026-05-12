---
name: לא עושים חצי עבודה
description: User explicitly said "לא עושים חצי עבודה" — don't do half-jobs. When a feature/fix is identified as needed, complete it fully, not partially.
type: feedback
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User said exactly: "האאוטקם קריטי לעכשיו. תעשה גם אותו. נתחיל הקרטות מחדש, תמחק את הישן שוב. לא עושים חצי עבודה"

**Why:** When I proposed two fixes — (1) lookup_X_winner update in bots, (2) adding market_outcomes.csv to all recorders — and asked which one, the user chose only #1. Then upon seeing the bots needed both, he said do both. The instruction is broader: don't propose minimal fixes, identify the COMPLETE work needed and just do it.

**How to apply:**
- When identifying a fix, if there are 2-3 related sub-fixes that together make the feature actually useful, do them all together. Don't artificially split.
- Don't ask "shall I do A or A+B" — just propose the right complete scope.
- If a recorder gap is identified that affects analysis, fix the recorder AND the consumer in one batch.
- "Half work" includes: updating bots to use a missing data source, but not adding the missing data; fixing a function but leaving the caller broken; deploying code without restart; etc.
