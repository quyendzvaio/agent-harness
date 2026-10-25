"""A small AI_SOC-like stack: compose base + `full` variant, FastAPI api, Kafka worker, Next.js frontend.

Planted on purpose: redis declared but never used (Q9-I6), a dead client call (Q9-I3), an unauthenticated
published route, a script that writes to the DB (batch-job `manual`), a smoke script that is only tooling,
an undeployed Dockerfile, and a README claim with no edge behind it.
"""

COMPOSE = """\
services:
  api:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://soc:soc@postgres:5432/soc
      SMTP_HOST: ${SMTP_HOST:-}
    depends_on:
      postgres:
        condition: service_healthy

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    environment:
      NEXT_PUBLIC_API_BASE_URL: http://localhost:8000
    depends_on:
      - api

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: soc
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U soc"]

  redis:
    image: redis:7-alpine

volumes:
  postgres_data:
"""

COMPOSE_FULL = """\
services:
  api:
    environment:
      KAFKA_BOOTSTRAP_SERVERS: kafka:9092
      KAFKA_TOPIC: security_events
    depends_on:
      - kafka

  worker:
    build: ./services/worker
    environment:
      API_BASE_URL: http://api:8000
      KAFKA_BOOTSTRAP_SERVERS: kafka:9092
      KAFKA_TOPIC: security_events
    depends_on:
      - kafka
      - api

  kafka:
    image: apache/kafka:3.9.0
"""

BACKEND_DOCKERFILE = """\
FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
"""

BACKEND_CONFIG = """\
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://soc:soc@localhost:5432/soc"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "security_events"
    smtp_host: str = ""


settings = Settings()
"""

BACKEND_MAIN = """\
from fastapi import FastAPI

from app.routers import internal, public, stream

app = FastAPI()
app.include_router(internal.router)
app.include_router(public.router)
app.include_router(stream.router)
"""

BACKEND_INTERNAL = """\
from fastapi import APIRouter, Depends

from app.deps import verify_token, get_session
from app.services.kafka import publish_event

router = APIRouter(prefix="/internal")


@router.post("/enrichments", dependencies=[Depends(verify_token)])
async def save_enrichment(payload: dict, session=Depends(get_session)):
    session.add(payload)
    await session.commit()
    await publish_event(payload)
    return {"ok": True}
"""

BACKEND_PUBLIC = """\
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.deps import get_session

router = APIRouter()


@router.get("/public")
async def public_alerts(session=Depends(get_session)):
    return (await session.execute(select("alerts"))).all()
"""

BACKEND_STREAM = """\
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()


@router.get("/stream/events")
async def stream_events():
    return StreamingResponse(iter(()), media_type="text/event-stream")
"""

BACKEND_DEPS = """\
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url)


async def get_session():
    async with engine.connect() as session:
        yield session


async def verify_token():
    return True
"""

BACKEND_KAFKA = """\
from aiokafka import AIOKafkaProducer

from app.core.config import settings

_producer = None


async def publish_event(event: dict) -> None:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers)
        await _producer.start()
    await _producer.send_and_wait(settings.kafka_topic, event)
"""

WORKER = """\
import os

import httpx
from aiokafka import AIOKafkaConsumer

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "security_events")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")


async def enrich(event: dict) -> dict:
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10) as client:
        try:
            response = await client.post("/internal/enrichments", json=event)
            return response.json()
        except httpx.HTTPError:
            return {}


async def stale_lookup() -> dict:
    async with httpx.AsyncClient(base_url=API_BASE_URL) as client:
        response = await client.get("/internal/nope")
        return response.json()


async def main() -> None:
    consumer = AIOKafkaConsumer(KAFKA_TOPIC, bootstrap_servers=KAFKA_BOOTSTRAP)
    await consumer.start()
    async for message in consumer:
        await enrich(message.value)
"""

WORKER_DOCKERFILE = """\
FROM python:3.12-slim
COPY . .
CMD ["python", "worker.py"]
"""

FRONTEND_PACKAGE = """\
{
  "name": "frontend",
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start"
  }
}
"""

FRONTEND_DOCKERFILE = """\
FROM node:20-alpine
COPY . .
RUN npm ci && npm run build
CMD ["npm", "run", "start"]
"""

FRONTEND_API = """\
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export async function listAlerts() {
  const response = await fetch(`${API_BASE_URL}/public`);
  return response.json();
}

export function subscribe(onEvent) {
  const source = new EventSource(`${API_BASE_URL}/stream/events`);
  source.onmessage = onEvent;
  return source;
}
"""

SEED = """\
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend_models import Alert

engine = create_engine(os.getenv("DATABASE_URL", "postgresql://soc:soc@postgres:5432/soc"))


def main() -> None:
    with Session(engine) as session:
        session.add(Alert(title="seed"))
        session.commit()


if __name__ == "__main__":
    main()
"""

SMOKE = """\
import requests

print(requests.get("http://127.0.0.1:8000/public", timeout=5).status_code)
"""

UNDEPLOYED_DOCKERFILE = """\
FROM python:3.12-slim
COPY . .
CMD ["python", "export.py"]
"""

README = """\
# Mini SOC

The frontend calls kafka directly to stream alerts.
"""

ENV_EXAMPLE = """\
SMTP_HOST=smtp.example.com
"""

FILES = {
    "docker-compose.yml": COMPOSE,
    "docker-compose.full.yml": COMPOSE_FULL,
    "backend/Dockerfile": BACKEND_DOCKERFILE,
    "backend/pyproject.toml": '[project]\nname = "backend"\n',
    "backend/app/__init__.py": "",
    "backend/app/main.py": BACKEND_MAIN,
    "backend/app/core/config.py": BACKEND_CONFIG,
    "backend/app/deps.py": BACKEND_DEPS,
    "backend/app/routers/internal.py": BACKEND_INTERNAL,
    "backend/app/routers/public.py": BACKEND_PUBLIC,
    "backend/app/routers/stream.py": BACKEND_STREAM,
    "backend/app/services/kafka.py": BACKEND_KAFKA,
    "services/worker/Dockerfile": WORKER_DOCKERFILE,
    "services/worker/worker.py": WORKER,
    "frontend/Dockerfile": FRONTEND_DOCKERFILE,
    "frontend/package.json": FRONTEND_PACKAGE,
    "frontend/lib/api.js": FRONTEND_API,
    "scripts/seed.py": SEED,
    "scripts/smoke.py": SMOKE,
    "tools/undeployed/Dockerfile": UNDEPLOYED_DOCKERFILE,
    "tools/undeployed/export.py": "print('export')\n",
    "README.md": README,
    ".env.example": ENV_EXAMPLE,
}

REPO = {"owner": "acme", "repo": "mini-soc", "meta": {"default_branch": "main", "description": "mini soc"}, "files": FILES}
URL = "https://github.com/acme/mini-soc"
