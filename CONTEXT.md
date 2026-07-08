# CONTEXT.md — внутренний справочник агента (ShnekAI Telegram-бот)

> Живой контекст для будущих сессий. Актуальное состояние после всех правок и
> аудитов. HANDOFF.md (корень) описывает исходный план — он выполнен; этот файл
> описывает ТЕКУЩЕЕ состояние. При расхождении с кодом — верь коду, обнови этот файл.

## 0. Что это / TL;DR

Telegram AI-бот, задеплоенный на Hugging Face Space (Docker, FastAPI + Uvicorn,
порт 7860). Диалог через OpenRouter (`openrouter/free`), веб-поиск Tavily,
ежедневный брифинг (HN/Reddit/HF Papers/Lobsters/GitHub), запоминание фактов.
Приватный режим включён (`ALLOWED_USER_ID` задан в секретах Space).

### Железные правила (нарушать нельзя)
1. Все команды — ТОЛЬКО в venv `/x/New-Projects/AIAgent/.venv`. Никаких глобальных python/pip.
2. Git-репозиторий — только `telegram-bot/`. Корень `/x/New-Projects/AIAgent/` — НЕ репозиторий.
3. Пуш — только на GitHub, ветка `master` (`git push origin HEAD:master`).
4. Remote `hf` — СВЯЩЕННЫЙ. Это авто-деплой на прод (HF Space). Пушить ТОЛЬКО по
   явной просьбе пользователя. Локальная ветка `main` трекает `origin/master`.
5. Коммит/пуш — только когда пользователь попросит или задача завершена и проверена.
6. После изменений: `pytest` + `ruff` + `mypy` должны быть зелёными.
7. Правки — хирургические, только то, что нужно по задаче.
8. Сообщение коммита заканчивать: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## 1. Архитектурное ограничение (критично)

HF Spaces блокирует исходящий HTTPS на `api.telegram.org`. Бот НЕ может сам звать
Telegram API. Используется **Webhook Response Pattern**: обработчик возвращает
`dict` с полем `method`, Telegram сам выполняет метод из тела HTTP-ответа.

Следствия:
- Одно сообщение на один апдейт. Второго ответа не будет.
- Нет ретрая на стороне бота. Ошибка ДО `return` → Telegram получает не-2xx и
  (для уже заклеймённого апдейта) молчит. Поэтому персистентные вызовы и обработчики
  защищены try/except.
- Картинки/голос убраны (getFile невозможен) — не предлагать.
- Бот НЕ выставляет `parse_mode` ни в одном ответе, КРОМЕ `/briefing` → битый
  Markdown в обычном ответе тишину НЕ вызывает. В `/briefing` `parse_mode="Markdown"`
  используется только для блока «Источники», а ссылки `[title](url)` строятся в
  коде из структурированных данных (не LLM), поэтому разметка всегда валидна.

## 2. Поток обработки

```
Telegram → POST /webhook → verify_webhook_secret (hmac.compare_digest, ASGI-middleware)
  → webhook(): Pydantic-валидация → claim_update (идемпотентность под локом)
  → chat_id/user_id → приватный фильтр ALLOWED_USER_ID
  → rate-limit (in-memory) → set_user_chat_id
  → handle_command (если entities[0].type == bot_command) ИЛИ handle_text
  → handle_text: history + facts → build_messages → call_openrouter (openrouter/free)
  → memory.add_message → фоновый extract_facts (len(text) >= FACT_MIN_LEN)
  → return {"method": "sendMessage", "chat_id": ..., "text": ...}
```

Точки тишины (возврат `{}`, пользователь ничего не получает): валидация Pydantic
(строка в main.py), не-message апдейт, отсутствие chat_id/user_id. Приватный режим
теперь НЕ молчит — отвечает «Access denied.» (см. §5).

## 3. Компоненты (telegram-bot/app/)

- main.py — FastAPI, lifespan, /webhook, /health, rate-limit, auth-middleware, приватный фильтр
- config.py — Pydantic Settings (SecretStr); поле `allowed_user_id: int | None = None`
- constants.py — все магические значения (MODEL_CHAIN, SEARCH_KEYWORDS, лимиты, тексты)
- exceptions.py — AppError, ExternalAPIError
- logging_config.py — JSON-логи в stdout (без утечки секретов)
- middleware/auth.py — hmac.compare_digest секрета вебхука (корректно)
- handlers/
  - commands.py — /start /clear /note /notes /clearnotes /briefing
  - messages.py — handle_text (главный путь), extract_facts в фоне
  - briefing.py — generate_briefing (asyncio.gather по 6 источникам)
- services/
  - memory.py — MemoryManager (HF Dataset), sync, локи, кап facts
  - llm.py — call_openrouter (ретраи по цепочке), extract_facts
  - search.py — Tavily
  - trends.py — HN / Reddit (OAuth) / HF Papers / Lobsters / GitHub
  - http_client.py — общий httpx.AsyncClient
- schemas/telegram.py — Pydantic-модели апдейта
- utils/
  - telegram.py — tg_resp()
  - helpers.py — build_system_prompt, build_messages, truncate

## 4. Конфигурация (секреты HF Space)

| Переменная | Обязательность | Назначение |
|------------|:--------------:|-----------|
| TELEGRAM_BOT_TOKEN | да | токен @BotFather |
| TELEGRAM_WEBHOOK_SECRET | да | X-Telegram-Bot-Api-Secret-Token |
| OPENROUTER_API_KEY | да | OpenRouter |
| HF_TOKEN | да | HF token к датасету |
| DATASET_REPO | да | ShnekAI/telegram-bot-data |
| SPACE_URL | да | ShnekAI-telegram-bot.hf.space |
| TAVILY_API_KEY | нет | веб-поиск |
| REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET | нет | Reddit-тренды в /briefing |
| ALLOWED_USER_ID | настроен | приватный режим (задан — бот отвечает «Access denied.» чужим) |

Локальный `.env` НЕ коммитится (.gitignore). Для тестов conftest.py подкидывает
env-заглушки и мокает huggingface_hub.

## 5. Поведение (что уже работает)

- **Поиск**: SEARCH_KEYWORDS (RU+EN, регэксп-токенизация). Релевантный текст → Tavily
  (если TAVILY_API_KEY задан), результат попадает в промпт как `[Search results:]`.
- **Обрезка**: все ответы ≤ TELEGRAM_MAX_MSG (4096): messages, briefing (с учётом
  префикса «📡 Ежедневный брифинг\n\n»), /notes.
- **/briefing**: 6 источников (HN, Reddit, HF Papers, Lobsters, GitHub, Tavily —
  опционален), дедуп по заголовкам между источниками, кэш сырых источников
  (`BRIEFING_CACHE_TTL`), отдельная модель (`settings.briefing_model`), блок
  «Источники» с **кликабельными заголовками** `[title](url)`, собранными в коде
  (не LLM) → `parse_mode="Markdown"`, `disable_web_page_preview=True`. Пустой
  результат (нет источников) → `BRIEFING_EMPTY_MSG` без вызова LLM.
- **Факты**: extract_facts в фоне, только если len(text) >= FACT_MIN_LEN (20);
  capped USER_FACTS_MAX (50) в MemoryManager.
- **Ошибки OpenRouter**: одна модель `openrouter/free`; при ExternalAPIError
  возвращается AI_TEMP_UNAVAILABLE («AI is temporarily unavailable …»), а не тишина.
- **Приватный режим**: ALLOWED_USER_ID задан → чужим `sendMessage` «Access denied.»
  на каждое сообщение; handle_text/handle_command не вызываются. Пустой = бот открыт.

## 6. Команды

/start · /clear · /note <текст> · /notes · /clearnotes · /briefing
(/costs удалён: модели :free, суммы фиктивны.)

## 7. Персистентность (MemoryManager)

HF Dataset `bot_data.json`. Ключи: conversations, notes, user_facts,
processed_updates, user_chat_ids. Фоновый sync каждые 30с; force-save на shutdown
(таймаут 30с, best-effort, логирует неполную выгрузку). Дисциплина локов: снимок под
локом, медленная выгрузка вне лока. get_user_history/get_user_facts возвращают копии.

## 8. Тесты и качество (venv-only)

venv: /x/New-Projects/AIAgent/.venv (Python 3.11 в проде, 3.14 локально).
Из telegram-bot/:
```
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/ruff.exe check app tests
../.venv/Scripts/mypy.exe app
```
Базовое состояние: **58 passed**, ruff/mypy чисты.
Структура: tests/unit/ (test_memory, test_llm, test_rate_limit, test_commands,
test_messages, test_trends) + tests/integration/ (test_webhook). conftest мокает HF,
реальный ASGI. Покрыты: handle_text (база/поиск/обрезка/гейт фактов/ExternalAPIError/
search-in-prompt/no-parse_mode), приватный режим, retry OpenRouter.

## 9. Деплой

GitHub: Amahasqq/telegram-bot (ветка master). HF Space: ShnekAI/telegram-bot
(авто-билд из ветки main).
```
git push origin HEAD:master   # GitHub
git push hf main              # HF прод (ТОЛЬКО по явной просьбе)
```
Webhook ставится вручную через браузер (HF блокирует setWebhook из кода; регистрация
в lifespan логируется как non-critical failure).

## 10. Применённые правки (история сессий)

- План из HANDOFF.md (A1–A8, B1–B3, C1–C4): RU-поиск, обрезка 4096, голые URL в
  briefing, try/except для handle_command, гейт фактов, openrouter/free (потом
  оставлена одна модель), удалён /costs, приватный режим, тесты, документация.
- Аудит-хардинг (commit 8508671): F-B защита claim_update/set_user_chat_id;
  F-C таймаут 30с + лог неполной выгрузки; F-D кап user_facts; F-A информативный
  AI_TEMP_UNAVAILABLE; F-F Reddit-токен без логирования e; F-M копии из getters;
  F-J тесты ExternalAPIError/search-in-prompt; F-H parse_mode-регрессия + правка
  OVERVIEW.md. (F-K дедуп хелпера — отложен, перекрывается F-A.)
- Приватный ответ (commit bf06758): чужим «Access denied.» вместо молчания.
- Создан OVERVIEW.md (описание проекта для людей).

## 11. Что НЕ делать

- Не менять стек (FastAPI / HF Dataset / OpenRouter остаются).
- Не предлагать картинки/голос (ограничение HF Spaces, не баг).
- Не трогать remote `hf` без явной просьбы.
- Не добавлять parse_mode без регрессионного теста (см. §1).
- Не делать преждевременный рефакторинг рабочего кода «заодно».
- ALLOWED_USER_ID уже настроен — приватный режим активен; не выключать без просьбы.

## 12. Быстрый старт локально

```
cd /x/New-Projects/AIAgent
./.venv/Scripts/python.exe -m pytest -q        # из telegram-bot/
./.venv/Scripts/ruff.exe check app tests
./.venv/Scripts/mypy.exe app
```
Для прод-логики нужен реальный .env с секретами (копия из HF Space settings).
