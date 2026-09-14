# Развёртывание и подключение CMR24 MCP Server

> Практическая инструкция для версии **0.1.0**. Рекомендуемая production-схема:
> **MCP-клиент → HTTPS reverse proxy → Docker-контейнер → CMR24 API**.

## Содержание

- [Что понадобится](#что-понадобится)
- [Выбор режима авторизации](#выбор-режима-авторизации)
- [Быстрый локальный запуск](#быстрый-локальный-запуск)
- [Классический деплой на Ubuntu](#классический-деплой-на-ubuntu)
- [Production-деплой в Docker](#production-деплой-в-docker)
- [Настройка HTTPS через Nginx](#настройка-https-через-nginx)
- [Подключение MCP-клиента](#подключение-mcp-клиента)
- [Проверка после подключения](#проверка-после-подключения)
- [Эксплуатация и обновление](#эксплуатация-и-обновление)
- [Диагностика](#диагностика)
- [Итоговый чек-лист](#итоговый-чек-лист)

## Что понадобится

Для локального запуска:

- Python 3.13;
- Git;
- действующий ключ CMR24.

Для production-деплоя дополнительно:

- Linux-сервер с Docker Engine;
- домен, например `mcp.example.com`;
- TLS-сертификат;
- Nginx или другой reverse proxy;
- входящий доступ только к `443/tcp`.

В репозитории находятся два MCP endpoint:

| Endpoint | Когда использовать |
|---|---|
| `/mcp/single` | Рекомендуется по умолчанию. Компактный каталог `call_tool` и `list_available_tools` экономит контекст клиента. |
| `/mcp/native` | Нужен клиентам, которым удобны отдельные типизированные инструменты для каждой операции. |

Оба endpoint используют протокол **Streamable HTTP**, одну бизнес-логику и одинаковые правила доступа.

## Выбор режима авторизации

### `per_request` — рекомендуемый режим

Каждый MCP-клиент отправляет собственный ключ CMR24:

```http
Authorization: Bearer <CMR24_AUTHKEY>
```

Сервис использует этот ключ только для текущего запроса и передаёт его CMR24 API. Значение `CMR24_AUTHKEY` на сервере в этом режиме не требуется.

Этот вариант подходит, если:

- пользователи должны работать под разными ключами;
- доступ предоставляется через интернет;
- требуется раздельная отзывчивость credentials.

### `service_account` — только для доверенного контура

Сервис использует один серверный `CMR24_AUTHKEY`. Входящие запросы разрешены только из сетей `MCP_SERVICE_ACCOUNT_NETWORKS` или от доверенного прокси с заранее согласованным секретным заголовком.

Не публикуйте `service_account` напрямую в пользовательскую или публичную сеть. Для обычного удалённого подключения выбирайте `per_request`.

## Быстрый локальный запуск

### Windows PowerShell

```powershell
git clone https://github.com/neurologicby/mcp-cmr24.git
Set-Location mcp-cmr24\mcp-cmr24
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements-windows.lock
```

Откройте `.env` и оставьте безопасные локальные настройки:

```dotenv
AUTH_MODE=per_request
CMR24_BASE_URL=https://cmr24.by/api
MCP_HOST=127.0.0.1
MCP_PORT=8000
MCP_ALLOWED_HOSTS=127.0.0.1,localhost,[::1]
MCP_DEFAULT_SCOPES=cargo.read
```

Запустите сервер:

```powershell
python start_server.py
```

Проверьте состояние в другом окне PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/readyz
```

Оба запроса должны вернуть успешный статус. Локальный MCP URL:

```text
http://127.0.0.1:8000/mcp/single
```

> **Важно:** файл `.env` исключён из Git. Не прикладывайте его к issue, логам, архивам или сообщениям.

## Классический деплой на Ubuntu

Этот вариант запускает приложение без Docker: исходный код и virtualenv находятся в `/opt`, настройки — в `/etc`, процесс контролирует `systemd`, а HTTPS завершает Nginx.

### Схема каталогов

```text
/opt/cmr24-mcp/source/          # Git checkout
/opt/cmr24-mcp/venv/            # Python virtualenv
/etc/cmr24-mcp/cmr24.env        # закрытая конфигурация
/etc/systemd/system/cmr24-mcp.service
```

### 1. Подготовьте систему

Установите системные инструменты:

```bash
sudo apt update
sudo apt install --yes git nginx curl ca-certificates
```

Проект и lock-файлы проверяются в CI на Python 3.13. Установите Python 3.13 и модуль `venv` из доверенного источника, принятого в вашей инфраструктуре, затем проверьте:

```bash
python3.13 --version
python3.13 -m venv --help >/dev/null
```

Ожидается Python `3.13.x`. На выпусках Ubuntu, где этой версии нет в штатном репозитории, не подключайте неизвестный PPA вслепую: используйте одобренный внутренний репозиторий, официальный образ/сборку Python или Docker-сценарий из следующего раздела.

Создайте отдельного системного пользователя без интерактивного входа:

```bash
sudo adduser \
  --system \
  --group \
  --home /opt/cmr24-mcp \
  --shell /usr/sbin/nologin \
  cmr24
```

### 2. Установите приложение

Клонируйте репозиторий от имени сервисного пользователя и выберите release-тег:

```bash
sudo -u cmr24 git clone \
  https://github.com/neurologicby/mcp-cmr24.git \
  /opt/cmr24-mcp/source

sudo -u cmr24 git -C /opt/cmr24-mcp/source checkout v0.1.0
```

Создайте virtualenv и установите зависимости строго по Linux lock-файлу:

```bash
sudo -u cmr24 python3.13 -m venv /opt/cmr24-mcp/venv
sudo -u cmr24 /opt/cmr24-mcp/venv/bin/python -m pip install \
  --require-hashes \
  --requirement /opt/cmr24-mcp/source/mcp-cmr24/requirements.lock
```

Проверьте, что рабочее дерево не содержит локальных изменений:

```bash
sudo -u cmr24 git -C /opt/cmr24-mcp/source status --short
```

Команда не должна ничего вывести.

### 3. Создайте закрытую конфигурацию

Env-файл должен находиться вне checkout и читаться только `root` и группой сервиса:

```bash
sudo install -d -o root -g cmr24 -m 750 /etc/cmr24-mcp
sudo install \
  -o root \
  -g cmr24 \
  -m 640 \
  /opt/cmr24-mcp/source/mcp-cmr24/.env.example \
  /etc/cmr24-mcp/cmr24.env
sudoedit /etc/cmr24-mcp/cmr24.env
```

Базовая конфигурация для `per_request`:

```dotenv
AUTH_MODE=per_request
CMR24_BASE_URL=https://cmr24.by/api
MCP_HOST=127.0.0.1
MCP_PORT=8000
MCP_ALLOWED_HOSTS=mcp.example.com
MCP_ALLOWED_ORIGINS=
MCP_DEFAULT_SCOPES=cargo.read
MCP_TRUST_SCOPE_HEADER=false
MCP_RATE_LIMIT_REQUESTS=120
MCP_RATE_LIMIT_WINDOW=60
```

Замените `mcp.example.com` своим доменом. В режиме `per_request` не записывайте `CMR24_AUTHKEY` в серверный env-файл: ключ передаёт каждый MCP-клиент в Bearer-заголовке.

Если разрешаете `cargo.delete`, сгенерируйте отдельный секрет:

```bash
openssl rand -hex 32
```

И сохраните его только в `/etc/cmr24-mcp/cmr24.env`:

```dotenv
MCP_DEFAULT_SCOPES=cargo.read,cargo.write,cargo.delete
CONFIRMATION_SECRET=<СЛУЧАЙНОЕ_ЗНАЧЕНИЕ_ИЗ_OPENSSL>
```

Проверьте права, не выводя содержимое файла:

```bash
sudo stat --format='%U %G %a %n' /etc/cmr24-mcp/cmr24.env
```

Ожидаемый владелец и режим: `root cmr24 640`.

### 4. Создайте службу systemd

Откройте новый unit-файл:

```bash
sudoedit /etc/systemd/system/cmr24-mcp.service
```

Содержимое:

```ini
[Unit]
Description=CMR24 MCP Server
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=cmr24
Group=cmr24
WorkingDirectory=/opt/cmr24-mcp/source/mcp-cmr24
EnvironmentFile=/etc/cmr24-mcp/cmr24.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/cmr24-mcp/venv/bin/python start_server.py
Restart=on-failure
RestartSec=5s
TimeoutStopSec=30s
UMask=0027

# Базовое усиление изоляции процесса
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

[Install]
WantedBy=multi-user.target
```

Проверьте unit и запустите службу:

```bash
sudo systemd-analyze verify /etc/systemd/system/cmr24-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now cmr24-mcp
sudo systemctl status cmr24-mcp --no-pager
```

Если установленная версия `systemd` не знает одну из защитных директив, `systemd-analyze verify` укажет её. Удалите только неподдерживаемую директиву, а не весь блок усиления.

### 5. Проверьте локальный процесс

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/healthz
curl --fail --silent --show-error http://127.0.0.1:8000/readyz
sudo journalctl --unit cmr24-mcp --lines 50 --no-pager
```

До настройки Nginx сервер должен быть доступен только на `127.0.0.1:8000`. Убедитесь, что он не слушает публичный интерфейс:

```bash
sudo ss -lntp | grep ':8000'
```

После этого настройте HTTPS по разделу [«Настройка HTTPS через Nginx»](#настройка-https-через-nginx). Пример Nginx одинаково подходит для systemd- и Docker-развёртывания.

### 6. Обновление и откат Ubuntu-службы

Перед обновлением сохраните текущий тег:

```bash
sudo -u cmr24 git -C /opt/cmr24-mcp/source describe --tags --exact-match
```

Обновление выполняется только на опубликованный release-тег:

```bash
sudo systemctl stop cmr24-mcp
sudo -u cmr24 git -C /opt/cmr24-mcp/source fetch --tags origin
sudo -u cmr24 git -C /opt/cmr24-mcp/source checkout <НОВЫЙ_RELEASE_TAG>
sudo -u cmr24 /opt/cmr24-mcp/venv/bin/python -m pip install \
  --require-hashes \
  --requirement /opt/cmr24-mcp/source/mcp-cmr24/requirements.lock
sudo systemctl start cmr24-mcp
```

Проверьте `systemctl status`, `/healthz`, `/readyz` и безопасную операцию чтения. Если проверка не прошла, верните предыдущий тег:

```bash
sudo systemctl stop cmr24-mcp
sudo -u cmr24 git -C /opt/cmr24-mcp/source checkout v0.1.0
sudo -u cmr24 /opt/cmr24-mcp/venv/bin/python -m pip install \
  --require-hashes \
  --requirement /opt/cmr24-mcp/source/mcp-cmr24/requirements.lock
sudo systemctl start cmr24-mcp
```

Настройки в `/etc/cmr24-mcp/cmr24.env` при переключении Git-тега не затрагиваются.

## Production-деплой в Docker

### 1. Получите исходный код

Используйте отдельный каталог и зафиксированный release-тег:

```bash
sudo mkdir -p /opt/cmr24-mcp
sudo chown "$USER":"$USER" /opt/cmr24-mcp
git clone https://github.com/neurologicby/mcp-cmr24.git /opt/cmr24-mcp/source
cd /opt/cmr24-mcp/source
git checkout v0.1.0
```

Перед production-деплоем убедитесь, что checkout чистый:

```bash
git status --short
```

Команда не должна ничего вывести.

### 2. Создайте конфигурацию вне репозитория

Секреты нельзя хранить в каталоге `source`: Docker собирает образ из этого каталога. Создайте отдельный файл:

```bash
sudo install -d -m 700 /etc/cmr24-mcp
sudo install -m 600 /opt/cmr24-mcp/source/mcp-cmr24/.env.example /etc/cmr24-mcp/cmr24.env
sudo nano /etc/cmr24-mcp/cmr24.env
```

Минимальная конфигурация для домена `mcp.example.com`:

```dotenv
AUTH_MODE=per_request
CMR24_BASE_URL=https://cmr24.by/api
MCP_HOST=0.0.0.0
MCP_PORT=8000
MCP_ALLOWED_HOSTS=mcp.example.com
MCP_ALLOWED_ORIGINS=
MCP_DEFAULT_SCOPES=cargo.read
MCP_TRUST_SCOPE_HEADER=false
MCP_RATE_LIMIT_REQUESTS=120
MCP_RATE_LIMIT_WINDOW=60
```

Замените `mcp.example.com` своим доменом. Не заключайте значения в `<` и `>` — такие обозначения в инструкции означают место для подстановки.

Если клиент работает в браузере, перечислите разрешённые origin через запятую:

```dotenv
MCP_ALLOWED_ORIGINS=https://app.example.com
```

Wildcard `*` намеренно запрещён. Для обычного desktop MCP-клиента `MCP_ALLOWED_ORIGINS` оставляют пустым.

По умолчанию доступно только чтение (`cargo.read`). Расширяйте права осознанно:

```dotenv
MCP_DEFAULT_SCOPES=cargo.read,cargo.write
```

Для удаления дополнительно нужен постоянный случайный секрет:

```bash
openssl rand -hex 32
```

Сохраните результат только в защищённом env-файле:

```dotenv
MCP_DEFAULT_SCOPES=cargo.read,cargo.write,cargo.delete
CONFIRMATION_SECRET=<СЛУЧАЙНОЕ_ЗНАЧЕНИЕ_ИЗ_OPENSSL>
```

`CONFIRMATION_SECRET` не является ключом CMR24 и не должен передаваться MCP-клиенту.

### 3. Соберите образ

Создавайте образ **до** добавления каких-либо локальных секретов в checkout:

```bash
cd /opt/cmr24-mcp/source
docker build --pull -t cmr24-mcp:0.1.0 ./mcp-cmr24
```

Env-файл из `/etc/cmr24-mcp` находится вне build context и поэтому не попадёт в слои образа.

### 4. Запустите контейнер

Публикуйте приложение только на loopback-интерфейсе сервера:

```bash
docker run -d \
  --name cmr24-mcp \
  --restart unless-stopped \
  --env-file /etc/cmr24-mcp/cmr24.env \
  --publish 127.0.0.1:8000:8000 \
  cmr24-mcp:0.1.0
```

Проверьте контейнер:

```bash
docker ps --filter name=cmr24-mcp
curl --fail --silent --show-error http://127.0.0.1:8000/healthz
curl --fail --silent --show-error http://127.0.0.1:8000/readyz
```

Ожидаемые ответы:

```json
{"status":"ok"}
{"status":"ready","matcher":"ready"}
```

Если `/readyz` отвечает HTTP 503, проверьте значения `AUTH_MODE` и `CMR24_AUTHKEY`. В режиме `per_request` сервер готов без серверного ключа.

## Настройка HTTPS через Nginx

Пример `/etc/nginx/sites-available/cmr24-mcp`:

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.example.com;

    ssl_certificate     /etc/letsencrypt/live/mcp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.example.com/privkey.pem;

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}

server {
    listen 80;
    server_name mcp.example.com;
    return 301 https://$host$request_uri;
}
```

Активируйте конфигурацию:

```bash
sudo ln -s /etc/nginx/sites-available/cmr24-mcp /etc/nginx/sites-enabled/cmr24-mcp
sudo nginx -t
sudo systemctl reload nginx
```

Проверьте внешний endpoint:

```bash
curl --fail --silent --show-error https://mcp.example.com/healthz
curl --fail --silent --show-error https://mcp.example.com/readyz
```

На firewall откройте только `80/tcp` для выпуска/перенаправления сертификата и `443/tcp` для клиентов. Порт `8000` не должен быть доступен извне.

## Подключение MCP-клиента

### Рекомендуемый компактный endpoint

Добавьте HTTP MCP-сервер в конфигурацию клиента:

```json
{
  "mcpServers": {
    "cmr24": {
      "type": "http",
      "url": "https://mcp.example.com/mcp/single",
      "headers": {
        "Authorization": "Bearer ${CMR24_AUTHKEY}"
      }
    }
  }
}
```

Задайте `CMR24_AUTHKEY` через переменную окружения или встроенное защищённое хранилище MCP-клиента. Поддержка `${CMR24_AUTHKEY}` зависит от конкретного клиента; если интерполяции нет, используйте его штатный secret manager.

Не сохраняйте реальный ключ:

- в Git-репозитории;
- в публичном JSON-примере;
- в URL или query string;
- в скриншотах и логах;
- в истории команд общей машины.

Для локального сервера замените URL:

```json
"url": "http://127.0.0.1:8000/mcp/single"
```

### Нативный каталог инструментов

Если клиенту нужны отдельные типизированные tools, используйте:

```json
"url": "https://mcp.example.com/mcp/native"
```

Не подключайте оба endpoint одновременно без необходимости: они предоставляют одинаковые операции в разных представлениях.

## Проверка после подключения

1. Перезапустите MCP-клиент после изменения конфигурации.
2. Убедитесь, что сервер `cmr24` отображается как подключённый.
3. Для `/mcp/single` вызовите `list_available_tools`.
4. Выполните безопасную операцию чтения.
5. Проверьте, что неизвестный или отозванный ключ получает отказ CMR24.
6. Не начинайте smoke-тест с создания, удаления или восстановления заявки.

Метрики доступны после авторизации:

```bash
curl --fail \
  --header 'Authorization: Bearer <CMR24_AUTHKEY>' \
  https://mcp.example.com/metrics
```

Не вставляйте настоящий ключ в команду на общей машине: предпочтительно передавайте заголовок через защищённый механизм вашего агента мониторинга.

## Эксплуатация и обновление

### Журналы

```bash
docker logs --tail 100 cmr24-mcp
docker logs --follow cmr24-mcp
```

Приложение не должно записывать credentials в журнал. Перед передачей логов всё равно проверьте их на персональные данные и пользовательские payload.

### Перезапуск после изменения конфигурации

Переменные из `--env-file` читаются при создании контейнера. После изменения файла пересоздайте контейнер:

```bash
docker rm --force cmr24-mcp
docker run -d \
  --name cmr24-mcp \
  --restart unless-stopped \
  --env-file /etc/cmr24-mcp/cmr24.env \
  --publish 127.0.0.1:8000:8000 \
  cmr24-mcp:0.1.0
```

### Обновление

```bash
cd /opt/cmr24-mcp/source
git fetch --tags origin
git checkout <НОВЫЙ_RELEASE_TAG>
docker build --pull -t cmr24-mcp:<НОВАЯ_ВЕРСИЯ> ./mcp-cmr24
```

После успешной сборки пересоздайте контейнер с новым тегом и повторите проверки `/healthz`, `/readyz` и безопасную операцию чтения.

### Откат

Старый образ можно запустить с прежним тегом:

```bash
docker rm --force cmr24-mcp
docker run -d \
  --name cmr24-mcp \
  --restart unless-stopped \
  --env-file /etc/cmr24-mcp/cmr24.env \
  --publish 127.0.0.1:8000:8000 \
  cmr24-mcp:0.1.0
```

## Диагностика

| Симптом | Возможная причина | Что проверить |
|---|---|---|
| `/healthz` недоступен | Контейнер не запущен или порт занят | `docker ps`, `docker logs cmr24-mcp`, `ss -lntp` |
| `/readyz` возвращает 503 | Некорректная авторизация | `AUTH_MODE`; наличие `CMR24_AUTHKEY` для `service_account` |
| MCP-клиент получает 401 | Нет Bearer-заголовка | Настройки headers и secret manager клиента |
| CMR24 отклоняет авторизацию | Неверный или отозванный ключ | Проверить ключ непосредственно в CMR24, затем выполнить его ротацию |
| Ответ 403 в `service_account` | Источник не входит в доверенную сеть | `MCP_SERVICE_ACCOUNT_NETWORKS` и фактический IP reverse proxy |
| Ошибка Host или DNS rebinding | Публичный Host отсутствует в allowlist | `MCP_ALLOWED_HOSTS` и `proxy_set_header Host $host` |
| Browser-клиент блокируется CORS | Origin отсутствует в allowlist | Добавить точный origin в `MCP_ALLOWED_ORIGINS` |
| Ответ 429 | Сработал лимит запросов | `Retry-After`, `MCP_RATE_LIMIT_REQUESTS`, число клиентов |
| Удаление не запускается | Нет scope или секрета подтверждения | `cargo.delete`, `CONFIRMATION_SECRET`, двухшаговое подтверждение |
| Сессия обрывается через прокси | Малые proxy timeout или включён buffering | `proxy_buffering off`, `proxy_read_timeout`, `proxy_send_timeout` |

## Итоговый чек-лист

- [ ] Используется release-тег, а не случайное состояние ветки.
- [ ] `.env` находится вне Git-репозитория и Docker build context.
- [ ] Для удалённых клиентов выбран `AUTH_MODE=per_request`.
- [ ] Приложение слушает `0.0.0.0` только внутри контейнера.
- [ ] Docker публикует порт `8000` только на `127.0.0.1` хоста.
- [ ] Внешний доступ идёт исключительно через HTTPS.
- [ ] `MCP_ALLOWED_HOSTS` содержит точный production-домен.
- [ ] `MCP_ALLOWED_ORIGINS` пуст или содержит только необходимые origin.
- [ ] По умолчанию выдан только scope `cargo.read`.
- [ ] Для `cargo.delete` задан отдельный постоянный `CONFIRMATION_SECRET`.
- [ ] `/healthz` и `/readyz` возвращают HTTP 200.
- [ ] MCP-клиент видит каталог инструментов и выполняет безопасное чтение.
- [ ] Порт `8000` закрыт от внешней сети.
- [ ] Настроены сбор метрик, централизованные логи и ротация credentials.

> Текущие одноразовые подтверждения удаления хранятся в памяти процесса. До появления общего хранилища запускайте **один экземпляр** приложения; несколько реплик могут нарушить двухшаговое подтверждение.
