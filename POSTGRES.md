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
Юрлицо берётся из сессии пользователя (см. «Доступ» ниже), а не из
конфигурации приложения. Отладочный вывод контекста включается переменной
`ALBION_DEBUG_TENANT=1`: при каждом открытии соединения печатается
`TENANT checkout entity=… group_admin=…`.

`core.audit_log` тоже под RLS: запись видна своему юрлицу, строки без
юрлица (неудачные входы) доступны всем, групповой администратор видит всё.

## Откат

SQLite не изменяется и остаётся рабочим:

```env
DB_BACKEND=sqlite
DB_PATH=/data/monitoring.db
```

## Доступ: сессии, юрлицо и аудит

Один вход создаёт строку в `auth_sessions` (SQLite) / `core.sessions`
(PostgreSQL). JWT привязан к сессии: в токене лежат `session_id`,
`token_version` и `legal_entity_id`.

Проверка запроса (`auth.get_current_user`):

1. обязательна запись `session_id`;
2. сессия не отозвана, не закрыта и не истекла;
3. пользователь активен (`users.active`), версия прав в токене совпадает с БД;
4. отпечаток токена совпадает с `token_hash`;
5. роль, активность и юрлицо берутся из БД, а не из токена;
6. найденное юрлицо уходит в контекст запроса и в RLS.

Отзыв сессий:

- выход (`/api/logout`) закрывает ровно свою сессию;
- смена пароля (`PUT /api/users/{id}/password`) увеличивает `token_version`
  и отзывает все сессии пользователя;
- блокировка пользователя (`users.active = 0`) закрывает доступ по
  существующим токенам.

Действия пишутся в `audit_log` / `core.audit_log`: вход, отказ входа,
блокировка, выход, смена пароля, настройки, задания и их переходы.
Ошибка записи аудита не роняет запрос, но и не теряется: запись идёт через
`SAVEPOINT`, а неудачная попытка попадает в лог приложения.

Справочники (`/api/addresses`, `/api/cities`, `/api/manufacturers`) требуют
токен: без него неизвестно юрлицо пользователя.

## Область видимости: юрлицо сессии

Активное юрлицо хранится на серверной сессии (`sessions.legal_entity_id`,
`sessions.all_entities`), а не в токене. Переключение не требует нового JWT и
проверяется на каждом запросе.

- Обычные роли видят только те юрлица, где есть членство:
  `core.user_entities` (или собственное `users.legal_entity_id`).
- Групповой администратор (роль `admin`/`administrator`) может выбрать любое
  активное юрлицо группы и дополнительно режим «Все юрлица».
- В режиме «Все юрлица» RLS-флаг `app.is_group_admin = 'on'` — видно данные
  всех юрлиц; в конкретном юрлице администратор тоже ограничен им.
- Создание записей в сводном режиме запрещено (`HTTP 400`): неизвестно, чьими
  будут данные. Комментарии, вложения и переходы статусов привязаны к своему
  объекту и наследуют его юрлицо, поэтому доступны и в этом режиме.
- Переключение пишет `auth.entity.switch` в журнал.

Эндпоинты: `GET /api/legal-entities` (доступные юрлица и текущий выбор),
`POST /api/session/legal-entity` (`{"legal_entity_id": 1}` или
`{"legal_entity_id": null}` для сводного режима).

## Ограничения текущего шага

- Приложение умеет работать на PostgreSQL, но production пока переключается
  вручную через `DB_BACKEND`.
- Изоляция данных между юрлицами обеспечивается RLS и работает только на
  PostgreSQL. В режиме SQLite область видимости влияет на список пользователей,
  создание и запись юрлица в новые строки, но ленты и отчёты не фильтруются —
  SQLite остаётся аварийным откатом, а не многотенантной площадкой.
- Юрлица и членство заводятся SQL-скриптом (см. `DEPLOYMENT.md`); CRUD-интерфейс
  администратора — следующий этап.
- Медиа остаётся локальной (`storage='local'`); интеграция с Альбион.Диском —
  отдельный этап.
- Конструктор ролей и workflow только заложен схемой; бизнес-логика позже.
- Время сессии PostgreSQL принудительно UTC (`-c TimeZone=UTC`), чтобы
  naive-UTC значения приложения не сдвигались относительно SQLite.
