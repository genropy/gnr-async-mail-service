import asyncio
import os
import json
import logging
import configparser
from pathlib import Path
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from async_mail_service.core import AsyncMailCore
from async_mail_service.api import create_app

# Configure logging level from environment
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    force=True  # Force reconfiguration to avoid duplicate handlers
)


def load_settings() -> dict[str, object]:
    """
    Load configuration from an INI file (default: config.ini) with environment variables as fallbacks.
    Supported sections/keys:
      [storage] db_path
      [server] host, port, api_token
      [client] client_sync_url, client_sync_user, client_sync_password, client_sync_token
      [delivery] send_interval_seconds, test_mode, default_priority, delivery_report_retention_seconds
      [logging] delivery_activity
    """
    config_path = Path(os.getenv("ASYNC_MAIL_CONFIG", "config.ini"))
    parser = configparser.ConfigParser()
    parser.read(config_path)

    def get(section: str, option: str, fallback: str | None = None) -> str | None:
        if parser.has_option(section, option):
            return parser.get(section, option)
        return fallback

    def get_int(section: str, option: str, fallback: str | None = None, default: int | None = None) -> int | None:
        value = get(section, option, fallback)
        if value is None:
            return default
        return int(value)

    def get_bool(section: str, option: str, fallback: str | None = None, default: bool | None = None) -> bool | None:
        value = get(section, option, fallback)
        if value is None:
            return default
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    def get_float(section: str, option: str, fallback: str | None = None, default: float | None = None) -> float | None:
        value = get(section, option, fallback)
        if value is None:
            return default
        return float(value)

    settings = {
        "db_path": get("storage", "db_path", os.getenv("DB_PATH", "/data/mail_service.db")),
        "http_host": get("server", "host", os.getenv("HOST", "0.0.0.0")),
        "http_port": get_int("server", "port", os.getenv("PORT", "8000")),
        "scheduler_active": get_bool("scheduler", "active", os.getenv("SCHEDULER_ACTIVE"), False),
        "api_token": get("server", "api_token", os.getenv("API_TOKEN")),
        "client_sync_url": get("client", "client_sync_url", os.getenv("CLIENT_SYNC_URL")),
        "client_sync_user": get("client", "client_sync_user", os.getenv("CLIENT_SYNC_USER")),
        "client_sync_password": get("client", "client_sync_password", os.getenv("CLIENT_SYNC_PASSWORD")),
        "client_sync_token": get("client", "client_sync_token", os.getenv("CLIENT_SYNC_TOKEN")),
        "send_loop_interval": get_float("delivery", "send_interval_seconds", os.getenv("SEND_LOOP_INTERVAL")),
        "test_mode": get_bool("delivery", "test_mode", os.getenv("TEST_MODE"), False),
        "default_priority": get_int("delivery", "default_priority", os.getenv("DEFAULT_PRIORITY"), default=2),
        "report_retention_seconds": get_int(
            "delivery",
            "delivery_report_retention_seconds",
            os.getenv("DELIVERY_REPORT_RETENTION_SECONDS"),
            default=7 * 24 * 3600,
        ),
        "batch_size_per_account": get_int(
            "delivery",
            "batch_size_per_account",
            os.getenv("BATCH_SIZE_PER_ACCOUNT"),
            default=50,
        ),
        "log_delivery_activity": get_bool(
            "logging",
            "delivery_activity",
            os.getenv("LOG_DELIVERY_ACTIVITY"),
            default=False,
        ),
        # IMAP receiving configuration
        "imap_enabled": get_bool("imap", "enabled", os.getenv("IMAP_ENABLED"), default=False),
        "imap_poll_interval": get_float("imap", "poll_interval_seconds", os.getenv("IMAP_POLL_INTERVAL"), default=60.0),
        "imap_message_retention_seconds": get_int("imap", "message_retention_seconds", os.getenv("IMAP_MESSAGE_RETENTION_SECONDS"), default=300),
        "imap_attachment_retention_seconds": get_int("imap", "attachment_retention_seconds", os.getenv("IMAP_ATTACHMENT_RETENTION_SECONDS"), default=600),
        "s3_bucket": get("imap", "s3_bucket", os.getenv("S3_BUCKET")),
        "s3_region": get("imap", "s3_region", os.getenv("S3_REGION"), default="eu-west-1"),
        "s3_access_key": get("imap", "s3_access_key", os.getenv("S3_ACCESS_KEY")),
        "s3_secret_key": get("imap", "s3_secret_key", os.getenv("S3_SECRET_KEY")),
        "s3_endpoint_url": get("imap", "s3_endpoint_url", os.getenv("S3_ENDPOINT_URL")),
        "s3_path_prefix": get("imap", "s3_path_prefix", os.getenv("S3_PATH_PREFIX"), default="received/"),
        "attachment_inline_max_size": get_int("imap", "attachment_inline_max_size", os.getenv("ATTACHMENT_INLINE_MAX_SIZE"), default=512 * 1024),
    }

    db_path = settings["db_path"]
    if isinstance(db_path, str):
        settings["db_path"] = os.path.expanduser(db_path)
    token = settings.get("api_token")
    if isinstance(token, str):
        token = token.strip() or None
    settings["api_token"] = token
    return settings


async def run_service(settings: dict[str, object]):
    service_kwargs = dict(
        db_path=settings["db_path"],
        start_active=bool(settings.get("scheduler_active")),
        client_sync_url=settings.get("client_sync_url"),
        client_sync_user=settings.get("client_sync_user"),
        client_sync_password=settings.get("client_sync_password"),
        client_sync_token=settings.get("client_sync_token"),
        default_priority=settings.get("default_priority"),
        report_retention_seconds=settings.get("report_retention_seconds"),
        batch_size_per_account=settings.get("batch_size_per_account"),
        test_mode=bool(settings.get("test_mode")),
        log_delivery_activity=bool(settings.get("log_delivery_activity")),
        # IMAP receiving
        imap_enabled=bool(settings.get("imap_enabled")),
        imap_poll_interval=settings.get("imap_poll_interval"),
        imap_message_retention_seconds=settings.get("imap_message_retention_seconds"),
        imap_attachment_retention_seconds=settings.get("imap_attachment_retention_seconds"),
        s3_bucket=settings.get("s3_bucket"),
        s3_region=settings.get("s3_region"),
        s3_access_key=settings.get("s3_access_key"),
        s3_secret_key=settings.get("s3_secret_key"),
        s3_endpoint_url=settings.get("s3_endpoint_url"),
        s3_path_prefix=settings.get("s3_path_prefix"),
        attachment_inline_max_size=settings.get("attachment_inline_max_size"),
    )
    send_loop_interval = settings.get("send_loop_interval")
    if send_loop_interval is not None:
        service_kwargs["send_loop_interval"] = float(send_loop_interval)

    service = AsyncMailCore(**service_kwargs)
    await service.start()
    return service


if __name__ == "__main__":
    settings = load_settings()
    # Create service instance but don't start it yet - let uvicorn handle the event loop
    service_kwargs = dict(
        db_path=settings["db_path"],
        start_active=bool(settings.get("scheduler_active")),
        client_sync_url=settings.get("client_sync_url"),
        client_sync_user=settings.get("client_sync_user"),
        client_sync_password=settings.get("client_sync_password"),
        client_sync_token=settings.get("client_sync_token"),
        default_priority=settings.get("default_priority"),
        report_retention_seconds=settings.get("report_retention_seconds"),
        batch_size_per_account=settings.get("batch_size_per_account"),
        test_mode=bool(settings.get("test_mode")),
        log_delivery_activity=bool(settings.get("log_delivery_activity")),
        # IMAP receiving
        imap_enabled=bool(settings.get("imap_enabled")),
        imap_poll_interval=settings.get("imap_poll_interval"),
        imap_message_retention_seconds=settings.get("imap_message_retention_seconds"),
        imap_attachment_retention_seconds=settings.get("imap_attachment_retention_seconds"),
        s3_bucket=settings.get("s3_bucket"),
        s3_region=settings.get("s3_region"),
        s3_access_key=settings.get("s3_access_key"),
        s3_secret_key=settings.get("s3_secret_key"),
        s3_endpoint_url=settings.get("s3_endpoint_url"),
        s3_path_prefix=settings.get("s3_path_prefix"),
        attachment_inline_max_size=settings.get("attachment_inline_max_size"),
    )
    send_loop_interval = settings.get("send_loop_interval")
    if send_loop_interval is not None:
        service_kwargs["send_loop_interval"] = float(send_loop_interval)

    service = AsyncMailCore(**service_kwargs)

    # Define lifespan context manager for startup/shutdown events
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: start the mail service
        await service.start()
        yield
        # Shutdown: cleanup if needed (currently no shutdown logic required)

    app = create_app(service, api_token=settings.get("api_token"), lifespan=lifespan)

    uvicorn.run(app, host=str(settings["http_host"]), port=int(settings["http_port"]))
