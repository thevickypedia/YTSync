import logging

import uvicorn
from fastapi import FastAPI

from ytsync.api import models, serve
from ytsync.modules import config

LOGGER = logging.getLogger("ytsync")

app = FastAPI(
    title=config.API.name,
    description=(
        f"#### Gateway to communicate with {config.API.name}\n\n"
        f"**Source Code:** [{config.API.repo_name}]({config.API.repo_link})"
    ),
    version=config.API.version,
    lifespan=serve.lifespan,
    routes=serve.api_routes,
    openapi_tags=[dict(name=tag.name, description=tag.value) for tag in models.Tags],
)


def start():
    """Start the YTSync API server using uvicorn."""
    kwargs = dict(
        app=app,
        host=config.env.host,
        port=config.env.port,
        workers=1,
    )
    if config.env.log_config:
        kwargs["log_config"] = config.env.log_config
    uvicorn.run(**kwargs)
