"""
Security-review fix: neither /auth/login nor /staff/login had any limit on
failed attempts - unlimited password guessing was possible against any
account. In-memory, per-process limiter (slowapi's default storage) - fine
for this single-instance deployment; if this ever runs as multiple
instances behind a load balancer, the limiter needs a shared backend
(Redis) instead, since each instance would otherwise count independently.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
