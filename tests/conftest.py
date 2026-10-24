import os

os.environ.setdefault("POSTGRES_DSN", "postgresql://ugtm_ai:test@localhost:5432/ugtm_ai")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("API_KEY", "test-api-key")

