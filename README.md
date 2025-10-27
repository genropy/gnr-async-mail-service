[![Documentation Status](https://readthedocs.org/projects/genro-mail-proxy/badge/?version=latest)](https://genro-mail-proxy.readthedocs.io/en/latest/)

# genro-mail-proxy

**Authors:** Softwell S.r.l. - Giovanni Porcari  
**License:** MIT

Asynchronous email dispatcher microservice with scheduling, rate limiting, attachments (S3/URL/base64), REST API (FastAPI), and Prometheus metrics.

## Why Use an Email Proxy?

Instead of directly connecting to SMTP servers from your application, genro-mail-proxy provides a **decoupled, resilient email delivery layer** with:

- ⚡ **19x faster requests** (32ms vs 620ms) - non-blocking async operations
- 🔄 **Never lose messages** - automatic retry, guaranteed persistence
- 🎯 **Connection pooling** - 10-50x faster for burst sends
- 📊 **Centralized monitoring** - Prometheus metrics, not scattered logs
- 🛡️ **Built-in rate limiting** - shared across all app instances
- 🎛️ **Priority queuing** - immediate/high/medium/low with automatic ordering

**See [Architecture Overview](docs/architecture_overview.rst)** for detailed comparison with direct SMTP.

## Main integration points:

- REST control plane secured by ``X-API-Token`` for queue management and configuration.
- Outbound ``proxy_sync`` call towards Genropy, authenticated via basic auth and configured through ``[client]`` in ``config.ini``.
- Delivery reports and Prometheus metrics to monitor message lifecycle and rate limiting.
- Unified SQLite storage with a single ``messages`` table that tracks queue state (`priority`, `deferred_ts`) and delivery lifecycle (`sent_ts`, `error_ts`, `reported_ts`).
- Background loops:
  - **SMTP dispatch loop** selects records from ``messages`` that lack ``sent_ts``/``error_ts`` and have ``deferred_ts`` in the past, enforces rate limits, then stamps ``sent_ts`` or ``error_ts``/``error``.
  - **Client report loop** batches completed items (sent/error/deferred) that are still missing ``reported_ts`` and posts them to the upstream ``proxy_sync`` endpoint; on acknowledgement the records receive ``reported_ts`` and are later purged according to the retention window.

## Quick start

```bash
docker build -t genro-mail-proxy .
docker run -p 8000:8000 -e SMTP_USER=... -e SMTP_PASSWORD=... -e FETCH_URL=https://your/api genro-mail-proxy
```

## Example client

A complete integration example is provided in `example_client.py`. This demonstrates the recommended pattern for integrating with the mail service:

```bash
# Install dependencies
pip install fastapi uvicorn aiohttp

# Configure your email address
nano example_config.ini  # Edit recipient_email

# Start the example client
python3 example_client.py

# Send test email
curl -X POST http://localhost:8081/send-test-email
```

The example shows:
- Local-first persistence (never lose messages)
- Async submission to mail service
- run-now trigger for fast delivery
- Delivery report handling via proxy_sync

**See [Example Client Documentation](docs/example_client.rst)** for detailed walkthrough.

## Configuration highlights

- ``[delivery]`` exposes ``delivery_report_retention_seconds`` to control how long reported messages stay in the ``messages`` table (default seven days).
- ``/commands/add-messages`` validates each payload (``id``, ``from``, ``to`` etc.), enqueues valid messages with `priority=2` when omitted, and returns a response with queued count plus a `rejected` list containing `{"id","reason"}` entries for failures.
