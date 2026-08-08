import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict

from ytm2jf.bot import process_request, poll_for_messages
from ytm2jf.config import env
from ytm2jf.logger import LOGGER
from ytm2jf.version import __version__

BOT_ENDPOINT = "/ytbot"


def two_factor(headers: Dict[str, str]) -> bool:
    """Verify that a request came from the configured Telegram webhook."""
    if env.bot_secret:
        return secrets.compare_digest(
            headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
            env.bot_secret,
        )

    LOGGER.warning("Use the env var bot_secret to secure the webhook interaction")
    return True


class TelegramWebhookHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for the Telegram webhook."""

    server_version = f"JarvisAPI/{__version__}"

    def do_POST(self):
        """POST endpoint to handle incoming messages."""
        if self.path != BOT_ENDPOINT:
            self.send_error(
                HTTPStatus.NOT_FOUND,
                HTTPStatus.NOT_FOUND.phrase,
            )
            return

        LOGGER.debug(
            "Connection received from %s via %s",
            self.client_address[0],
            self.headers.get("Host"),
        )

        # Read request body.
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(
                HTTPStatus.BAD_REQUEST,
                HTTPStatus.BAD_REQUEST.phrase,
            )
            return

        try:
            body = self.rfile.read(content_length)
            response = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            LOGGER.error(error)
            self.send_error(
                HTTPStatus.BAD_REQUEST,
                HTTPStatus.BAD_REQUEST.phrase,
            )
            return

        # Ensure only the owner who set the webhook can interact with the bot.
        if not two_factor(self.headers):
            LOGGER.error("Request received from a non-webhook source")
            LOGGER.error(response)

            self.send_error(
                HTTPStatus.FORBIDDEN,
                HTTPStatus.FORBIDDEN.phrase,
            )
            return

        payload = response.get("message")

        if payload:
            LOGGER.debug(response)
            process_request(payload)

            # Telegram only needs a successful HTTP response.
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            return

        self.send_error(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            HTTPStatus.UNPROCESSABLE_ENTITY.phrase,
        )

    def do_GET(self):
        """GET endpoint for incoming messages."""
        self.send_error(
            HTTPStatus.NOT_FOUND,
            HTTPStatus.NOT_FOUND.phrase,
        )

    def log_message(self, format, *args):
        """Send BaseHTTPRequestHandler logs through the existing logger."""
        LOGGER.info(
            "%s - %s",
            self.address_string(),
            format % args,
        )


def run():
    """Start the Jarvis API server."""
    if not env.bot_webhook:
        offset = 0
        while True:
            poll_for_messages(offset)
            offset += 1

    server = ThreadingHTTPServer(
        (env.host, env.port),
        TelegramWebhookHandler,
    )

    LOGGER.info("Hosting at http://%s:%s", env.host, env.port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        LOGGER.info("Shutting down API server.")
        server.shutdown()
        server.server_close()
