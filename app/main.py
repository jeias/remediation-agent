"""
FastAPI application monitored by the AI Remediation Agent.

Logging contract: every log line is a JSON object with fields:
  - timestamp (ISO 8601)
  - level (INFO, ERROR, WARNING)
  - message (human-readable description)
  - error (optional, full exception string on failures)

The AI agent distinguishes two failure modes by error patterns:
  - ProgrammingError ("column does not exist") -> broken deployment -> app runs, endpoint fails with 500
  - OperationalError ("Connection refused") -> infrastructure issue -> app runs, all endpoints fail with 503
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import psycopg2
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pythonjsonlogger.json import JsonFormatter

# --- Structured JSON Logging ---

logger = logging.getLogger("app")
logger.setLevel(logging.INFO)

handler = logging.StreamHandler(sys.stdout)
formatter = JsonFormatter(
    "%(asctime)s %(levelname)s %(message)s",
    rename_fields={"asctime": "timestamp", "levelname": "level"},
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
handler.setFormatter(formatter)
logger.addHandler(handler)

# Suppress uvicorn's default loggers to avoid duplicate unstructured output
for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
    uvi_logger = logging.getLogger(name)
    uvi_logger.handlers = [handler]
    uvi_logger.propagate = False


# --- Database ---

DB_CONFIG = {
    "host": os.environ.get("DATABASE_HOST", "localhost"),
    "port": os.environ.get("DATABASE_PORT", "5432"),
    "dbname": os.environ.get("DATABASE_NAME", "remediationdb"),
    "user": os.environ.get("DATABASE_USER", "postgres"),
    "password": os.environ.get("DATABASE_PASSWORD", ""),
    "connect_timeout": 5,
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS items (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
"""


def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


def init_database():
    """Connect to DB and create tables. On failure, log and continue (degraded mode)."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
        return True
    except psycopg2.OperationalError as e:
        logger.error("Database connection failed", extra={"error": str(e)})
        logger.warning("Application started with degraded database connectivity")
        return False


# --- App Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application on 0.0.0.0:8000")
    init_database()
    yield


app = FastAPI(lifespan=lifespan)


# --- Request Logging Middleware ---

@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    logger.info(f"{request.method} {request.url.path} - {response.status_code}")
    return response


# --- Endpoints ---

@app.get("/health")
def health():
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}
    except psycopg2.Error as e:
        logger.error("Database connection failed", extra={"error": str(e)})
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)},
        )


@app.get("/items")
def list_items():
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # BROKEN DEPLOY: uncomment the line below to simulate a bad deployment
            # (code expects a column from a migration that was never applied) test30
            # cur.execute("SELECT id, name, description, created_at FROM items ORDER BY created_at DESC")
            # cur.execute("SELECT id, name, created_at FROM items ORDER BY created_at DESC")
            rows = cur.fetchall()
        conn.close()
        return [
            {"id": row[0], "name": row[1], "created_at": row[2].isoformat()}
            for row in rows
        ]
    except psycopg2.Error as e:
        logger.error("Database connection failed", extra={"error": str(e)})
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)},
        )


@app.post("/items", status_code=201)
def create_item(item: dict):
    name = item.get("name")
    if not name:
        return JSONResponse(status_code=400, content={"error": "name is required"})
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO items (name) VALUES (%s) RETURNING id, name, created_at",
                (name,),
            )
            row = cur.fetchone()
        conn.commit()
        conn.close()
        return {"id": row[0], "name": row[1], "created_at": row[2].isoformat()}
    except psycopg2.Error as e:
        logger.error("Database connection failed", extra={"error": str(e)})
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)},
        )
