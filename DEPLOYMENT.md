# Публичный деплой Albion Monitoring

Ниже — рекомендуемый вариант: Linux VPS + Docker Compose + Nginx/Let's Encrypt.
Приложение слушает только `127.0.0.1:18000`; наружу открыт только HTTPS (443).

## 1. Что нужно

- VPS: минимум 2 vCPU / 4 GiB RAM / SSD, лучше 4 vCPU / 8 GiB.
- Ubuntu 22.04/24.04 (или другой Linux с Docker).
- Домен, например `albion.example.com`, и A/AAAA-запись на IP сервера.
- Открытые порты: `22/tcp`, `80/tcp`, `443/tcp`. Порт `18000` наружу **не открывать**.
- Резервное копирование БД и uploads. Один экземпляр приложения: SQLite и локальные
  файлы не предназначены для горизонтального масштабирования.

## 2. Установка Docker

```bash
sudo apt update
sudo apt install -y ca-certificates curl git ufw
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
```

Проверка:

```bash
docker compose version
```

Включите автозапуск Docker при загрузке сервера:

```bash
sudo systemctl enable --now docker
```

## 3. Код и переменные окружения

```bash
sudo mkdir -p /opt/albion-monitoring
sudo chown "$USER":"$USER" /opt/albion-monitoring
git clone https://github.com/Renebrene/albion-monitoring-demo.git /opt/albion-monitoring
cd /opt/albion-monitoring/app
git checkout <release-branch>   # например feature/postgres-core до merge
```

Создайте `app/.env` (файл уже в `.gitignore`, в git не попадёт):

```bash
PG_PASSWORD=$(openssl rand -base64 32)
cat > .env <<EOF
JWT_SECRET=$(openssl rand -base64 48)
DB_PATH=/data/monitoring.db
UPLOADS_DIR=/uploads
POSTGRES_DB=albion
POSTGRES_USER=albion
POSTGRES_PASSWORD=$PG_PASSWORD
DATABASE_URL=postgresql+psycopg://albion:$PG_PASSWORD@db:5432/albion
DB_BACKEND=sqlite
EOF
chmod 600 .env
```

Требования:

- `JWT_SECRET` — минимум 32 символа, случайный. Смена ключа завершает все активные сессии.
- Не используйте старые `SECRET_KEY`/`DATABASE_URL` вместо `JWT_SECRET`.
- `DB_BACKEND=sqlite` — текущий режим; после переноса данных и проверки
  переключается на `postgres` (см. `POSTGRES.md`).
- `PRIVATE_MEDIA_DIR` задавайте только если действительно смонтировали отдельный
  приватный каталог (см. `TECHNICAL_DESCRIPTION.md`).

Создайте постоянные каталоги:

```bash
mkdir -p ../data ../uploads ../pgdata
chmod 750 ../data ../uploads ../pgdata
```

Compose монтирует `../data` → `/data`, `../uploads` → `/uploads`,
`../pgdata` → данные PostgreSQL. Все три каталога должны попадать в backup
(после перехода на PG — `pg_dump`, см. `POSTGRES.md`).

## 4. Запуск приложения

Используйте основной `docker-compose.yml`:

```bash
cd /opt/albion-monitoring/app
docker compose config
docker compose up -d --build
docker compose run --rm migrate   # alembic upgrade head
docker compose ps
docker compose logs --tail=100 app
```

Проверка только с самого сервера:

```bash
curl -fsS http://127.0.0.1:18000/healthz
curl -I http://127.0.0.1:18000/login
ss -ltnp | grep 18000
```

В `ss` адрес должен быть `127.0.0.1:18000`, а не `0.0.0.0:18000`.

Пока `DB_BACKEND=sqlite`, приложение работает как раньше. PostgreSQL поднят,
схема применена, данные можно перенести по `POSTGRES.md`.

После проверки отчёта миграции (`ok: true`):

```bash
# в app/.env
DB_BACKEND=postgres
docker compose up -d app
curl -fsS https://albion.example.com/healthz
```

Откат — вернуть `DB_BACKEND=sqlite` и перезапустить app; SQLite-файл остаётся
нетронутым.

### Сессии, юрлица и аудит после обновления

- Каждый вход теперь создаёт серверную сессию: выход, смена пароля и
  блокировка пользователя немедленно отзывают доступ по старым токенам.
  После обновления все пользователи входят заново — старые JWT без
  `session_id` не принимаются.
- Роль, активность и юрлицо берутся из БД, а не из токена: понижение роли
  действует сразу, без перевыпуска токена.
- Действия пишутся в журнал: `audit_log` (SQLite) или `core.audit_log`
  (PostgreSQL). Проверить можно так:

  ```bash
  docker compose exec db psql -U albion -d albion \
    -c "SELECT occurred_at, actor_user_id, legal_entity_id, action FROM core.audit_log ORDER BY id DESC LIMIT 20"
  ```

- Справочники адресов (`/api/addresses`, `/api/cities`, `/api/manufacturers`)
  отвечают только авторизованным запросам: без токена неизвестно юрлицо.
- Диагностика RLS: запустить app с `ALBION_DEBUG_TENANT=1` — при каждом
  открытии соединения печатается контекст юрлица.

### Второе юрлицо группы

Юрлица и членство пока заводятся SQL-скриптом (UI администратора — отдельный
этап). Пример для PostgreSQL:

```sql
-- 1. Новое юрлицо
INSERT INTO core.legal_entities (name, short_name, inn)
VALUES ('ООО Второе', 'Второе', '6160000000');

-- 2. Членство: кто имеет доступ и с какой ролью в этом юрлице
INSERT INTO core.user_entities (user_id, legal_entity_id, role, is_default)
SELECT id, 2, role, false FROM core.users WHERE login = 'сервис1';

-- 3. При необходимости — «домашнее» юрлицо и адреса
UPDATE core.users SET legal_entity_id = 2 WHERE login = 'сервис1';
UPDATE monitoring.addresses SET shared = 0 WHERE id = /* личный адрес */;

-- 4. Сбросить активный выбор сессий, чтобы новые правила применились сразу
UPDATE core.sessions SET legal_entity_id = NULL, all_entities = 0;
```

После этого пользователь при следующем входе получит выбор юрлица в шапке,
если ему доступно больше одного. Адрес с `shared = 1` виден всем юрлицам
группы; перенесённые из SQLite адреса помечаются общими автоматически.

В режиме SQLite изоляция чтения между юрлицами не применяется (RLS есть только
в PostgreSQL) — многотенантная эксплуатация предполагает `DB_BACKEND=postgres`.

## 5. HTTPS

### Вариант A — Caddy (рекомендуется, автоматический TLS)

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile        # заменить albion.example.com
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager
```

Caddy сам получит и будет обновлять сертификат. В примере уже закрыты
`/docs`, `/redoc`, `/openapi.json`, `/api/login-users`. Ограничение частоты
запросов к `/api/login` у Caddy нет — добавьте Cloudflare/fail2ban либо
используйте вариант B.

### Вариант B — Nginx + certbot (rate limit и security headers)

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo mkdir -p /var/www/certbot
```

Сначала поднимите только HTTP-конфиг, иначе Nginx не стартует без сертификата:

```bash
sudo tee /etc/nginx/sites-available/albion-monitoring >/dev/null <<'EOF'
server {
    listen 80;
    server_name albion.example.com;
    root /var/www/certbot;

    location /.well-known/acme-challenge/ { }
    location / { return 301 https://$host$request_uri; }
}
EOF
sudo ln -sf /etc/nginx/sites-available/albion-monitoring /etc/nginx/sites-enabled/albion-monitoring
sudo nginx -t && sudo systemctl reload nginx

sudo certbot certonly --webroot -w /var/www/certbot -d albion.example.com
```

Теперь замените конфиг полным примером (там уже есть HTTPS, rate limit,
`client_max_body_size 110m`, закрытые docs/login-users и security headers):

```bash
sudo cp deploy/nginx-albion.conf.example /etc/nginx/sites-available/albion-monitoring
sudo nano /etc/nginx/sites-available/albion-monitoring   # домен и пути к сертификату
sudo nginx -t && sudo systemctl reload nginx
```

Проверка:

```bash
curl -I https://albion.example.com/login
curl -fsS https://albion.example.com/healthz
sudo certbot renew --dry-run
sudo systemctl list-timers | grep certbot
```

В Nginx-примере уже:

- редирект HTTP → HTTPS;
- ограничение размера запроса `110m` (видео до 100 MiB + multipart);
- проксирование на `127.0.0.1:18000`;
- закрытые `/docs`, `/redoc`, `/openapi.json`, `/api/login-users`;
- security headers и rate limit на `/api/login`.

## 6. Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

Не открывайте `18000/tcp`. Проверьте, что приложение недоступно напрямую:

```bash
curl --connect-timeout 3 http://PUBLIC_IP:18000/healthz   # должно не подключиться
```

## 7. Backup и restore

SQLite backup без остановки приложения:

```bash
cd /opt/albion-monitoring/app
mkdir -p /var/backups/albion
docker compose exec -T app python - <<'PY'
import sqlite3
src = sqlite3.connect("/data/monitoring.db")
dst = sqlite3.connect("/data/monitoring.db.backup")
src.backup(dst)
dst.close()
src.close()
PY
cp ../data/monitoring.db.backup "/var/backups/albion/monitoring-$(date +%F-%H%M).db"
tar czf "/var/backups/albion/uploads-$(date +%F-%H%M).tgz" -C /opt/albion-monitoring uploads
```

Cron (пример — ежедневно в 03:15):

```cron
15 3 * * * cd /opt/albion-monitoring/app && docker compose exec -T app python -c "import sqlite3; s=sqlite3.connect('/data/monitoring.db'); d=sqlite3.connect('/data/monitoring.db.backup'); s.backup(d); d.close(); s.close()" && cp ../data/monitoring.db.backup /var/backups/albion/monitoring-$(date +\%F).db && tar czf /var/backups/albion/uploads-$(date +\%F).tgz -C /opt/albion-monitoring uploads
```

Храните копии вне сервера. Периодически проверяйте restore на отдельной машине:

1. остановить приложение;
2. заменить `data/monitoring.db` и `uploads/`;
3. поднять приложение;
4. проверить `/healthz`, вход и одну карточку.

Контейнер по умолчанию работает от root, поэтому созданные им файлы могут быть
root-owned. Если `cp`/restore от вашего пользователя не имеет прав, используйте
`sudo` либо заранее запускайте контейнер под UID/GID владельца `data`/`uploads`.

Перед обновлением приложения **всегда** делайте backup: миграции выполняются
при импорте `app.py` и могут менять схему.

## 8. Обновление и rollback

```bash
cd /opt/albion-monitoring/app
# backup
git pull
docker compose up -d --build
docker compose ps
curl -fsS https://albion.example.com/healthz
```

Rollback:

```bash
git checkout <previous-commit>
docker compose up -d --build
# при необходимости восстановить БД/uploads из backup
```

Не запускайте `init_db.py` на рабочей БД: он seed-ит пользователей и импортирует адреса.

## 9. Ограничения и безопасность

- **Один инстанс.** SQLite + локальные uploads; не запускайте несколько replicas
  на разных машинах. Четыре Gunicorn workers не означают четыре независимых writer.
- **Rate limit.** В самом приложении нет ограничения попыток входа; используйте
  Nginx `limit_req` (есть в примере), fail2ban или Cloudflare.
- **`/api/login-users`.** Публичный список логинов/ролей не нужен форме входа;
  в примере Nginx он закрыт. Не открывайте его в интернет.
- **`/docs`.** FastAPI docs закрыты в примере Nginx. Если они нужны, ограничьте
  доступ по IP/VPN.
- **TLS обязателен.** JWT хранится в `localStorage`; без HTTPS его легко перехватить.
- **Обновления зависимостей** и ротация `JWT_SECRET` — по регламенту.
- **Cloudflare:** бесплатный план ограничивает тело запроса ~100 MB; видео
  до 100 MiB + multipart может не пройти. Для больших файлов используйте
  DNS-only или платный план.
- **Приватные медиа:** при использовании `PRIVATE_MEDIA_DIR` смонтируйте только
  этот каталог и настройте Linux service user/group; не монтируйте весь
  `ALBION_DATA_DIR` или публичный `albion-files/uploads/`.

## 10. Вариант Render/PaaS

В репозитории есть `render.yaml`, но free-plan Render использует эфемерный диск:
SQLite и uploads будут потеряны при redeploy/restart. Для production нужен
persistent disk, один instance и явно заданный `JWT_SECRET`. Для файлов лучше
внешнее private storage; текущая архитектура рассчитана на локальный диск.
