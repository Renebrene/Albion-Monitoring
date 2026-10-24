# АЛЬБИОН. Мониторинг экранов

Внутренний веб-сервис для мониторинга рекламных экранов по адресам и ведения
заданий между командами «Сервис», «Экипаж», «Руководитель» и «Директор».
Монолит на FastAPI + Jinja2 + vanilla JS, две СУБД на выбор: PostgreSQL
(целевая, с RLS) и SQLite (откат/разработка).

## Документы

| Документ | О чём |
|---|---|
| [`TECHNICAL_DESCRIPTION.md`](TECHNICAL_DESCRIPTION.md) | Техническое описание: архитектура, страницы, данные, сессии, юрлица, роли, типы заданий, workflow, медиа |
| [`POSTGRES.md`](POSTGRES.md) | PostgreSQL: схема `core`/`monitoring`, RLS, миграции Alembic, перенос из SQLite, область видимости юрлиц |
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Развёртывание: Docker Compose, HTTPS (Caddy), тоннель, бэкапы, второе юрлицо |
| [`docs/ADMIN_GUIDE.md`](docs/ADMIN_GUIDE.md) | Руководство администратора: юрлица, роли, доступы, типы заданий, переходы |
| [`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md) | Обзор проекта со скриншотами: интерфейсы, логика, справочник API и функций |
| [`docs/presentation.html`](docs/presentation.html) | Презентация проекта (самодостаточный HTML, открыть в браузере) |
| [`CHANGELOG.md`](CHANGELOG.md) | История изменений по этапам |
| [`docs/ADMIN_GUIDE.md`](docs/ADMIN_GUIDE.md) | Руководство администратора |
| [`docs/archive/AUDIT_2026-09.md`](docs/archive/AUDIT_2026-09.md) | Архив: исходный аудит проекта (сентябрь 2026) |
| FastAPI OpenAPI | `GET /docs` и `GET /openapi.json` на работающем приложении |

## Возможности

- **Доступ и аудит.** Вход по логину/паролю (bcrypt, JWT), серверные сессии с
  отзывом: выход, смена пароля и блокировка пользователя закрывают доступ
  немедленно; журнал `audit_log` (вход, отказы, настройки, задания, юрлица,
  роли).
- **Юрлица группы.** Несколько организаций в одной установке; активное юрлицо —
  часть серверной сессии (переключатель в шапке), изоляция данных через RLS в
  PostgreSQL; общие адреса (`shared = 1`) видны всем юрлицам.
- **Конструктор ролей.** Роль = набор возможностей (фиксированный каталог в
  коде) + рабочее место (страница) + область данных («всё юрлицо» / «только
  свои»). Системные роли воспроизводят прежнее поведение и защищены от правок.
- **Типы заданий.** До пяти дополнительных полей (текст, число, дата, список) с
  валидацией; значения — в JSON рядом с задачей, показываются в карточках и
  выгружаются в Excel.
- **Переходы workflow.** Статусы фиксированы
  (open → pending_review → completed / rework → closed), но для каждого
  перехода настраивается список возможностей, любой из которых достаточно.
- Задания с историей переходов и комментариями, мониторинги по адресам,
  отдельные отчёты Экипажа и комментарии Руководителя, единая лента с
  пагинацией, Excel-экспорт с фильтрами.
- Медиа: фото/видео с лимитами (per-file и квота на хранилище), выдача только
  через авторизованный `GET /api/media/{filename}`; опционально отдельный
  private-каталог (`PRIVATE_MEDIA_DIR`).
- Публичный `GET /healthz` для health-check; интерактивная OpenAPI на `/docs`.

## Архитектура

| Слой | Реализация |
|---|---|
| API + страницы | FastAPI, Pydantic (`app/app.py`) |
| HTML | Jinja2 (`app/templates/*.html`) |
| Клиент | Vanilla JS/CSS (`app/static/*.js`) |
| Данные | SQLAlchemy Core + psycopg (PostgreSQL) / sqlite3 (SQLite), миграции Alembic (`app/database.py`, `app/migrations/`) |
| Аутентификация | JWT HS256, bcrypt, серверные сессии (`app/auth.py`) |
| Медиа | Pillow, авторизованный streaming-эндпоинт |
| Экспорт | OpenPyXL |
| Runtime | Uvicorn / Gunicorn (сборка в `app/Dockerfile`, compose-файлы) |

Ключевые модули: `app/app.py` (эндпоинты и аудит), `app/auth.py`
(сессии/роли/возможности), `app/database.py` (адаптер двух СУБД, миграции,
 tenant-контекст), `app/permissions.py` (каталог возможностей и системные
роли), `migrate_sqlite_to_pg.py` (перенос данных с контрольными суммами).

## Запуск локально

```bash
cd app
uv run --with-requirements requirements.txt uvicorn app:app --host 127.0.0.1 --port 8080
```

Переменные окружения: `JWT_SECRET` (обязателен, 32+ символа), `DB_BACKEND`
(`sqlite` по умолчанию / `postgres` + `DATABASE_URL`), `DB_PATH`, `UPLOADS_DIR`,
опционально `PRIVATE_MEDIA_DIR`. Подробнее — `app/DEPLOYMENT.md`.

## Тесты

SQLite и PostgreSQL (встроенный pgserver) в одном наборе; Playwright-сценарии
для ролевых страниц:

```bash
cd app
uv run --with-requirements requirements-dev.txt python -m unittest discover -s tests -v

# E2E (нужны Chrome и bun)
cd tests/e2e && bun install
E2E_ROOT=/tmp/albion-e2e-run E2E_PORT=18001 ../../.venv/bin/python setup_db.py
./start_server.sh &   # затем
BASE_URL=http://127.0.0.1:18001 CHROME_PATH=/usr/bin/google-chrome bun run e2e_roles.js
```

## Инструменты

- `app/maintenance_cleanup.py` — безопасная чистка просроченных временных
  загрузок (dry-run по умолчанию, `--apply` для удаления).
- `benchmarks/benchmark_feeds.py` — замер SQL-запросов, латентности и
  размера payload лент на синтетических данных.
- `migrate_sqlite_to_pg.py` — перенос SQLite → PostgreSQL с отчётом
  (`--dry-run`, `--truncate`), сверкой количества строк и контрольных сумм.

## Текущее состояние

Ветка `feature/postgres-core`: PostgreSQL-бэкенд, многотенантность по юрлицам,
сессии/аудит, конструктор ролей, типы заданий и настройка переходов. Известные
ограничения: изоляция юрлиц по чтению — только на PostgreSQL; в Excel-экспорте
выгружаются задания и мониторинги без вложений; интеграция с Альбион.Диском
(полные файлы + previews) — следующий этап.
