# Telegram AI Bot — Infrastructure

## 1. Architecture

```
Telegram → setWebhook → Hugging Face Space (FastAPI + Uvicorn) → OpenRouter (AI)
                                    ↓
                          Hugging Face Datasets (persistence)
```

**Key constraint:** Hugging Face Spaces blocks all outbound HTTPS traffic to `api.telegram.org`. Therefore:
- ❌ No outbound requests to Telegram API (sendMessage, getFile, sendChatAction)
- ✅ Can only respond to webhook — Telegram executes the API method from the response

The bot uses the **Webhook Response Pattern**: handler returns `dict` with `method` field.

---

## 2. Hosting

- **HF Space:** `ShnekAI/telegram-bot` — Docker SDK, port :7860, branch `main`
- **GitHub:** `Amahasqq/telegram-bot` — branch `main`, source code + Actions
- **Keep-alive:** `.github/workflows/keep-alive.yml` — pings `/health` every 10 min
- **GitHub Secret:** `SPACE_URL` = `ShnekAI-telegram-bot.hf.space`

---

## 3. Secrets (HF Space — 7 variables)

| Secret | Required | Description |
|--------|:-------:|-------------|
| `TELEGRAM_BOT_TOKEN` | yes | Token from @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | yes | Secret for X-Telegram-Bot-Api-Secret-Token |
| `OPENROUTER_API_KEY` | yes | OpenRouter API key |
| `HF_TOKEN` | yes | HF token with access to `ShnekAI/telegram-bot-data` |
| `DATASET_REPO` | yes | `ShnekAI/telegram-bot-data` |
| `SPACE_URL` | yes | `ShnekAI-telegram-bot.hf.space` (without https://) |
| `TAVILY_API_KEY` | no | Web search (1000 req/month) |
| `REDDIT_CLIENT_ID` | no | Reddit trends OAuth (script app) |
| `REDDIT_CLIENT_SECRET` | no | Reddit trends OAuth (script app) |

---

## 4. File Structure

```
telegram-bot/
├── app/                          # Application package (23 modules)
│   ├── main.py                   # FastAPI app, lifespan, webhook routes
│   ├── config.py                 # Pydantic Settings (SecretStr)
│   ├── constants.py              # All magic values, prompts, limits
│   ├── exceptions.py             # Custom exceptions (5 types)
│   ├── logging_config.py         # Structured JSON logging
│   ├── middleware/
│   │   └── auth.py               # hmac.compare_digest webhook verification
│   ├── handlers/
│   │   ├── commands.py           # /start, /clear, /notes, /clearnotes, /costs, /briefing
│   │   ├── messages.py           # handle_text (text only, no image/voice)
│   │   └── briefing.py           # generate_briefing with asyncio.gather
│   ├── services/
│   │   ├── memory.py             # MemoryManager (HF Datasets persistence)
│   │   ├── llm.py                # OpenRouter with 3-model fallback chain
│   │   ├── search.py             # Tavily search (API key in Authorization header)
│   │   ├── trends.py             # HN + Reddit (parallel fetch)
│   │   └── http_client.py        # Shared httpx.AsyncClient with connection pooling
│   ├── schemas/
│   │   └── telegram.py           # Pydantic: TelegramUpdate, Message, User, Chat
│   └── utils/
│       ├── telegram.py           # tg_resp() helper
│       └── helpers.py            # build_system_prompt, build_messages, truncate
├── prompts/
│   └── system.txt                # System prompt with {user_facts} placeholder
├── tests/
│   ├── conftest.py               # Fixtures (mock HF API, mock httpx)
│   ├── unit/
│   │   ├── test_memory.py        # MemoryManager: 17 tests
│   │   ├── test_llm.py           # OpenRouter + fact extraction: 9 tests
│   │   ├── test_rate_limit.py    # Rate limiting: 4 tests
│   │   └── test_commands.py      # Command handlers: 8 tests
│   └── integration/
│       └── test_webhook.py       # Webhook flow tests (requires FastAPI)
├── Dockerfile                    # Multi-stage, python:3.11-slim, non-root, healthcheck
├── requirements.txt              # 6 dependencies (fastapi, uvicorn, httpx, huggingface-hub, pydantic, pydantic-settings)
├── .env.example                  # Documented env vars
├── .dockerignore
├── .github/workflows/
│   └── keep-alive.yml            # Ping /health every 10 min
├── INFRASTRUCTURE.md             # This document
└── README.md                     # HF Space metadata
```

---

## 5. Data Flow

### Text (working)

```
Telegram → POST /webhook → verify_webhook (hmac.compare_digest)
  → webhook() → Pydantic validation → dedup update_id → rate limit (in-memory)
  → set_user_chat_id() → handle_text()
  → memory.get_history() + memory.get_user_facts()
  → build_system_prompt() → build_messages()
  → call_openrouter() (3-model fallback chain with retry)
  → memory.add_message() + log_costs()
  → asyncio.create_task(extract_facts) [background]
  → return {"method": "sendMessage", "chat_id": ..., "text": "..."}
```

### /briefing (working)

```
generate_briefing() → asyncio.gather(
    get_hackernews_trends(),      # HN Firebase API
    get_reddit_trends(),           # Parallel subreddit fetch
    search_web("AI tech news")    # Tavily (optional)
  ) → BRIEFING_PROMPT.format()
  → call_openrouter()
  → return tg_resp("sendMessage", ...)
```

### Image / Voice (REMOVED)

Image and voice handling was removed because HF Spaces blocks `api.telegram.org`, making `getFile` calls impossible.

---

## 6. MemoryManager — Persistence

- **Schema keys**: `conversations`, `user_facts`, `notes`, `costs`, `rate_limits`, `processed_updates`, `user_chat_ids`
- **File**: `bot_data.json` in HF Dataset `ShnekAI/telegram-bot-data`
- **Max history**: 20 messages per user (FIFO), truncated to 2000 chars each
- **Sync**: Every 30 seconds (background task), force-save on shutdown (with 10s timeout)
- **Graceful fallback**: Missing keys handled in code (no migration needed)
- **Users**: Multi-user via `user_chat_ids` mapping

### Dataset Creation
1. `huggingface.co → New Dataset → telegram-bot-data → Private`
2. `HF_TOKEN` from account `ShnekAI` with dataset access
3. File created automatically after first save (~30 sec)
4. Explicit init on 404: empty structure created on first load

---

## 7. External APIs

| API | Auth | Free limit | Status |
|-----|------|:----------:|--------|
| OpenRouter | `Authorization: Bearer` | $1 credit | ✅ Working |
| Tavily | `Authorization: Bearer` | 1000 req/month | ✅ Working |
| Hacker News Firebase API | None | unlimited | ✅ Working |
| Reddit (`oauth.reddit.com`) | OAuth app-only (client_credentials) | 100 QPM | ✅ Working (needs `REDDIT_CLIENT_ID/SECRET`) |

### Model Fallback Chain
```python
MODEL_CHAIN = [
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "qwen/qwen3-coder:free",
]
```
Each model is retried 3 times with exponential backoff (2^attempt sec).

---

## 8. Webhook Registration

Auto-registration in `lifespan` **may fail** (HF blocks `api.telegram.org`). Register **manually** via browser:

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://ShnekAI-telegram-bot.hf.space/webhook&secret_token=<SECRET>
```

Verify:
```
https://api.telegram.org/bot<TOKEN>/getWebhookInfo
```

Expected: `{"url": ".../webhook", "pending_update_count": 0, "last_error_date": 0}`

---

## 9. Rate Limiting

- **Per user**: 1.5 second cooldown between messages
- **Mechanism**: In-memory dict (`_user_last_msg`)
- **Persistent fallback**: `rate_limits` key in HF Dataset (30s batch sync)
- **On restart**: Rate limits reset (acceptable for private bot)

---

## 10. Error Handling

| Error | Behavior |
|-------|----------|
| OpenRouter timeout | 3 retries (2^attempt sec), switch to next model in chain, then user message |
| Dataset 404 | Start with empty data (graceful fallback) |
| Tavily/Reddit/HN failure | Return empty list (defensive) |
| Invalid secret_token | 403 Forbidden (hmac.compare_digest) |
| Any handler error | "An error occurred. Please try again later." |
| External API error | Specific message: "I'm having trouble connecting..." |

---

## 11. Commands

- `/start` — Welcome message
- `/clear` — Clear conversation history
- `/note <text>` — Save a note
- `/notes` — Last 10 notes
- `/clearnotes` — Delete all notes
- `/costs` — Token usage and estimated cost
- `/briefing` — AI/tech briefing (HN + Reddit + Tavily → OpenRouter)

### Technical URLs
- `https://ShnekAI-telegram-bot.hf.space/health` — Health check
- `https://ShnekAI-telegram-bot.hf.space/webhook` — POST webhook

---

## 12. Known Limitations

1. **Image/Voice removed** — HF blocks `api.telegram.org`
2. **`sendChatAction` not possible** — Same reason
3. **Dataset 404 on first run** — File created after ~30 sec after first activity (explicit init on 404)
4. **Keep-alive requires `SPACE_URL`** in GitHub Secrets
5. **Reddit may be empty** — HF IP under rate limit
6. **Max 20 messages** history per user
7. **OpenRouter free models may be unstable** — Possible `null` responses (handled as `""`)
8. **No outbound Telegram API** — Cannot send proactive messages

---

## 13. Deployment

```bash
git add -A
git commit -m "description"
git push origin main    # GitHub
git push hf main        # HF → auto-build
```

Logs: `huggingface.co/spaces/ShnekAI/telegram-bot → Logs`
Restart: button **⋮ → Restart Space**

---

## 14. Testing

```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov

# Run unit tests (38 tests)
pytest tests/unit -v

# Run with coverage
pytest tests/unit --cov=app --cov-report=term
```

---

## 15. Accounts

| Service | Login | Purpose |
|---------|-------|---------|
| GitHub | `Amahasqq` | Repository telegram-bot |
| Hugging Face | `ShnekAI` | Space + Dataset |
| Telegram | @BotFather | Bot token |
| OpenRouter | — | API key |
| Tavily | — | API key (optional) |
