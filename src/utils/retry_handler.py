import asyncio
from typing import Any, Callable, TypeVar, Awaitable, Optional
from ccxt.base.errors import RequestTimeout, NetworkError
import logging

T = TypeVar('T')


class RetryHandler:
    """Handles retry logic with exponential backoff for network operations."""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 2.0,
        max_delay: float = 30.0,
        logger: Optional[logging.Logger] = None
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.logger = logger

    async def retry_with_backoff(
        self,
        operation: Callable[..., Awaitable[T]],
        operation_name: str,
        *args: Any,
        **kwargs: Any
    ) -> T:
        """Execute an operation with exponential backoff retry logic."""
        last_exception: Exception = Exception("No attempts made")

        for attempt in range(self.max_retries + 1):
            try:
                return await operation(*args, **kwargs)
            except (RequestTimeout, NetworkError, ConnectionError, TimeoutError) as e:
                last_exception = e
                if attempt == self.max_retries:
                    if self.logger:
                        self.logger.error(f"{operation_name} failed after {self.max_retries + 1} attempts: {e}")
                    raise e

                # Calculate delay with exponential backoff
                delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                if self.logger:
                    self.logger.warning(f"{operation_name} failed (attempt {attempt + 1}/{self.max_retries + 1}): {e}. Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
            except Exception as e:
                # For non-network errors, don't retry
                if self.logger:
                    self.logger.error(f"{operation_name} failed with non-retryable error: {e}")
                raise e

        # This should not be reached, but just in case
        raise last_exception
