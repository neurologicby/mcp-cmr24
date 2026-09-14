# CMR24 MCP Server

MCP-сервер предоставляет операции CMR24 в двух стабильных представлениях над одной бизнес-логикой:

- `/mcp/single` — компактный каталог из `call_tool` и `list_available_tools` для клиентов с ограниченным контекстом;
- `/mcp/native` — отдельный типизированный MCP tool для каждой операции.

Каталог выбирается адресом endpoint и не меняется в течение MCP-сессии. Оба режима используют один `ROUTER`, одинаковую авторизацию, проверку прав, подтверждения и формат `Result`.

## Быстрый запуск

1. Скопируйте `.env.example` в `.env` и выберите `AUTH_MODE`.
2. Установите прямые зависимости в Windows:

   ```powershell
   python -m pip install -r requirements.in
   ```

3. Запустите сервер:

   ```powershell
   python start_server.py
   ```

По умолчанию сервер слушает только `127.0.0.1:8000`. Проверки состояния доступны на `/healthz` и `/readyz`, метрики Prometheus — на `/metrics` после авторизации.

## Авторизация и права

`AUTH_MODE=per_request` требует `Authorization: Bearer <CMR24 key>` для каждого запроса и никогда не использует `CMR24_AUTHKEY` как fallback.

`AUTH_MODE=service_account` использует только `CMR24_AUTHKEY`. В этом режиме источник запроса должен входить в `MCP_SERVICE_ACCOUNT_NETWORKS` либо пройти проверку доверенного reverse proxy. Не публикуйте service-account режим непосредственно в пользовательскую сеть.

Права задаются `MCP_DEFAULT_SCOPES`. Без явной настройки разрешено только `cargo.read`. Доступные scope:

- `cargo.read`
- `cargo.write`
- `cargo.delete`
- `cargo.restore`

Заголовки `X-CMR24-Scopes` и `X-CMR24-Caller` принимаются только при `MCP_TRUST_SCOPE_HEADER=true`, то есть после аутентифицирующего reverse proxy. Для внешнего доступа используйте TLS, проверку Host, лимиты запросов и журнал доступа на прокси. Разрешённые Host и Origin задаются `MCP_ALLOWED_HOSTS` и `MCP_ALLOWED_ORIGINS`.

## Безопасное удаление

Удаление выполняется в два шага. Сначала вызовите `request_delete_confirmation` с ID заявки. Затем передайте полученный короткоживущий `confirmation_token` в `delete_cargo` для того же ID. Токен связан с вызывающим субъектом, операцией и ID и может быть использован только один раз.

## Результаты операций

Каждая операция возвращает одинаковую структуру:

```json
{
  "ok": true,
  "code": "ok",
  "message": "Операция выполнена",
  "data": {},
  "request_id": "...",
  "retriable": false
}
```

Ответ HTTP 200 от CMR24 не считается успехом, если тело содержит `error`, `success: false`, `ok: false` или не подтверждает изменение данных. Таймауты, 401, 429 и транспортные ошибки имеют стабильные коды.

При создании заявки `data.publication_actor` имеет значение `main_account`, если `id_user` отсутствует, либо `employee` с подтверждённым ID сотрудника. Идентичность вызывающего MCP-клиента не подставляется в `id_user`.

## Тесты

Функциональные тесты не обращаются к реальному CMR24 и не создают заявки:

```powershell
python -m unittest discover -s tests -v
```

Нагрузочный тест измеряет in-process путь реестра и Result без внешней сети:

```powershell
python -m tests.load_test --requests 5000 --concurrency 100
```

Для контрактных smoke-тестов реального API используйте только архивный endpoint `add-cargo-in-archive` и отдельный тестовый аккаунт.

## Обновление зависимостей

`requirements.in` содержит прямые версии и подходит для локальной разработки, включая Windows. `requirements.lock` — полный Linux lock с SHA-256 хэшами для Docker-образа. После осознанного обновления версии пересоберите lock:

```powershell
uv pip compile requirements.in --generate-hashes --python-version 3.13 --python-platform x86_64-unknown-linux-gnu --output-file requirements.lock
```
