# Production runbook

## Безопасные defaults

- Реальные ставки отсутствуют: система только собирает данные, рассчитывает и публикует
  разрешённые сигналы.
- Автоматического размещения ставок нет. В текущем профиле `uel_football` Telegram-
  алерты включены для forward-наблюдения; это экспериментальный режим без доказанного ROI.
- Неизвестные factor ID Fonbet не участвуют в прогнозах.
- Live-счёт Fonbet не считается финальным результатом без terminal status.
- Telegram требует токен, admin whitelist и отдельный/первый admin chat ID. Для канала
  задайте `TELEGRAM_ALERT_CHAT_ID=-100...` и добавьте бота администратором с правом публикации;
  это проверяется через Bot API при старте.
- Неотправленный Telegram alert имеет persisted expiry: после устаревания исходного odds
  snapshot или старта события он переводится в `skip`, а не доставляется задним числом.
- Settlement/ROI учитывают только calls, которые реально получили `sent_at` после успешной
  доставки в Telegram.
- RAW JSON и PostgreSQL/Redis используют persistent volumes.
- Полный Fonbet base snapshot имеет большой размер. Без подтверждённого targeted feed
  безопасный начальный интервал — 60 секунд. При snapshot порядка 7–8 MB интервал 15 секунд
  способен дать десятки GB RAW-данных в сутки; нужен мониторинг диска/retention/compression.

## Запуск

Перед обновлением уже наполненной production-БД сделайте `pg_dump`: миграция дедупликации
помечает исторические повторные алерты как `skip` и удаляет производные duplicate settlement,
при этом `ModelPrediction`, odds и source results сохраняются.

1. Скопировать `.env.example` в `.env` и заменить пароль PostgreSQL, Telegram token и
   admin IDs. `FONBET_BASE_URL` остаётся конфигурацией, а не скрытым default в коде.
2. Запустить Docker Engine.
3. Проверить конфигурацию: `docker compose config --quiet`.
4. Запустить: `docker compose up --build -d --force-recreate`. После любого изменения `.env`
   также используйте `--force-recreate`: `docker compose restart` не перечитывает environment.
5. Проверить: `docker compose ps` и Telegram `/status`, `/parsers`.

Сервисы:

- `app`: Alembic migration, затем append-only Fonbet odds collector;
- `worker`: UEL/ESB/SIS history jobs, normalization, matching, prediction decisions и
  settlement по подтверждённым source results; исключение одного job логируется и не
  останавливает остальные. Для UEL page 1 обновляется каждый цикл, а pages 2..N обходятся
  фоном; один неудачный tour-data request не отменяет сохранение остальных туров;
- `telegram`: admin commands, доставка `alert` и ответы settlement;
- `postgres`: основная база;
- `redis`: health/runtime dependency и база для дальнейшей очереди/coordination.

У всех сервисов есть restart policy и healthcheck. В текущем окружении Compose schema
проверена, но образ не был собран: локальный Docker socket не запущен. Это нужно повторить
после запуска Docker Desktop.

## Ручные one-shot команды

```bash
uv run alembic upgrade head
uv run python -m app collect
uv run python -m app normalize --source fonbet
uv run python -m app match-events --historical-source uel_ef
uv run python -m app predict
uv run python -m app settle
uv run python -m app health
```

## Режим алертов

В текущем рабочем профиле алерты уже включены по запросу оператора. Для заявления о
прибыльности или увеличения ставок всё равно должны быть выполнены условия:

1. накоплена историческая линия для выбранного рынка;
2. settlement проверен на этих рынках;
3. temporal walk-forward показал приемлемые LogLoss/Brier/calibration;
4. ROI и drawdown посчитаны на непересекающемся будущем интервале;
5. результат не является следствием подбора порога на test.
6. для выбранного source подтверждён финальный result contract, а не только live score.

После изменения конфигурации сначала запустить `predict` вручную и проверить `/signals`:
каждая запись должна ссылаться на конкретные `odds_snapshot_id` и `event_match_id` и
иметь ожидаемые `filter_reasons`/`anomaly_flags`.
