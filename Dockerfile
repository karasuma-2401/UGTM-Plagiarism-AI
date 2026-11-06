FROM python:3.12-slim

WORKDIR /service

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY requirements ./requirements
RUN python -m pip install --no-cache-dir -r requirements/cpu-worker.txt

COPY app ./app
# Classifier artifacts are excluded from the build context and supplied by a volume.
RUN mkdir -p ./artifacts
COPY training ./training
COPY tests ./tests

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
