# PostgreSQL: схема, миграции и перенос данных

PostgreSQL — целевой backend. Приложение поддерживает оба режима через
`DB_BACKEND`: `sqlite` (по умолчанию, локальная разработка) и `postgres`.
Переключение не меняет API и UI.

## Схемы

```text
core                          monitoring
├── legal_entities            ├── addresses (shared)
├── branches                  ├── tasks
├── users                     ├── task_status_history
├── user_entities             ├── reports
├── sessions                  ├── media
├── roles                     ├── comments
├── capabilities              ├── director_comments
├── role_capabilities         ├── crew_monitorings
├── role_scopes               ├── crew_monitoring_media
├── user_roles                ├── crew_report_comments
├── audit_log                 ├── crew_report_comment_media
├── domains                   └── settings
├── task_types
└── workflow_definitions
```

Все таблицы `monitoring`, кроме `settings`, содержат `legal_entity_id`.
`addresses.shared` помечает общий для группы адрес.

## Переменные окружения

```env
DB_BACKEND=postgres
POSTGRES_DB=albion
POSTGRES_USER=albion
POSTGRES_PASSWORD=change-me
DATABASE_URL=postgresql+psycopg://albion:change-me@db:5432/albion
```

`DB_PATH` остаётся для аварийного отката на SQLite.

## Переключение приложения

```env
DB_BACKEND=postgres
DATABASE_URL=postgresql+psycopg://albion:...@db:5432/albion
```

1. `alembic upgrade head`;
2. перенос данных (`migrate_sqlite_to_pg.py --truncate`) и проверка `ok: true`;
3. `DB_BACKEND=postgres` и перезапуск приложения.

Откат: вернуть `DB_BACKEND=sqlite`, перезапустить. SQLite-файл не изменяется.

## Локальная разработка и тесты

Docker локально не нужен: тесты поднимают встроенный PostgreSQL через `pgserver`.

```bash
cd app
uv run --with-requirements requirements-dev.txt python -m unittest discover -s tests -v
```

Проверяются:

- схема `core`/`monitoring`, колонки, индексы, RLS-политики и функции;
- идемпотентность `upgrade`/`downgrade`;
- перенос SQLite → PostgreSQL со сверкой count и контрольных сумм;
- изоляция юрлиц через RLS и групповой доступ.

## Сервер

```bash
cd app
docker compose up -d db
docker compose run --rm migrate        # alembic upgrade head
```

`migrate` — одноразовый контейнер, чтобы миграции не запускались из четырёх
воркеров одновременно.

## Перенос данных из SQLite

Порядок:

1. Backup SQLite и `uploads/`.
2. Задать `POSTGRES_PASSWORD` и `DATABASE_URL` в `app/.env`.
3. Поднять PostgreSQL и выполнить `alembic upgrade head`.
4. Preview без записи:

```bash
python migrate_sqlite_to_pg.py --dry-run
```

5. Перенос в пустую целевую БД:

```bash
python migrate_sqlite_to_pg.py --truncate
```

6. Проверить отчёт: `ok: true`, все `match: true`, контрольные суммы совпали.
7. Только после этого переключать приложение на `DB_BACKEND=postgres`.

Скрипт:

- читает SQLite только в режиме `mode=ro`;
- пишет одной транзакцией;
- сохраняет ID, конвертирует время в `timestamptz` (UTC);
- переносит `media_json` в `jsonb`;
- выставляет последовательности identity после импорта;
- отказывается писать в непустую БД без `--truncate`;
- не удаляет SQLite и файлы медиа.

## RLS

Политика `tenant_isolation` включена на 11 таблицах `monitoring`.
Приложение выставляет на транзакцию:

```sql
SET LOCAL app.legal_entity_ids = '1,2';
SET LOCAL app.is_group_admin = 'on';
```

- строки видны, если `legal_entity_id` входит в список;
- `addresses.shared = 1` виден всем юрлицам;
- групповой администратор видит все юрлица.

Миграция создаёт роль `albion_app` без `BYPASSRLS` и выдаёт ей гранты.
Подключение приложения выполняется от владельца, но сразу делает
`SET ROLE albion_app`, поэтому RLS применяется и к приложению.
Сейчас контекст однотенантный (`app.legal_entity_ids = '1'`); выбор юрлица
из сессии пользователя — следующий этап вместе с UI ролей.

## Откат

SQLite не изменяется и остаётся рабочим:

```env
DB_BACKEND=sqlite
DB_PATH=/data/monitoring.db
```

## Ограничения текущего шага

- Приложение умеет работать на PostgreSQL, но production пока переключается
  вручную через `DB_BACKEND`.
- Тенант-контекст приложения фиксирован на юрлицо 1; per-user выбор юрлица
  появится вместе с UI ролей.
- Медиа остаётся локальной (`storage='local'`); интеграция с Альбион.Диском —
  отдельный этап.
- Конструктор ролей и workflow только заложен схемой; бизнес-логика позже.
