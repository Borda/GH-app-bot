import os

# Environment variables (set these in your env or .env file)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
