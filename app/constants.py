RATE_LIMIT = 1.5

MAX_HISTORY = 20
MAX_TEXT_LENGTH = 2000

HF_SYNC_INTERVAL = 30

TAVILY_MAX_RESULTS = 5
TAVILY_QUERY_LENGTH = 500

MAX_PROCESSED_UPDATES = 1000
PROCESSED_UPDATES_TTL_HOURS = 24

MODEL_CHAIN = [
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "qwen/qwen3-coder:free",
]
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT = 30
OPENROUTER_RETRIES = 3

SEARCH_KEYWORDS = {
    "news", "latest", "what", "search",
    "find", "google", "trending", "weather", "stock",
    "price", "how", "who", "what's",
}

TAVILY_TIMEOUT = 10
TAVILY_MIN_REMAINING = 50

HN_TIMEOUT = 15
REDDIT_TIMEOUT = 10
REDDIT_POSTS_PER_SUB = 5
HN_TOP_STORIES = 15
HN_RESULTS = 10
REDDIT_SUBREDDITS = ["artificial", "MachineLearning", "singularity", "technology"]

WEBHOOK_TIMEOUT = 30
