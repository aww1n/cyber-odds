# ✅ DEPLOYMENT CHECKLIST

Актуальная единственная Alembic head: `7a2c91d4e6b8`. Перед новой миграцией
используйте custom-format `pg_dump -Fc` и проверяйте архив через
`pg_restore --list`.

## ПЕРЕД ПРИМЕНЕНИЕМ МИГРАЦИЙ

- [ ] Создать бэкап БД:
  ```bash
  docker compose exec postgres pg_dump -U cyber_odds cyber_odds > backup_$(date +%Y%m%d_%H%M%S).sql
  ```

- [ ] Проверить текущее состояние миграций:
  ```bash
  alembic current
  ```

- [ ] Проверить что есть только один HEAD:
  ```bash
  alembic heads
  # Должен показать: 7a2c91d4e6b8 (head)
  ```

## ПРИМЕНЕНИЕ МИГРАЦИЙ

- [ ] Применить миграции:
  ```bash
  alembic upgrade head
  ```

- [ ] Проверить что применились:
  ```bash
  alembic current
  # Должен показать: 7a2c91d4e6b8 (head)
  ```

- [ ] Проверить логи миграций:
  ```bash
  alembic history --verbose
  ```

## ПРОВЕРКА БД

- [ ] Проверить что поля добавились в signals:
  ```sql
  SELECT column_name, data_type 
  FROM information_schema.columns 
  WHERE table_name = 'signals' 
  AND column_name IN ('bankroll_at_signal', 'stake_percent', 'stake_amount');
  ```

- [ ] Проверить что поле line добавилось в odds_corridors:
  ```sql
  SELECT column_name, data_type 
  FROM information_schema.columns 
  WHERE table_name = 'odds_corridors' 
  AND column_name = 'line';
  ```

- [ ] Проверить что constraints обновились:
  ```sql
  SELECT constraint_name 
  FROM information_schema.table_constraints 
  WHERE table_name = 'odds_corridors' 
  AND constraint_type = 'UNIQUE';
  ```

## ТЕСТИРОВАНИЕ

- [ ] Установить зависимости:
  ```bash
  uv sync --extra dev --extra ml --extra telegram
  ```

- [ ] Запустить тесты:
  ```bash
  uv run pytest tests/ -v
  ```

- [ ] Проверить линтеры:
  ```bash
  uv run ruff check .
  ```

- [ ] Проверить типы:
  ```bash
  uv run mypy
  ```

## ЗАПУСК DOCKER

- [ ] Остановить старые контейнеры:
  ```bash
  docker compose down
  ```

- [ ] Пересобрать образы:
  ```bash
  docker compose build
  ```

- [ ] Запустить контейнеры:
  ```bash
  docker compose up -d
  ```

- [ ] Проверить что все контейнеры запустились:
  ```bash
  docker compose ps
  ```

## ПРОВЕРКА РАБОТЫ

- [ ] Проверить логи app:
  ```bash
  docker compose logs app | tail -50
  ```

- [ ] Проверить логи worker:
  ```bash
  docker compose logs worker | tail -50
  ```

- [ ] Проверить логи telegram:
  ```bash
  docker compose logs telegram | tail -50
  ```

- [ ] Проверить что prediction worker работает:
  ```bash
  docker compose logs worker | grep "prediction"
  ```

- [ ] Проверить что settlement worker работает:
  ```bash
  docker compose logs worker | grep "settlement"
  ```

- [ ] Проверить что Telegram bot запустился:
  ```bash
  docker compose logs telegram | grep "polling started"
  ```

## ФУНКЦИОНАЛЬНОЕ ТЕСТИРОВАНИЕ

- [ ] Отправить команду `/start` в Telegram bot
- [ ] Отправить команду `/signals` - проверить что показываются текущие сигналы
- [ ] Дождаться нового сигнала и проверить формат сообщения
- [ ] Проверить что сигнал правильно expires через 2 минуты
- [ ] Дождаться settlement и проверить что сообщение обновилось

## МОНИТОРИНГ

- [ ] Проверить метрики БД:
  ```sql
  SELECT COUNT(*) FROM signals WHERE created_at > NOW() - INTERVAL '1 day';
  SELECT COUNT(*) FROM model_predictions WHERE created_at > NOW() - INTERVAL '1 day';
  SELECT COUNT(*) FROM odds_corridors WHERE is_active = true;
  ```

- [ ] Проверить размер таблиц:
  ```sql
  SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
  FROM pg_tables
  WHERE schemaname = 'public'
  ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
  ```

## ROLLBACK ПЛАН

Если что-то пошло не так:

```bash
# 1. Остановить контейнеры
docker compose down

# 2. Откатить миграции
alembic downgrade e9c1b6d42a10

# 3. Восстановить БД из бэкапа (если нужно)
docker compose exec -T postgres psql -U cyber_odds cyber_odds < backup_YYYYMMDD_HHMMSS.sql

# 4. Запустить старую версию
docker compose up -d
```

## NOTES

- Миграции обратимы (есть downgrade)
- Все новые поля nullable, старые записи не нарушатся
- Бэкап обязателен перед применением миграций
- Если тесты упадут - не запускать в production

---

**Используй этот checklist для безопасного deployment!**
