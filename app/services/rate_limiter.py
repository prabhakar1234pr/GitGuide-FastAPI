"""
Distributed rate limiter using Redis.
Prevents hitting LLM API rate limits by coordinating requests across workers.
"""

import asyncio
import logging
import time

try:
    import redis.asyncio as redis

    REDIS_AVAILABLE = True
except ImportError:
    try:
        import redis

        REDIS_AVAILABLE = True
    except ImportError:
        REDIS_AVAILABLE = False

from app.config import settings

logger = logging.getLogger(__name__)


# Fallback rate limiter if Redis is not available
class InMemoryRateLimiter:
    """In-memory rate limiter for development/testing without Redis"""

    def __init__(self, max_requests: int = 20, window_seconds: int = 60, min_delay: float = 3.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.min_delay_between_requests = min_delay  # 3 seconds minimum
        self.requests = []
        self.last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Wait until we can make a request"""
        async with self._lock:
            now = time.time()

            # Enforce minimum delay between requests
            time_since_last = now - self.last_request_time
            if time_since_last < self.min_delay_between_requests:
                wait_time = self.min_delay_between_requests - time_since_last
                logger.debug(f"⏳ Enforcing minimum delay: waiting {wait_time:.1f}s (in-memory)")
                await asyncio.sleep(wait_time)
                now = time.time()

            # Remove old requests outside the window
            self.requests = [
                req_time for req_time in self.requests if now - req_time < self.window_seconds
            ]

            # If at limit, wait until oldest request expires
            if len(self.requests) >= self.max_requests:
                oldest = min(self.requests)
                wait_time = oldest + self.window_seconds - now + 0.1
                logger.info(f"⏳ Rate limit: waiting {wait_time:.1f}s (in-memory limiter)")
                await asyncio.sleep(wait_time)
                return await self.acquire()

            self.requests.append(now)
            self.last_request_time = now
            return True


class RedisRateLimiter:
    """Redis-based distributed rate limiter"""

    def __init__(
        self,
        redis_client: redis.Redis | None = None,
        max_requests: int = 20,  # More conservative: 20 RPM (leaves buffer below 30 RPM limit)
        window_seconds: int = 60,
        key_prefix: str = "rate_limit:groq:",
        min_delay_between_requests: float = 3.0,  # Minimum 3 seconds between requests
    ):
        self.redis_client = redis_client
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix
        self.min_delay_between_requests = min_delay_between_requests
        self.last_request_time = 0.0
        # IMPORTANT: if Redis is not configured/available, we must keep state
        # across calls. Creating a new in-memory limiter per acquire() would
        # effectively disable window-based limiting.
        self._fallback_limiter = InMemoryRateLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
            min_delay=min_delay_between_requests,
        )

    async def acquire(self, identifier: str = "default"):
        """
        Acquire permission to make a request.
        Blocks until a slot is available.
        Also enforces minimum delay between requests.

        Args:
            identifier: Optional identifier for different rate limit buckets

        Returns:
            True when permission is granted
        """
        # Enforce minimum delay between requests
        now = time.time()
        time_since_last = now - self.last_request_time
        if time_since_last < self.min_delay_between_requests:
            wait_time = self.min_delay_between_requests - time_since_last
            logger.debug(f"⏳ Enforcing minimum delay: waiting {wait_time:.1f}s")
            await asyncio.sleep(wait_time)
            now = time.time()

        if not self.redis_client:
            # Fallback to in-memory if Redis not available (stateful)
            result = await self._fallback_limiter.acquire()
            self.last_request_time = time.time()
            return result

        key = f"{self.key_prefix}{identifier}"
        now = time.time()
        window_start = now - self.window_seconds

        try:
            # Use Redis pipeline for atomic operations
            pipe = self.redis_client.pipeline()

            # Remove old entries (outside window)
            pipe.zremrangebyscore(key, 0, window_start)

            # Count current requests in window
            pipe.zcard(key)

            # Add current request
            pipe.zadd(key, {str(now): now})

            # Set expiration on key
            pipe.expire(key, self.window_seconds + 10)

            results = await pipe.execute()
            current_count = results[1]

            if current_count >= self.max_requests:
                # Get oldest request time
                oldest_requests = await self.redis_client.zrange(key, 0, 0, withscores=True)

                if oldest_requests:
                    oldest_time = oldest_requests[0][1]
                    wait_time = oldest_time + self.window_seconds - now + 0.1

                    if wait_time > 0:
                        logger.info(f"⏳ Rate limit: waiting {wait_time:.1f}s")
                        await asyncio.sleep(wait_time)
                        return await self.acquire(identifier)

            self.last_request_time = time.time()
            return True

        except Exception as e:
            logger.warning(f"⚠️  Redis rate limiter error: {e}, falling back to in-memory")
            result = await self._fallback_limiter.acquire()
            self.last_request_time = time.time()
            return result


# Singleton instance
_rate_limiter: RedisRateLimiter | None = None
_redis_client: redis.Redis | None = None


async def get_redis_client() -> redis.Redis | None:
    """Get or create Redis client"""
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    redis_url = getattr(settings, "redis_url", None)

    if not redis_url:
        logger.info("ℹ️  Redis URL not configured, using in-memory rate limiter")
        return None

    try:
        _redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=True,
        )
        # Test connection
        await _redis_client.ping()
        logger.info("✅ Redis connected for rate limiting")
        return _redis_client
    except Exception as e:
        logger.warning(f"⚠️  Failed to connect to Redis: {e}, using in-memory rate limiter")
        return None


def get_rate_limiter() -> RedisRateLimiter:
    """Get or create rate limiter instance"""
    global _rate_limiter

    if _rate_limiter is None:
        # Try to get Redis client (non-blocking, will be None if not available)
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, create client in background
                redis_client = None
            else:
                redis_client = loop.run_until_complete(get_redis_client())
        except RuntimeError:
            # No event loop, will create client on first use
            redis_client = None

        _rate_limiter = RedisRateLimiter(
            redis_client=redis_client,
            max_requests=20,  # Conservative: 20 RPM (buffer below 30 RPM limit)
            window_seconds=60,
            min_delay_between_requests=3.0,  # 3 seconds minimum between requests
        )
        logger.info("✅ Rate limiter initialized")

    return _rate_limiter


async def initialize_rate_limiter():
    """Initialize Redis client for rate limiter (call on startup)"""
    global _rate_limiter, _redis_client

    if _rate_limiter is None:
        _redis_client = await get_redis_client()
        _rate_limiter = RedisRateLimiter(
            redis_client=_redis_client,
            max_requests=20,  # Conservative: 20 RPM
            window_seconds=60,
            min_delay_between_requests=3.0,  # 3 seconds minimum delay
        )
        logger.info("✅ Rate limiter initialized (20 RPM, 3s min delay)")
