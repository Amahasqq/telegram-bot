# Обзор проекта: Telegram AI-бот (ShnekAI)

> Полный контекст: что это, как работает, из чего состоит, как запускать и деплоить.
> Для узкой технической инфраструктуры см. INFRASTRUCTURE.md.

## 1. Что это такое

Telegram-бот с искусственным интеллектом, задеплоенный на **Hugging Face Space**
(контейнер Docker, FastAPI + Uvicorn, порт 7860). Бот ведёт диалог, ищет веб через
Tavily, запоминает факты о пользователе и генерирует ежедневный технобрифинг.

Ядро интеллекта — **OpenRouter** (шлюз к LLM). Модель в цепочке одна:
`openrouter/free` (авто-роутер по доступным бесплатным моделям).

## 2. Главное архитектурное ограничение

HF Spaces **блокирует исходящий HTTPS на `api.telegram.org`**. Поэтому бот не может
сам вызывать Telegram API (sendMessage, getFile, sendChatAction).

Решение — **Webhook Response Pattern**: обработчик возвращает `dict` с полем `method`,
и Telegram сам выполняет этот метод из тела HTTP-ответа на вебхук.

Следствия (всегда в голове):
- Одно сообщение на один апдейт. Второго ответа не будет.
- Нет ретрая на стороне бота. Любая необработанная ошибка ДО `return` → Telegram
  получает не-2xx и (при уже заклеймленном апдейте) молчит — поэтому персистентные
  вызовы и обработчики защищены try/except.
- Картинки и голос убраны (getFile невозможен).
- Бот **не выставляет `parse_mode`** ни в одном ответе, поэтому битый Markdown
  тишину НЕ вызывает. Голые URL в `/briefing` — защита на будущее (если `parse_mode`
  когда-либо добавят), а не текущая проблема.

## 3. Поток обработки текста

```
Telegram → POST /webhook
  → verify_webhook_secret (hmac.compare_digest, ASGI-middleware)
  → webhook(): Pydantic-валидация → claim_update (идемпотентность под локом)
  → извлечение chat_id/user_id → приватный фильтр ALLOWED_USER_ID
  → rate-limit (in-memory) → set_user_chat_id
  → handle_command (если entities[0].type == bot_command) ИЛИ handle_text
  → handle_text: history + facts → build_messages → call_openrouter (openrouter/free)
  → memory.add_message → фоновый extract_facts (если длина >= FACT_MIN_LEN)
  → return {"method": "sendMessage", "chat_id": ..., "text": ...}
```

## 4. Компоненты (telegram-bot/app/)

- `main.py` — FastAPI, lifespan, `/webhook`, `/health`, rate-limit, auth-middleware
- `config.py` — Pydantic Settings (SecretStr); поле `allowed_user_id`
- `constants.py` — все магические значения: `MODEL_CHAIN`, `SEARCH_KEYWORDS`,
  `TELEGRAM_MAX_MSG=4096`, `FACT_MIN_LEN=20`, лимиты, источники брифинга
- `exceptions.py` — `AppError`, `ExternalAPIError` (2 типа)
- `logging_config.py` — JSON-логи
- `middleware/auth.py` — `hmac.compare_digest` секрета вебхука
- `handlers/`
  - `commands.py` — `/start /clear /note /notes /clearnotes /briefing`
  - `messages.py` — `handle_text` — главный путь, `extract_facts` в фоне
  - `briefing.py` — `generate_briefing` — `asyncio.gather` по 6 источникам
- `services/`
  - `memory.py` — `MemoryManager` (HF Dataset), sync, локи
  - `llm.py` — `call_openrouter` (ретраи), `extract_facts`
  - `search.py` — Tavily
  - `trends.py` — HN / Reddit / HF Papers / Lobsters / GitHub
  - `http_client.py` — общий `httpx.AsyncClient` (пул соединений)
- `schemas/telegram.py` — Pydantic-модели апдейта
- `utils/`
  - `telegram.py` — `tg_resp()`
  - `helpers.py` — `build_system_prompt`, `build_messages`, `truncate`

## 5. Персистентность (MemoryManager)

Состояние в **HF Dataset** `bot_data.json` (JSON). Ключи:
`conversations`, `notes`, `user_facts`, `processed_updates`, `user_chat_ids`.
Фоновый sync каждые 30 c; force-save на shutdown (таймаут 10 c).
Дисциплина локов: снимок под локом, медленная выгрузка вне лока.

## 6. Внешние API

| API | Auth | Статус |
|-----|------|--------|
| OpenRouter | Bearer | ✅ основной LLM (`openrouter/free`) |
| Tavily | Bearer | ✅ веб-поиск (опционально) |
| Hacker News Firebase | нет | ✅ |
| Reddit (`oauth.reddit.com`) | client_credentials | ✅ (нужны `client_id`/`secret`) |
| HF Daily Papers / Lobsters / GitHub | нет | ✅ источники `/briefing` |

## 7. Команды

`/start` — приветствие · `/clear` — очистить историю · `/note <текст>` — сохранить заметку
`/notes` — последние 10 · `/clearnotes` — удалить все · `/briefing` — ИИ/тех брифинг

(команда `/costs` удалена: модели `:free`, суммы фиктивны)

## 8. Поведенческие особенности

- **Поиск:** `SEARCH_KEYWORDS` (RU+EN); релевантный текст → Tavily (если задан ключ).
- **Обрезка:** все ответы ≤ `TELEGRAM_MAX_MSG` (4096); `/briefing` с префиксом
  «📡 …» и `disable_web_page_preview`, ссылки — голые URL (бот не использует
  `parse_mode`, поэтому битый Markdown тишину не вызывает; голые URL — защита на будущее).
- **Факты:** `extract_facts` только при `len(text) >= FACT_MIN_LEN` (экономия квоты).
- **Приватный режим:** `ALLOWED_USER_ID` задан → чужим отвечает «Access denied.» на
  каждое сообщение, `handle_text` не вызывается.

## 9. Запуск и проверки (venv-only)

venv: `/x/New-Projects/AIAgent/.venv` (Python 3.11 в проде, 3.14 локально).
Из `telegram-bot/`:

```bash
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/ruff.exe check app tests
../.venv/Scripts/mypy.exe app
```

Базовое состояние: 55 passed, ruff/mypy чисты.

## 10. Пример .env

Копия `.env.example`. Обязательные поля — без них бот не стартует; опциональные
включают доп. функции. `ALLOWED_USER_ID` пустой = бот открыт для всех.

```dotenv
# Required
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_WEBHOOK_SECRET=your_random_secret_here
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxx
HF_TOKEN=hf_xxxxxxxxxxxx
DATASET_REPO=ShnekAI/telegram-bot-data
SPACE_URL=ShnekAI-telegram-bot.hf.space

# Optional: веб-поиск через Tavily (1000 req/month)
TAVILY_API_KEY=tvly-xxxxxxxxxxxx

# Optional: Reddit-тренды в /briefing (script-приложение на reddit.com/prefs/apps;
# анонимный доступ заблокирован с 403, нужны client_id/secret)
REDDIT_CLIENT_ID=xxxxxxxxxxxx
REDDIT_CLIENT_SECRET=xxxxxxxxxxxx

# Optional: ограничить бота одним пользователем Telegram.
# Пусто = открыт для всех. Чужим отвечает «Access denied.».
ALLOWED_USER_ID=123456789
```

## 11. Деплой

Репозиторий — внутри `telegram-bot/`. GitHub: `Amahasqq/telegram-bot` (ветка `master`).
HF Space: `ShnekAI/telegram-bot` (авто-билд из ветки `main`).

```bash
git push origin HEAD:master   # GitHub
git push hf main              # HF → авто-деплой на прод
```

Remote `hf` трогать только по явной просьбе (это прод).

## 12. Известные ограничения

- Образ/голос убраны (HF блокирует `api.telegram.org`).
- Нет исходящего Telegram API → нельзя слать проактивные сообщения.
- `openrouter/free` — единственная модель; при исчерпании квоты пользователь
  получает «I'm having trouble connecting…» (резерва нет).
- Rate-limit in-memory, сбрасывается при рестарте.
- Webhook ставится вручную через браузер (HF блокирует `setWebhook` из кода).
