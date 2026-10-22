# PostgreSQL: схема, миграции и перенос данных

PostgreSQL — целевой backend. Текущее приложение пока работает на SQLite;
этот слой подготовлен и протестирован, переключение endpoint-ов — следующий шаг.

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
- `addresses.shared = true` виден всем юрлицам;
- групповой администратор видит все юрлица.

Приложение должно подключаться ролью без `BYPASSRLS` и без прав владельца
таблиц. Создание роли и выдача грантов — часть переключения приложения на PG.

## Откат

SQLite не изменяется и остаётся рабочим:

```env
DB_BACKEND=sqlite
DB_PATH=/data/monitoring.db
```

## Ограничения текущего шага

- `app.py` продолжает работать через SQLite; слой PG подготовлен, endpoint-ы
  ещё не переведены.
- Медиа остаётся локальной (`storage='local'`); интеграция с Альбион.Диском —
  отдельный этап.
- Конструктор ролей и workflow только заложен схемой; бизнес-логика позже.
