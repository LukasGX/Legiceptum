from slowapi import Limiter
from slowapi.util import get_remote_address

# Counters are kept in memory: they reset on restart and are per worker process.
# The client address comes from request.client, which uvicorn fills from
# X-Forwarded-For only for proxies listed in --forwarded-allow-ips.
limiter = Limiter(key_func=get_remote_address)

LOGIN_LIMIT = "5/minute;30/hour"
REGISTER_LIMIT = "5/hour"
RESEND_LIMIT = "5/hour"
