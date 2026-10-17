from celery import Celery

from app.config import settings


celery_app = Celery(
    "ugtm_plagiarism_ai",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"],
)

