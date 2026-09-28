import logging

import uvicorn
from fastapi import FastAPI

from ytsync.api import models, serve
from ytsync.modules import config, releases

LOGGER = logging.getLogger("ytsync")

app = FastAPI(
    title=releases.github.REPO,
    description=(
        f"#### Gateway to communicate with {releases.github.REPO}\n\n"
        f"**Source Code:** [{releases.github.OWNER}/{releases.github.REPO}]({releases.github.BASE_WEB_URL})"
    ),
    version=config.API_VERSION,
    lifespan=serve.lifespan,
    routes=serve.api_routes,
    openapi_tags=[dict(name=tag.name, description=tag.value) for tag in models.Tags],
)


def start():
    """Start the Jarvis API server using uvicorn."""
    kwargs = dict(
        app=app,
        host=config.env.host,
        port=config.env.port,
        workers=1,
    )
    if config.env.log_config:
        kwargs["log_config"] = config.env.log_config
    uvicorn.run(**kwargs)
