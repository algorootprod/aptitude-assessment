from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aioboto3

from app.core.config import get_settings


@asynccontextmanager
async def sqs_client() -> AsyncIterator[Any]:
    settings = get_settings()
    session = aioboto3.Session(
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
    )
    async with session.client("sqs", endpoint_url=settings.sqs_endpoint_url) as client:
        yield client
