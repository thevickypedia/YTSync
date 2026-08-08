import requests

EgressErrors = (
    ConnectionError,
    TimeoutError,
    requests.RequestException,
    requests.Timeout,
)


class BotError(Exception):
    """Custom base exception for Telegram Bot.

    >>> BotError

    """


class BotWebhookConflict(BotError):
    """Error for conflict with webhook and getUpdates API call.

    >>> BotWebhookConflict

    """


class BotInUse(BotError):
    """Error indicate bot token is being used else where.

    >>> BotInUse

    """


class BotTokenInvalid(BotError):
    """Error indicate bot token is invalid.

    >>> BotTokenInvalid

    """
