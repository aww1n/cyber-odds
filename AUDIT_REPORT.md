# Полный аудит проекта Cyber Odds

Дата аудита: 2026-08-17

## Итог

Проект в целом имеет рабочую модульную архитектуру и полноценный live-конвейер для текущего профиля `UEL eFootball -> Fonbet 1x2 -> baseline -> Telegram -> Result -> Settlement`. Критические ошибки, которые уже проявлялись на живой базе (недостижимый matching confidence, дубли прогнозов/алертов, settlement по mutable status), исправлены. В ходе полного аудита найдены и дополнительно исправлены ещё несколько ошибок корректности.

При этом проект **нельзя считать доказанно прибыльной betting-системой**: текущая модель `individual_h2h_fixed` является вероятностным baseline, а live-порог `min_samples: 12` пока не подтверждён достаточным out-of-sample ROI. Это исследовательская система с включёнными forward alerts, а не автоматический исполнитель ставок.

## Что проверено

- структура `src/app`, CLI и entrypoints;
- Dockerfile и `docker-compose.yml`;
- настройки и strategy config;
- PostgreSQL models и ограничения;
- линейность Alembic migrations;
- Fonbet / UEL / ESB / H2H providers и parsers;
- raw archive и хранение метаданных;
- normalization и event matching;
- feature builder и leakage cutoffs;
- baseline/backtest/value filters/staking;
- live prediction worker;
- signal generation и Telegram publisher;
- settlement worker и привязка результатов;
- Telegram admin repository/formatter;
- тесты, Python syntax, lockfile;
- наличие секретов и runtime-данных в дистрибутиве;
- приблизительный рост raw/model data.

## Исправлено в audited build

### 1. Stale odds фактически не работал

Live worker раньше передавал в backtest engine одинаковое время как `decision_at` и `odds_received_at`. Поэтому фильтр `stale_after_seconds` всегда видел возраст коэффициента `0` и никогда не мог сработать.

Исправление:

- `decision_at` = реальное текущее время обработки;
- `odds_received_at` = timestamp конкретного snapshot;
- `feature_cutoff_at` остаётся leakage-safe;
- старый коэффициент теперь получает `stale_odds`.

### 2. Settlement race

Ручной `python -m app settle` и scheduler могли одновременно попытаться создать settlement для одного `signal_id`, после чего unique violation мог откатить весь batch.

Исправление:

- insert каждого settlement изолирован через SAVEPOINT;
- unique index остаётся источником истины;
- duplicate settlement пропускается, остальные строки batch продолжают обрабатываться.

### 3. Settlement больше не зависит от mutable EventMatch.status

Уже выданный прогноз мог позже получить `EventMatch.status='rejected'` после повторного matching, из-за чего старый worker переставал его закрывать.

Исправление:

- settlement использует зафиксированную связь `ModelPrediction.event_match_id -> source_event_id -> Result`;
- текущий `EventMatch.status` не является условием закрытия.

### 4. Заморожена ориентация участников

`EventMatch.components['reversed_sides']` тоже мог измениться при повторном matching. Это создавало риск перевернуть P1/P2 при settlement уже выданного сигнала.

Исправление:

- в `model_predictions` добавлено `mapping_reversed_sides`;
- live prediction сохраняет ориентацию в момент создания;
- settlement и Telegram result используют frozen value;
- для старых строк остаётся fallback на EventMatch.components.

### 5. Дедупликация live alerts

Раньше каждый новый odds snapshot мог давать новый `Signal(alert)` по одному и тому же матчу/исходу, что приводило к сотням одинаковых сообщений и полностью искажало ROI.

Исправление:

- добавлен `signals.alert_key`;
- ключ: `strategy + bookmaker event_id + selection`;
- unique index защищает даже от конкурентных workers;
- все новые `ModelPrediction` всё равно сохраняются для аналитики;
- повторный qualifying snapshot сохраняется как `skip` с `duplicate_alert`;
- миграция оставляет один канонический historical alert и демотирует старые дубли.

При миграции канонический alert теперь выбирается с приоритетом реально отправленного (`sent_at IS NOT NULL`), а не просто самого раннего созданного.

### 6. Просроченные Telegram alerts

Если Telegram был недоступен, persisted alert мог быть отправлен позже с уже устаревшим коэффициентом или даже после начала матча.

Исправление:

- добавлено `signals.expires_at`;
- expiry для нового live alert = минимум из старта матча и `odds_received_at + stale_after_seconds`;
- publisher перед отправкой демотирует просроченные unsent alerts в `skip` с `delivery_expired`;
- освобождается `alert_key`, чтобы свежий snapshot до старта матча мог создать новый актуальный alert.

### 7. PnL считается только по реально доставленным calls

Ранее `settlement_worker` и Telegram stats могли учитывать `decision='alert'`, даже если сообщение никогда не дошло до канала.

Исправление:

- settlement требует `Signal.sent_at IS NOT NULL`;
- `/stats`, `/models`, `/bank` считают только отправленные alerts;
- несостоявшаяся доставка больше не изображается как реальная ставка пользователя.

### 8. Production password safety

В compose есть development fallback `change_me`. В production это опасно, если забыть `.env`.

Исправление:

- `APP_ENV=production` теперь отклоняет `DATABASE_URL` с default password `change_me`.

### 9. Безопасный дистрибутив

Исходный ZIP содержал production `.env` и большой runtime dataset. Audited ZIP их не содержит.

Удалены из выдаваемого архива:

- `.env`;
- `.venv`;
- runtime `data/raw/*`;
- `data/research.sqlite3`;
- caches / `__pycache__`;
- `.pytest_cache`;
- generated `dist/`.

`.env.example` сохранён.

Если старый ZIP когда-либо отправлялся третьим лицам или публиковался, секреты из старого `.env` следует ротировать.

## Проверки, которые прошли

- `python -m compileall src tests` — OK;
- `uv lock --check` — OK;
- Alembic — один линейный head `c42f7a6e91bd`;
- PostgreSQL offline generation всех migrations — OK;
- `python -m app --help` — OK;
- 112 выбранных unit/parser/provider/core tests — passed;
- расширенный `pytest --ignore=tests/test_health.py`: 118 passed; 16 DB integration tests не смогли стартовать только из-за отсутствия `aiosqlite` в среде аудита;
- full pytest collection дополнительно блокируется отсутствием `redis` в среде аудита.

`aiosqlite` и `redis` присутствуют в `pyproject.toml`/`uv.lock`; это ограничение текущего audit container, а не отсутствующая project dependency.

## Что не удалось проверить в этой среде

- реальный `docker compose build/up` — Docker CLI/daemon в audit environment недоступен;
- PostgreSQL integration migrations на живом контейнере;
- DB integration tests через `aiosqlite`;
- реальный Telegram send/response;
- реальные HTTP endpoints в момент аудита — интернет для container execution отключён.

После распаковки audited build эти проверки нужно выполнить на Mac через Docker Compose.

## Оставшиеся риски

### HIGH — рост storage

В runtime data из проверенного архива:

- raw directory занимал примерно 131 MB (весь `data` около 227 MB);
- один крупный Fonbet snapshot был порядка 7.6 MB;
- при polling каждые 15 секунд это теоретически порядка 40+ GB raw/day без retention/compression;
- SQLite research DB содержала 44,622 odds snapshots;
- `ModelPrediction.features` в выборке имел около 107 KB среднего JSON на строку (median ~94 KB, max ~149 KB).

PostgreSQL TOAST уменьшит физический объём отдельных JSON, но не делает cross-row deduplication. При большом количестве snapshots таблица model_predictions всё равно будет расти очень быстро.

Рекомендация: до долгого unattended запуска добавить retention/compression raw payloads и вынести feature vectors в deduplicated feature snapshots/reference table. Пока это не сделано, более безопасный polling — 60 секунд, если 15 секунд не требуется для конкретного эксперимента.

### HIGH — прибыльность модели не доказана

`individual_h2h_fixed` сейчас используется как baseline. Текущие forward calls не являются доказательством betting edge.

Особенно важно:

- live `min_samples=12` выбран из-за текущей глубины базы;
- ранее проект документировал более строгие sample thresholds;
- первые несколько уникальных settlements статистически ничего не доказывают;
- `safety_multiplier=1.15` также должен быть подтверждён out-of-sample betting evaluation.

Нужно собирать только уникальные реально отправленные calls и оценивать ROI/CLV по сотням независимых bets, а не по snapshots.

### MEDIUM — возможная leakage assumption по `source_updated_at`

Feature builder считает результат доступным, если до cutoff был `settled_at`, `source_updated_at` или `observed_at`. Для UEL result создаётся только для finished match, что существенно снижает риск, но корректность исторического backtest всё равно зависит от того, что UEL `updated_at` действительно отражает доступность финального результата, а не более раннее live-обновление.

Нужно периодически сверять source contract/raw payload timestamps.

### MEDIUM — Telegram delivery остаётся at-least-once

Есть небольшой crash window: Telegram может принять сообщение, а процесс упасть до записи `sent_at/message_id` в PostgreSQL. После рестарта publisher может отправить его ещё раз. Полностью exactly-once без idempotency support со стороны Telegram получить сложно.

DB-level alert dedupe защищает от повторной генерации betting decision, но не устраняет этот редкий transport-level resend.

### MEDIUM — live coverage ограничен

Текущий `PredictionSignalWorker` намеренно поддерживает только:

- `individual_h2h_fixed`;
- market `1x2`;
- активную стратегию `uel_football`;
- букмекерскую live line Fonbet + UEL history/matching.

`esb_totals`, `h2h_result`, `fon_ml_v1` сейчас disabled/research-only. То есть система пока не делает live value calls «по всем источникам и рынкам».

### MEDIUM — feature storage design

Каждый odds snapshot может создать новый ModelPrediction с полным большим `features` JSON. Это полезно для воспроизводимости, но дорого. Более зрелая схема должна иметь immutable feature snapshot с hash/id и ссылку из predictions.

### LOW/MEDIUM — standalone wheel config

Docker build явно копирует `config/strategies.yaml`, поэтому Docker deployment корректен. Но wheel включает только `src/app`; при standalone pip install default `STRATEGIES_PATH=config/strategies.yaml` требует внешнего файла в текущем рабочем каталоге. Для Docker это не проблема, для CLI package distribution надо либо package-data, либо обязательный explicit path.

### LOW — image reproducibility

Docker images закреплены по tags (`postgres:17-alpine`, `redis:7-alpine`, uv tag), но не по immutable digest. Для строгой production reproducibility лучше pin digest.

## Миграции

Новый head:

`d7b4c8e219f0`

Цепочка остаётся линейной.

Важно: migration `8d31a9f4c2be` намеренно удаляет **только derived duplicate settlement rows**, созданные старыми повторными alerts, и демотирует duplicate alerts в `skip`. Raw events/results/odds/model_predictions она не удаляет.

Перед обновлением существующей production DB обязательно сделать `pg_dump`.

## Рекомендуемый порядок обновления

```bash
# 1. Бэкап существующей PostgreSQL БД
docker compose exec -T postgres pg_dump -U cyber_odds cyber_odds > backup_before_audit.sql

# 2. Сохранить свой .env отдельно
cp .env ../cyber-odds.env.backup

# 3. Обновить исходники audited build, не заменяя .env

# 4. Сборка и автоматический alembic upgrade
docker compose up -d --build

# 5. Проверка
docker compose ps
docker compose exec worker python -m app match-events --historical-source uel_ef --live
docker compose exec worker python -m app predict
docker compose exec worker python -m app settle
```

После запуска стоит проверить:

```bash
docker compose exec postgres psql -U cyber_odds -d cyber_odds -c "SELECT version_num FROM alembic_version;"
```

Ожидаемый head: `d7b4c8e219f0`.

## Вывод

После внесённых исправлений live-контур стал существенно корректнее: один betting call не размножается по snapshots, просроченные calls не должны уходить в Telegram, settlement использует зафиксированную ориентацию и считает только реально доставленные сигналы, а статистика не должна снова превращать один матч в десятки ставок.

Следующая крупная инженерная задача — не ещё один фильтр, а **storage/retention + строгая out-of-sample betting evaluation на уникальных delivered signals**.

## Дополнительная проверка после проблемы «alerts есть, но Telegram не прислал»

После live-проверки, где БД временно содержала `decision='alert'`, `sent_at IS NULL`, а затем
эти строки уходили из alert-очереди, дополнительно усилен transport/runtime-контур:

- Telegram destination проверяется через Bot API при старте. Polling больше не может выглядеть
  «здоровым», если configured alert chat недоступен боту.
- Перед polling выполняется немедленный `publish_once`, затем publisher работает по интервалу.
- Ошибка отправки одного сообщения изолирована: она не прекращает обработку остальных pending
  alerts/settlements в том же цикле.
- Ошибка HTML entities получает plain-text retry.
- Publisher логирует размер очереди, expiry и успешные message IDs.
- Стратегия не создаёт alert если до `min(event_start, odds_expiry)` осталось меньше `min_alert_lead_seconds`
  (15 секунд по умолчанию); такой prediction остаётся в аналитике как skip
  `insufficient_delivery_window`.
- Scheduler теперь пишет в stdout запуск/завершение recurring jobs и summaries matching/predict,
  поэтому пустой `docker compose logs worker -f` больше не является нормальным состоянием.
- UEL history больше не навсегда ограничена `page=1`: первая страница обновляется каждый цикл,
  а страницы 2..N обходятся циклически в фоне.
- UEL tour-data имеет bounded concurrency=10 и partial-failure isolation: один недоступный турнир
  больше не срывает сохранение остальных турниров страницы.

Дополнительные проверки текущего дерева:

- Python compileall: OK;
- `uv lock --check`: OK;
- Alembic: один head `c42f7a6e91bd`, offline PostgreSQL SQL generation: OK;
- 116 unit/parser/provider/core tests без DB/redis-зависимых модулей: passed;
- отдельный UEL partial-failure smoke/test: passed;
- DB integration tests в audit container по-прежнему нельзя выполнить без `aiosqlite`, а
  `test_health.py` без установленного `redis`; обе зависимости присутствуют в project lock и
  устанавливаются Docker build через `uv sync`.

### One real 1X2 call per match

A further correctness issue was found during the final pass: deduplication by
`event + selection` still allowed mutually exclusive P1 and X (or P2) calls for the same match.
The live worker now evaluates all three 1X2 selections, preserves every ModelPrediction, and
issues at most one real Telegram alert per `strategy + bookmaker event`: the qualifying
selection with the highest value edge. Other simultaneously qualifying outcomes are persisted
as `skip` with `better_selection_available`. Migration `d7b4c8e219f0` rewrites the live alert
key to event-level semantics while preserving already-delivered historical calls.
