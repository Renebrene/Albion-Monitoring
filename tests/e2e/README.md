# E2E role scenarios

Изолированный Playwright-набор проверяет ролевую согласованность UI/API:
задания, комментарии, review/rework, мониторинг, отчёты экипажа и настройки.

Тест использует **только временную БД и временный uploads-каталог**. Рабочие
`data/` и `uploads/` не затрагиваются.

## Запуск

Нужны системный Chrome/Chromium и Python-зависимости приложения (`.venv`).

```bash
cd app/tests/e2e
bun install

export E2E_ROOT=/tmp/albion-e2e-run
export E2E_PORT=18001
rm -rf "$E2E_ROOT"

../../.venv/bin/python setup_db.py
./start_server.sh > "$E2E_ROOT/server.log" 2>&1 &
SERVER_PID=$!
sleep 3
curl -fsS "http://127.0.0.1:$E2E_PORT/healthz"

BASE_URL="http://127.0.0.1:$E2E_PORT" \
CHROME_PATH="${CHROME_PATH:-/usr/bin/google-chrome}" \
bun run e2e_roles.js

kill "$SERVER_PID"
```

`setup_db.py` создаёт пользователей всех ролей (`admin`, `service`, `director`,
`crew`, `head`, `staff`, `administrator`, `curator`) с паролем `TestPass1!`.
Пароль можно переопределить через `E2E_PASSWORD`. Дополнительно создаются
второе юрлицо и пользователь `scope-admin` с доступом к обоим юрлицам — на нём
проверяется переключатель области видимости.

## Что проверяется

- вход и landing page каждой роли;
- `staff = service`, `administrator = admin`, `curator = director`;
- создание задания, комментарий, отправка на проверку, approve/rework;
- director не проверяет чужие задания;
- создание мониторинга и комментарии директора/админа;
- отчёт экипажа, комментарий Руководителя и его видимость Экипажу;
- отсутствие нерабочей формы комментария к `crew_report` у admin/director;
- настройки и список пользователей у administrator;
- переключатель юрлица: список доступных юрлиц, сводный режим «Все юрлица»
  (создание запрещено сервером) и сохранение выбора в сессии;
- отсутствие console/page errors (кроме favicon 404).
