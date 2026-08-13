"""
Security-review fix: neither /auth/login nor /staff/login had any limit on
failed attempts - unlimited password guessing was possible against any
account. Storage is Redis-backed when settings.redis_url is set (see
docker-compose.yml's `redis` service) so every uvicorn worker - and every
instance, if this ever runs behind a load balancer - shares one counter.
Falls back to slowapi's default in-memory storage when redis_url is blank
(e.g. running app.main directly without docker-compose) - correct only for
a single worker process, since in-memory storage is per-process.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(key_func=get_remote_address, storage_uri=settings.redis_url or "memory://")
