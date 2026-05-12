---
name: מיקום קבוע של תמונת מסך
description: User saves screenshots to a fixed path. When he says "look at the image" / "תסתכל בתמונה", read this file every time.
type: reference
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
The user keeps overwriting screenshots at one fixed path:

`C:\Users\user\Desktop\BTC_5Min_Trading\תמונה.png`

Also exists `.jpg` at the same dir but the active recent file is `.png`.

**How to apply:**
- Whenever the user says "תסתכל בתמונה" / "ראה את התמונה" / "look at the image" without giving a path, read `C:\Users\user\Desktop\BTC_5Min_Trading\תמונה.png` immediately
- Don't ask which image — it's always this path
- Don't ask for re-upload — they overwrite this file with each new screenshot
- The file gets replaced with newer content over time, so re-read every time, don't rely on previous reads
