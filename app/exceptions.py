class AppError(Exception):
    """Base application error."""


class ExternalAPIError(AppError):
    """External API error (OpenRouter, Tavily, etc.)."""
