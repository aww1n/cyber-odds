# Live betting fixes

This build includes the live-pipeline fixes discussed in chat.

## What changed

- UEL live alerts enabled; `min_samples` set to `12`.
- Event matching keeps the player+tournament+time score and does not penalize inconsistent cross-source team labels.
- Prediction insertion remains idempotent under concurrent scheduler/manual runs.
- New Telegram alerts are deduplicated by `strategy + bookmaker event + selection` using a database-enforced `alert_key`.
- Every odds snapshot can still create a `ModelPrediction`; repeated qualifying snapshots become `skip` with `duplicate_alert` instead of another real bet.
- Migration demotes historical duplicate alert rows, removes only their derived duplicate settlement rows, and preserves all `ModelPrediction` rows.
- Settlement no longer requires the mutable `EventMatch.status` to remain `matched`; an already-issued bet is settled using its recorded event-match link when a final result appears.
- Telegram sends a reply to the original bet after the match result is available: win/loss/return, final score, bet, odds, stake, payout and PnL.
- Telegram alert text now clearly shows the participants, bookmaker, exact side/outcome to bet, odds, model probability, fair odds, value, minimum odds and sample size.

## Upgrade

From the project directory:

```bash
docker compose up -d --build
```

The `app` service runs `alembic upgrade head` automatically before starting, so the new `signals.alert_key` migration is applied on startup.

Check containers:

```bash
docker compose ps
```

Check current live matching/prediction manually if desired:

```bash
docker compose exec worker python -m app match-events --historical-source uel_ef --live
docker compose exec worker python -m app predict
docker compose exec worker python -m app settle
```

Normally the history worker performs matching, prediction and settlement on schedule, so manual commands are only for diagnostics.

## Full audit follow-up

- Fixed live stale-odds semantics: `decision_at` is now actual processing time, while
  feature/odds cutoffs remain at the snapshot timestamp. `stale_after_seconds` therefore works.
- Settlement insert is concurrency-safe under scheduler/manual races via SAVEPOINT + unique key.
- Settings tests are isolated from a local production `.env`.
- Source distribution must not include `.env`, runtime `data/`, caches or generated `dist/`.
- Documentation now matches the intentionally enabled UEL forward-alert profile and warns that
  profitability is not established.
- Frozen `mapping_reversed_sides` on each live ModelPrediction so later rematching cannot flip settlement orientation.
- Added `signals.expires_at`; queued Telegram alerts are demoted to `skip` instead of being sent after quote freshness expires or the event has started.
- Settlement and Telegram PnL statistics now count only alerts that were actually delivered (`sent_at IS NOT NULL`).
- Production settings reject the default `change_me` database password.
- Added migration `c42f7a6e91bd` for frozen mapping orientation and alert expiry.
- Added migration `d7b4c8e219f0`: one canonical real alert per 1X2 event/strategy; all predictions are still preserved.

## Delivery/backfill hardening (current build)

- Telegram verifies the configured alert destination with `getChat` at startup; bad chat IDs or missing bot access fail loudly.
- Publisher performs one delivery pass immediately at startup, then continues on the configured interval.
- One Telegram send failure no longer blocks all later signals/settlements in the queue.
- HTML entity parse failures get a plain-text retry.
- Delivery cycles and successful alert/settlement sends are logged with IDs, so `docker compose logs telegram -f` is useful.
- Alerts that are too close to kickoff (`min_alert_lead_seconds`, default 15s) are stored as `skip` instead of becoming undeliverable alerts.
- Worker scheduler logs every recurring job start/completion and matching/prediction summaries.
- UEL page 1 stays fresh while pages 2..N are automatically walked in the background; history coverage is no longer permanently limited to page 1.
- UEL tour-data fetches use bounded concurrency (10) even when `HISTORY_MAX_TOURNAMENTS=100`.
- `.env.example` now enables UEL and uses the current 60s/100-tournament collection profile.
