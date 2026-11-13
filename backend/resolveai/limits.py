"""Atomic Redis token buckets; a bounded development fallback without Redis."""

import hashlib
import os
import time

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

_local = {}
_SCRIPT = """
local value=redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens=tonumber(value[1]) or tonumber(ARGV[1])
local previous=tonumber(value[2]) or tonumber(ARGV[2])
tokens=math.min(tonumber(ARGV[1]),tokens+math.max(0,tonumber(ARGV[2])-previous)*tonumber(ARGV[1])/tonumber(ARGV[3]))
local allowed=0
if tokens>=1 then tokens=tokens-1 allowed=1 end
redis.call('HSET',KEYS[1],'tokens',tokens,'at',ARGV[2])
redis.call('EXPIRE',KEYS[1],math.ceil(tonumber(ARGV[3])*2))
return allowed
"""


async def rate_limit(scope, identity, capacity=60, seconds=60):
    if os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "false":
        return
    key = (
        "resolveai:rate:" + scope + ":" + hashlib.sha256(identity.encode()).hexdigest()
    )
    now, url = time.time(), os.getenv("REDIS_URL")
    if url:
        client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        try:
            allowed = bool(await client.eval(_SCRIPT, 1, key, capacity, now, seconds))
        except (RedisError, OSError):
            raise HTTPException(
                503, "The request limiter is temporarily unavailable. Please retry."
            ) from None
        finally:
            await client.aclose()
    else:
        previous, at = _local.get(key, (capacity, now))
        tokens = min(capacity, previous + max(0, now - at) * capacity / seconds)
        allowed = tokens >= 1
        if len(_local) > 10000:
            _local.clear()
        _local[key] = (tokens - 1 if allowed else tokens, now)
    if not allowed:
        raise HTTPException(
            429,
            "Too many requests. Wait a moment and try again.",
            headers={"Retry-After": str(seconds)},
        )
