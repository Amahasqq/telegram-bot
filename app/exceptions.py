class AppError(Exception):
    """Base application error."""


class AuthenticationError(AppError):
    """Webhook authentication failed."""


class RateLimitedError(AppError):
    """User exceeded rate limit."""


class ExternalAPIError(AppError):
    """External API error."""


class ValidationError(AppError):
    """Input validation error."""
