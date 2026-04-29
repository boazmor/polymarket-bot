---
name: Local copies of server recordings (downloaded 2026-04-29)
description: Where on the home machine the server recordings were downloaded for offline analysis
type: reference
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
On 2026-04-29 the essential recording CSVs were downloaded from the Hetzner server to:

```
C:\Users\user\Desktop\server_recordings_2026-04-29\
├── binance_poly\
│   ├── combined_per_second.csv   (62 MB, 137,804 rows, 25/04 09:18 → 26/04 23:57, 50 cols)
│   ├── market_outcomes.csv       (57 KB, 464 rows — winning side per 5-min market)
│   ├── markets.csv               (338 KB — slug, target_price, up/down tokens, question)
│   └── events.csv                (418 KB — recorder events)
└── chainlink\
    ├── per_second.csv            (8.7 MB, 38,129 rows, 24/04 03:46 → 24/04 14:22, 15 cols)
    ├── rtds_ticks.csv            (4.7 MB, 37,663 rows — every Chainlink RTDS tick with latency_ms)
    └── events.csv                (1.9 KB)
```

**The huge files were intentionally NOT downloaded:**
- `data_ws_binance_poly_research/binance_ticks.csv` (137 MB) — every Binance tick
- `data_ws_binance_poly_research/poly_book_ticks.csv` (1.4 GB) — every order-book update
- `data_ws_binance_poly_research/raw_poly_messages.jsonl` (40 GB) — raw WS messages

If a specific drill-down needs them, query the server directly via SSH/SFTP rather than downloading.

**Critical limitation:** the Binance+Poly and Chainlink recordings have **no time overlap** — Chainlink only has 24/04, Binance+Poly only has 25–26/04. So a direct "Binance BTC vs Polymarket Chainlink BTC at the same second" comparison is not possible from existing data. To do that comparison, both recorders need to be re-run simultaneously.

These files belong in `.gitignore` — they are large local data, not source code.
