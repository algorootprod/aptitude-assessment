from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.exceptions import ModuleError


def retry_sync_call() -> AsyncRetrying:
    """Tenacity policy for the evaluation_report → user_topic_mapping sync hop (retry 2–3)."""
    return AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
        retry=retry_if_exception_type((ModuleError, ConnectionError, TimeoutError)),
        reraise=True,
    )
