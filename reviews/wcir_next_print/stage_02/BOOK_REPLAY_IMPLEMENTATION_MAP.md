# Stage 2 Book Replay Implementation Map

| Responsibility | Implementation |
|---|---|
| epoch/baseline/delta reconstruction | `src/platform/market_data/ws_incremental_book.py` |
| gap, ordering, parity and reconnect handling | `IncrementalBookReconstructor` / `materialize_reconstructed_books` |
| fee-aware 1/5/10-share truth | `src/platform/market_data/executable_book_truth.py` |
| event checkpoint alignment | `align_book_checkpoints` |
| frozen real-data replay | `scripts/analysis/forecast_quality/research_wcir_stage02_stage03.py` |
| unit/contract tests | `tests/pmm_tests/test_weather_ws_incremental_book.py`, `tests/pmm_tests/test_executable_book_truth.py` |

The Stage 2 real-data audit freezes 2026-08-26, the last complete day before the run cutoff. It includes all 608 declared epochs, 12,429 relevant raw frames, relevant REST captures, and exact file identities. The day contains four independent reconnect chains, which are replayed separately without invented carry.

