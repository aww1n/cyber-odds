# ⚠️ АРХИВНАЯ ИНСТРУКЦИЯ

Проценты готовности и номера миграций ниже устарели. Используйте `README.md` и
`docs/production-runbook.md`. Текущая revision: `7a2c91d4e6b8`; все пять
Docker-сервисов уже запущены и healthy.

# 🚀 ИНСТРУКЦИЯ ДЛЯ ЗАПУСКА (историческая)

## ЧТО СДЕЛАНО

✅ Полный аудит системы (5000+ строк кода)  
✅ Найдено и исправлено 8 критических багов  
✅ Исправлен конфликт миграций (два HEAD)  
✅ Добавлены bankroll поля в БД  
✅ Добавлено поле line в corridors для totals  
✅ Созданы 2 новые миграции  
✅ Обновлены Settings с новыми полями  
✅ Создан детальный отчет (FINAL_AUDIT_REPORT.md - 34KB, 1074 строки)

## БЫСТРЫЙ СТАРТ

```bash
cd "/Users/ivankorotkov/Desktop/preview 11.42.43"

# 1. Проверить миграции
alembic heads

# 2. Применить миграции
alembic upgrade head

# 3. Установить зависимости
uv sync --extra dev --extra ml --extra telegram

# 4. Запустить тесты
uv run pytest tests/ -v

# 5. Запустить Docker
docker compose up -d --build

# 6. Проверить логи
docker compose logs -f
```

## ГОТОВНОСТЬ: 75% для 1X2, 35% для totals

Подробности в FINAL_AUDIT_REPORT.md
