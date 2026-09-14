# CMR24 MCP Server

Public release **0.1.0** of an MCP server for working with the CMR24 cargo API.

The server provides two MCP interfaces over one operation registry:

- `/mcp/single` — a compact catalogue for clients with limited context;
- `/mcp/native` — individual typed MCP tools.

The application source, deployment instructions, security notes and test
commands are in [`mcp-cmr24/README.md`](mcp-cmr24/README.md).

## Документация на русском языке

Полная пошаговая инструкция по развёртыванию и подключению сервиса:

### **[Открыть инструкцию по деплою CMR24 MCP Server →](DEPLOYMENT_RU.md)**

В инструкции описаны:

- классическая установка на Ubuntu без Docker: Python 3.13, virtualenv и `systemd`;
- production-деплой в Docker;
- настройка Nginx, HTTPS и сетевых ограничений;
- безопасное хранение API-ключей и других секретов;
- подключение MCP-клиентов к `/mcp/single` и `/mcp/native`;
- проверка работоспособности, метрики и журналы;
- обновление, откат и диагностика типовых проблем.

Для большинства публичных установок рекомендуется схема:

```text
MCP-клиент → HTTPS/Nginx → CMR24 MCP Server → CMR24 API
```

## Quick start

```powershell
cd mcp-cmr24
Copy-Item .env.example .env
python -m pip install --require-hashes -r requirements-windows.lock
python start_server.py
```

Keep `.env` private. The repository contains only `.env.example` with empty
credential fields.

## License

MIT — see [LICENSE](LICENSE).
