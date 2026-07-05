# Telegram AI Bot — Инфраструктура

## 1. Общая архитектура

```
Telegram → setWebhook → Hugging Face Space (FastAPI + Uvicorn) → OpenRouter (AI)
                                    ↓
                          Hugging Face Datasets (персистентность)
```

**Ключевое ограничение:** Hugging Face Spaces блокирует весь HTTPS-трафик к `api.telegram.org`. Поэтому:
- ❌ Нельзя outbound-запросы к Telegram API (sendMessage, getFile, sendChatAction)
- ✅ Можно отвечать на вебхук — Telegram сам выполняет API-метод из ответа

Весь бот построен на **webhook response pattern**: handler возвращает `dict` с полем `method`.

---

## 2. Хостинг

- **HF Space:** `ShnekAI/telegram-bot` — Docker SDK, порт :7860, ветка `main`
- **GitHub:** `Amahasqq/telegram-bot` — ветка `main`, исходный код + Actions
- **Keep-alive:** `.github/workflows/keep-alive.yml` — каждые 10 мин пингует `/health`
- **GitHub Secret для keep-alive:** `SPACE_URL` = `ShnekAI-telegram-bot.hf.space`

---

## 3. Secrets (HF Space — 10 переменных)

| Secret | Обязательный | Что это |
|--------|:---:|---------|
| `TELEGRAM_BOT_TOKEN` | да | Токен от @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | да | Секрет для X-Telegram-Bot-Api-Secret-Token |
| `OPENROUTER_API_KEY` | да | Ключ OpenRouter |
| `ALLOWED_USER_ID` | да | Telegram User ID (целое число) |
| `HF_TOKEN` | да | HF токен с доступом к `ShnekAI/telegram-bot-data` |
| `DATASET_REPO` | да | `ShnekAI/telegram-bot-data` |
| `SPACE_URL` | да | `ShnekAI-telegram-bot.hf.space` (без https://) |
| `GOOGLE_GENAI_API_KEY` | нет | Gemini STT — не работает (блокировка HF) |
| `TAVILY_API_KEY` | нет | Поиск в интернете |
| `X_BEARER_TOKEN` | нет | Не используется (заменён на Hacker News) |

---

## 4. Файловая структура

```
telegram-bot/
├── bot.py                    # FastAPI + вебхук + все хендлеры (401 строка)
├── services.py               # MemoryManager, OpenRouter, Gemini, поиск (314 строк)
├── config.py                 # Pydantic Settings — чтение env
├── Dockerfile                # python:3.11-slim, uvicorn :7860
├── requirements.txt          # fastapi, uvicorn, httpx, huggingface-hub, google-genai, pydantic
├── .dockerignore
├── prompts/
│   └── system.txt            # Системный промпт с {user_facts}
├── .github/workflows/
│   └── keep-alive.yml        # Пинг /health каждые 10 мин
├── README.md                 # Метаданные HF Space
└── INFRASTRUCTURE.md         # Этот документ
```

---

## 5. Потоки данных

### Текст (работает)

```
Telegram → POST /webhook → verify_webhook (secret_token)
  → webhook() → dedup update_id → allowed_user_id → rate limit
  → handle_text() → memory.get_history()
  → build_system_prompt() → call_openrouter()
  → memory.add_message() + extract_facts (фон)
  → return {"method": "sendMessage", "chat_id": ..., "text": "..."}
```

### /briefing (работает)

```
generate_briefing() → get_hackernews_trends()
                    → get_reddit_trends()
                    → search_web("главная новость AI tech") [Tavily]
                    → BRIEFING_PROMPT.format()
                    → call_openrouter()
                    → return tg_resp("sendMessage", ...)
```

### Изображение / Голос (НЕ РАБОТАЮТ)

```
handle_image() → GET api.telegram.org/bot<TOKEN>/getFile
              → ❌ ConnectTimeout (HF блокирует домен)
```

---

## 6. MemoryManager — персистентность

- Хранит: `conversations`, `user_facts`, `notes`, `costs`
- Файл: `bot_data.json` в HF Dataset `ShnekAI/telegram-bot-data`
- Максимум 20 сообщений на пользователя (FIFO), обрезаются до 2000 символов
- Синхронизация: каждые 30 секунд (фоновый цикл), force-save при остановке
- **Лимит:** только один пользователь (`ALLOWED_USER_ID`)

**Создание Dataset:**
1. `huggingface.co → New Dataset → telegram-bot-data → Private`
2. `HF_TOKEN` от аккаунта `ShnekAI` с доступом к датасету
3. Файл создаётся автоматически после первого save (~30 сек)

---

## 7. Внешние API

| API | Без ключа? | Бесплатный лимит |
|-----|:----------:|:----------------:|
| OpenRouter (`openrouter/free`) | нет | $1 кредит |
| Tavily (`api.tavily.com/search`) | нет | 1000 запросов/мес |
| Hacker News Firebase API | да | безлимит |
| Reddit (`/r/{sub}/hot.json`) | да | rate limit |
| Gemini STT (google-genai SDK) | нет | 60 RPM — **не работает** |

---

## 8. Регистрация вебхука

Автоматическая **падает** (блокировка HF). Регистрировать **вручную** через браузер:

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://ShnekAI-telegram-bot.hf.space/webhook&secret_token=<SECRET>
```

Проверка:
```
https://api.telegram.org/bot<TOKEN>/getWebhookInfo
```

Ожидаемый ответ: `{"url": ".../webhook", "pending_update_count": 0}`

---

## 9. Rate limit

1.5 секунды между сообщениями одного пользователя. Превышение — "Шнеку нужно передохнуть."

---

## 10. Обработка ошибок

| Ошибка | Поведение |
|--------|-----------|
| OpenRouter таймаут | 3 retry (2^attempt sec), затем "Бот временно недоступен" |
| Dataset 404 | Старт с пустыми данными (некритично) |
| Tavily/Reddit/HN падение | Возврат пустого списка |
| Неверный secret_token | 403 Forbidden |
| Чужой user_id | "Доступ закрыт." |
| Любая ошибка в handler | "Произошла ошибка. Попробуй позже." |

---

## 11. Команды

- `/start` — приветствие
- `/clear` — очистить историю
- `/notes` — последние 10 заметок
- `/clearnotes` — удалить заметки
- `/costs` — статистика токенов
- `/briefing` — брифинг (HN + Reddit + Tavily → OpenRouter)

### Технические URL

- `https://ShnekAI-telegram-bot.hf.space/health` — health check
- `https://ShnekAI-telegram-bot.hf.space/webhook` — POST вебхук

---

## 12. Известные ограничения

1. **Изображения и голос не работают** — HF блокирует `api.telegram.org` (ConnectTimeout)
2. **`sendChatAction` не работает** — та же причина
3. **Dataset 404 при первом запуске** — файл создаётся через ~30 сек после первой активности
4. **Keep-alive требует `SPACE_URL`** в GitHub Secrets
5. **Reddit может быть пуст** — HF IP под rate limit
6. **Максимум 20 сообщений** истории
7. **OpenRouter free model нестабильна** — возможны `null`-ответы (обрабатывается как `""`)

---

## 13. Деплой

```bash
git add -A
git commit -m "описание"
git push origin main    # GitHub
git push hf main        # HF → автобилд
```

Логи: `huggingface.co/spaces/ShnekAI/telegram-bot → Logs`
Рестарт: кнопка **⋮ → Restart Space**

---

## 14. Аккаунты

| Сервис | Логин | Что там |
|--------|-------|---------|
| GitHub | `Amahasqq` | Репозиторий telegram-bot |
| Hugging Face | `ShnekAI` | Space + Dataset |
| Telegram | @BotFather | Токен бота |
| OpenRouter | — | API-ключ |
| Tavily | — | API-ключ |
| Google AI | — | API-ключ (не используется) |
