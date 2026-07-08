RATE_LIMIT = 1.5

MAX_HISTORY = 20
MAX_TEXT_LENGTH = 2000

HF_SYNC_INTERVAL = 30

TAVILY_MAX_RESULTS = 5
TAVILY_QUERY_LENGTH = 500

MAX_PROCESSED_UPDATES = 1000
PROCESSED_UPDATES_TTL_HOURS = 24

TELEGRAM_MAX_MSG = 4096
FACT_MIN_LEN = 20
USER_FACTS_MAX = 50

AI_TEMP_UNAVAILABLE = (
    "AI is temporarily unavailable - the free model quota may be exhausted. "
    "Please try again in a minute."
)

MODEL_CHAIN = [
    "openrouter/free",
]
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT = 30
OPENROUTER_RETRIES = 3

SEARCH_KEYWORDS = {
    "news", "latest", "what", "search",
    "find", "google", "trending", "weather", "stock",
    "price", "how", "who", "what's",
    "новости", "последние", "что", "поиск", "найди", "гугл",
    "тренды", "погода", "курс", "цена", "как", "кто",
    "сколько", "где", "когда",
}

TAVILY_TIMEOUT = 10
TAVILY_MIN_REMAINING = 50

HN_TIMEOUT = 15
REDDIT_TIMEOUT = 10
REDDIT_POSTS_PER_SUB = 5
REDDIT_RESULTS = 10

# Reddit OAuth (app-only / client_credentials). Reddit blocks anonymous .json
# access (403), so a registered app's client id/secret are required.
REDDIT_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_BASE = "https://oauth.reddit.com"
REDDIT_USER_AGENT = "python:telegram-ai-bot:1.0 (by /u/telegram-ai-bot)"
REDDIT_TOKEN_TTL = 3000  # refresh a bit before Reddit's ~3600s expiry
HN_TOP_STORIES = 15
HN_RESULTS = 10
REDDIT_SUBREDDITS = [
    "artificial",
    "MachineLearning",
    "LocalLLaMA",
    "OpenAI",
    "singularity",
    "technology",
]

# Hugging Face Daily Papers — curated trending AI research
HF_PAPERS_URL = "https://huggingface.co/api/daily_papers"
HF_PAPERS_TIMEOUT = 15
HF_PAPERS_RESULTS = 8

# Lobsters — HN-like tech community (AI tag)
LOBSTERS_URL = "https://lobste.rs/t/ai.json"
LOBSTERS_TIMEOUT = 10
LOBSTERS_RESULTS = 8

# GitHub trending — recently created, most-starred repos on a topic
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_TIMEOUT = 10
GITHUB_RESULTS = 6
GITHUB_TOPIC = "llm"
GITHUB_TREND_DAYS = 30
GITHUB_USER_AGENT = "telegram-ai-bot"

# Briefing: how many items of each source to feed the model, and how many
# characters of each serialized source block to keep in the prompt.
BRIEFING_NEWS_COUNT = 5
BRIEFING_HN_CHARS = 2000
BRIEFING_REDDIT_CHARS = 1500
BRIEFING_NEWS_CHARS = 1500
BRIEFING_LOBSTERS_CHARS = 1500
BRIEFING_PAPERS_CHARS = 1500
BRIEFING_GITHUB_CHARS = 1500

WEBHOOK_TIMEOUT = 30
