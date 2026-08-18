# Final check

Final audit performed 2026-08-17.

## Live pipeline

- Fonbet odds collector runs independently.
- UEL page 1 is refreshed each history cycle and older pages are traversed in the background.
- UEL per-tour fetch is concurrency-bounded and individual tour failures do not abort the page.
- Event matching uses normalized player/tournament/time evidence and does not let cross-source team labels cap otherwise strong matches.
- Every model prediction is retained for research.
- At most one real 1X2 alert is emitted per event and strategy; the qualifying selection with the highest value is used.
- Alert uniqueness is protected by the database as well as application logic.
- Alerts with too little remaining delivery time are saved as skips instead of transient undeliverable calls.
- Telegram destination and channel posting permission are checked at bot startup.
- Telegram publisher retries continuously, isolates per-message failures, logs delivery, and immediately drains persisted pending alerts at startup.
- Only delivered alerts are settled/count as real calls.
- Settlement uses the mapping orientation frozen at prediction time and is not invalidated by later rematching.
- Settlement creation is idempotent under scheduler/manual races.
- Result messages reply to the original Telegram alert when available.

## Verification executed

- Python compileall: PASS
- `uv lock --check`: PASS
- Alembic migration graph: single head `d7b4c8e219f0`
- PostgreSQL offline migration generation through head: PASS
- CLI `python -m app --help`: PASS
- Focused matcher / UEL resilience / Telegram formatter / strategy / scheduler / settlement / value tests: 48 passed
- Broad test run excluding health collection: 119 passed; 18 DB-backed tests could not run in the audit host only because the host lacks the test-only `aiosqlite` package. `aiosqlite` is declared in the project dev dependencies and lockfile.
- Health test could not be collected in the audit host because its global Python environment lacks the runtime `redis` package. The production Docker image installs runtime dependencies from `uv.lock`.
- Docker daemon/CLI is unavailable in the audit environment, so final container startup must be performed on the deployment Mac.

## Deployment notes

This archive intentionally does not contain `.env` or runtime database/raw data. Restore your own `.env` before starting. For a clean database, remove Compose volumes before startup. Alembic expected head is `d7b4c8e219f0`.

No claim of betting profitability is made by these software checks; model performance must be established from a sufficiently large set of unique delivered and settled calls.
