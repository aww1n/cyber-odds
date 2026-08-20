# ⚠️ АРХИВНЫЙ ОТЧЁТ — НЕ ИСПОЛЬЗОВАТЬ КАК ТЕКУЩИЙ СТАТУС

Docker сейчас запущен: `app`, `worker`, `telegram`, `postgres`, `redis` имеют
статус `healthy`. Актуальные команды находятся в `README.md` и
`docs/production-runbook.md`; текущая Alembic revision — `7a2c91d4e6b8`.

# Историческая заметка: Docker не был запущен

## Проблема

Alembic не может подключиться к БД, потому что Docker Desktop не запущен.

## Решение 1: Запустить Docker Desktop (РЕКОМЕНДУЕТСЯ)

```bash
# 1. Открыть Docker Desktop (через Spotlight или Applications)
open -a Docker

# 2. Подождать пока Docker запустится (иконка в menubar станет активной)

# 3. Запустить PostgreSQL
cd "/Users/ivankorotkov/Desktop/preview 11.42.43"
docker compose up -d postgres

# 4. Подождать 5-10 секунд пока БД инициализируется

# 5. Применить миграции
alembic upgrade head

# 6. Запустить всё остальное
docker compose up -d
```

## Решение 2: Использовать локальный PostgreSQL (БЕЗ DOCKER)

Если не хочешь запускать Docker, можешь использовать локальный PostgreSQL:

```bash
# 1. Установить PostgreSQL (если нет)
brew install postgresql@17

# 2. Запустить PostgreSQL
brew services start postgresql@17

# 3. Создать БД
createdb cyber_odds

# 4. Изменить .env (убрать '@postgres', использовать localhost)
# Было: postgresql+asyncpg://cyber_odds@postgres:5432/cyber_odds
# Стало: postgresql+asyncpg://cyber_odds@localhost:5432/cyber_odds

# 5. Применить миграции
alembic upgrade head
```

## Быстрая проверка что Docker работает

```bash
# Проверить статус Docker
docker ps

# Если ошибка "Cannot connect to Docker daemon" - Docker не запущен
```

## После запуска Docker

```bash
cd "/Users/ivankorotkov/Desktop/preview 11.42.43"

# 1. Запустить PostgreSQL
docker compose up -d postgres

# 2. Проверить что запустился
docker compose ps

# 3. Посмотреть логи
docker compose logs postgres

# 4. Применить миграции
alembic upgrade head

# 5. Проверить что применились
alembic current
# Ожидается: 1c5f8a9d3b2e (head)

# 6. Запустить всё остальное
docker compose up -d

# 7. Проверить логи
docker compose logs -f
```

## Что делать дальше?

Выбери один из вариантов:

**Вариант A: Запустить Docker Desktop** (проще)
1. Открой Docker Desktop
2. Дождись запуска
3. Выполни команды из "Решение 1"

**Вариант B: Использовать локальный PostgreSQL** (без Docker)
1. Установи PostgreSQL через brew
2. Измени .env
3. Выполни команды из "Решение 2"

---

**Скажи какой вариант выбираешь, я помогу дальше!**
