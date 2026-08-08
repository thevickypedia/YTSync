import json
import secrets
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict

import requests.exceptions

from ytm2jf.bot import poll_for_messages, process_request
from ytm2jf.config import env
from ytm2jf.exceptions import (
    BotInUse,
    BotTokenInvalid,
    BotWebhookConflict,
    EgressErrors,
)
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
        failed_connections = 0
        LOGGER.info("Polling for incoming messages...")
        while True:
            try:
                time.sleep(env.poll_interval)
                if offset_id := poll_for_messages(offset):
                    offset = offset_id
            except BotWebhookConflict as error:
                # At this point, its be safe to remove the dead webhook
                LOGGER.error(error)
            except BotInUse as error:
                LOGGER.error(error)
            except BotTokenInvalid as error:
                LOGGER.error("ATTENTION: %s", error)
            except EgressErrors as error:
                # ReadTimeout is just saying that there were no messages to read within the time specified
                if isinstance(error, requests.exceptions.ReadTimeout):
                    continue
                LOGGER.error(error)
                failed_connections += 1
                if failed_connections > env.max_retries:
                    LOGGER.critical(
                        "ATTENTION::Couldn't recover from connection error. Restarting current process."
                    )
                    delay = failed_connections * env.backoff_factor
                    LOGGER.info("Restarting in %d seconds.", delay)
            except Exception as error:
                LOGGER.critical("ATTENTION: %s", error)
            except KeyboardInterrupt:
                return

    # TODO: Switch to fastapi - swagger with auth
    #   API should always run regardless of the state of telegram (as a backup measure)
    #   Northstar: Build a UI with login form
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
