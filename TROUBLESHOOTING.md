# Troubleshooting Guide

This guide provides diagnostic tools and procedures for debugging issues with the genro-mail-proxy SMTP dispatcher.

## Quick Diagnostics

If messages are not being sent, run these checks in order:

```bash
# 1. Check overall system state
python3 diagnose.py

# 2. Verify loop is processing messages
python3 check_loop.py

# 3. Manually test message dispatch
python3 test_dispatch.py
```

---

## Diagnostic Scripts

### 📊 diagnose.py

**Purpose:** Comprehensive database and configuration analysis

**Usage:**
```bash
python3 diagnose.py [db_path]
```

**What it checks:**
- Message counts by status (pending/sent/error/deferred)
- Timestamp conditions (current vs deferred_ts)
- Exact simulation of SMTP loop query
- SMTP account configuration
- Detailed message payload inspection
- Recent send_log entries for rate limiting

**Example output:**
```
📊 STATO MESSAGGI
Messaggi totali:        15
Messaggi pending:       3
Messaggi inviati:       10
Messaggi con errore:    2

✅ TROVATI 3 messaggi pronti per invio
```

**When to use:**
- Messages appear stuck in queue
- Need to verify account configuration
- Investigating rate limiting behavior
- Understanding why messages aren't being picked up

---

### ⏱️ check_loop.py

**Purpose:** Monitor SMTP loop activity in real-time

**Usage:**
```bash
python3 check_loop.py
```

**What it does:**
- Captures message state snapshot
- Waits 5 seconds
- Compares before/after state
- Reports any changes in updated_at or deferred_ts
- Shows recent send activity

**Example output:**
```
📊 Messaggi pending: 2

⏳ Attendo 5 secondi...

✅ Messaggio abc123 updated_at cambiato
   PRIMA: 2025-10-23 09:00:00
   DOPO:  2025-10-23 09:00:05
→ Loop sta processando!
```

**When to use:**
- Service appears to be running but no messages are sent
- Need to confirm loop is actively processing
- Verifying send_interval_seconds configuration
- Checking if test_mode is blocking automatic dispatch

---

### 🧪 test_dispatch.py

**Purpose:** Manual message dispatch for isolated testing

**Usage:**
```bash
# Stop the service first to avoid DB locking
python3 test_dispatch.py
```

**What it does:**
- Initializes AsyncMailCore in test mode (no background loops)
- Fetches one ready message from database
- Manually invokes _dispatch_message()
- Reports detailed success/error/deferred status
- Shows exact exception if dispatch fails

**Example output:**
```
🚀 Tento dispatch...

📊 Stato messaggio dopo dispatch:
   sent_ts: 1761211032
   error_ts: None
   error: None

🎉 MESSAGGIO INVIATO CON SUCCESSO!
```

**When to use:**
- Need to reproduce delivery error in isolation
- Testing SMTP account credentials
- Debugging attachment issues
- Investigating rate limiting behavior
- Want to see exact error message without parsing logs

---

## Common Issues and Solutions

### ❌ Messages Stuck with error_ts

**Symptom:** diagnose.py shows pending=0 but sent < total messages

**Diagnosis:**
```sql
SELECT id, error, error_ts
FROM messages
WHERE error_ts IS NOT NULL;
```

**Common causes:**
1. **Invalid SMTP credentials** - `error: authentication failed`
2. **Account not found** - `error: Account 'xxx' not found`
3. **Missing required fields** - `error: missing from/to`
4. **SMTP connection failure** - `error: Connection refused`

**Solutions:**
- Reset error_ts to retry: `UPDATE messages SET error_ts=NULL, error=NULL WHERE id='xxx'`
- Fix account configuration via `/account` API endpoint
- Verify SMTP host/port are correct
- Check network connectivity to SMTP server

---

### ⏸️ Messages with Future deferred_ts

**Symptom:** diagnose.py shows "FUTURE" deferred status

**Diagnosis:**
```sql
SELECT id, deferred_ts, datetime(deferred_ts, 'unixepoch')
FROM messages
WHERE deferred_ts > strftime('%s', 'now');
```

**Cause:** Rate limiter has deferred messages due to:
- `limit_per_minute` exceeded
- `limit_per_hour` exceeded
- `limit_per_day` exceeded

**Solutions:**
- Wait until deferred_ts passes (automatic)
- Clear deferred_ts for immediate retry: `UPDATE messages SET deferred_ts=NULL WHERE id='xxx'`
- Increase rate limits on SMTP account
- Use different account with higher limits

---

### 🔄 Loop Not Processing

**Symptom:** check_loop.py shows "Nessun cambiamento rilevato"

**Diagnosis:**
```bash
# Check if service is running
ps aux | grep "python.*main.py"

# Check loop interval
grep send_interval_seconds config.ini

# Check test mode
grep test_mode config.ini
```

**Common causes:**
1. **Service not running** - Process crashed or never started
2. **test_mode=true** - Loop waits for manual "run now" trigger
3. **send_interval too high** - Loop sleeping for long period
4. **All messages have error_ts** - Nothing to process

**Solutions:**
- Start service: `python3 main.py`
- Set `test_mode=false` in config.ini
- Reduce `send_interval_seconds` (e.g., 0.5)
- Force wake up: `curl -X POST http://localhost:8000/commands/run-now`

---

### 🚫 Account Configuration Missing

**Symptom:** diagnose.py shows "⚠️ Nessun account configurato"

**Diagnosis:**
```sql
SELECT * FROM accounts;
```

**Solution:** Add SMTP account via API:
```bash
curl -X POST http://localhost:8000/account \
  -H "X-API-Token: your-token" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "smtp-main",
    "host": "smtp.gmail.com",
    "port": 587,
    "user": "your@gmail.com",
    "password": "app-password",
    "use_tls": false
  }'
```

**Gmail specific:**
- Use App Password, not regular password
- Enable 2FA and generate App Password at: https://myaccount.google.com/apppasswords
- Port 587: use_tls=false (STARTTLS)
- Port 465: use_tls=true (TLS)

---

## Debug Checklist

When investigating delivery issues, follow this checklist:

- [ ] **Run diagnose.py** - Get system overview
  - [ ] Any pending messages found?
  - [ ] Any error messages? Check `error` field
  - [ ] Any future deferred_ts?
  - [ ] Account configuration exists?

- [ ] **Check configuration** - Verify config.ini
  - [ ] `test_mode = false`
  - [ ] `send_interval_seconds` reasonable (0.5-2.0)
  - [ ] `delivery_activity = true` (for detailed logs)

- [ ] **Verify service running**
  - [ ] Process active: `ps aux | grep main.py`
  - [ ] API responding: `curl http://localhost:8000/status`

- [ ] **Run check_loop.py** - Confirm loop activity
  - [ ] updated_at changing?
  - [ ] Recent send_log entries?

- [ ] **Manual test** - Run test_dispatch.py
  - [ ] Exact error message if failing
  - [ ] SMTP connection successful?
  - [ ] Authentication working?

- [ ] **Check logs** - Review service output
  - [ ] Look for "Attempting delivery" messages
  - [ ] Look for "Delivery failed" warnings
  - [ ] Any Python exceptions/tracebacks?

---

## Enabling Debug Logging

For maximum visibility during troubleshooting:

**config.ini:**
```ini
[logging]
delivery_activity = true
```

**Expected log output:**
```
[2025-10-23 11:17:31] [INFO] Attempting delivery for message msg-001 to user@example.com (account=smtp-main)
[2025-10-23 11:17:32] [INFO] Delivery succeeded for message msg-001 (account=smtp-main)
```

**Error log output:**
```
[2025-10-23 11:17:31] [WARNING] Delivery failed for message msg-001 (account=smtp-main): authentication failed
```

---

## Manual Database Queries

Useful SQL queries for direct database inspection:

```sql
-- Count messages by state
SELECT
  SUM(CASE WHEN sent_ts IS NOT NULL THEN 1 ELSE 0 END) as sent,
  SUM(CASE WHEN error_ts IS NOT NULL THEN 1 ELSE 0 END) as errors,
  SUM(CASE WHEN sent_ts IS NULL AND error_ts IS NULL THEN 1 ELSE 0 END) as pending
FROM messages;

-- Find stuck messages (pending > 1 hour)
SELECT id, created_at, account_id
FROM messages
WHERE sent_ts IS NULL
  AND error_ts IS NULL
  AND created_at < datetime('now', '-1 hour');

-- Check rate limiting for account
SELECT COUNT(*) as sends_last_hour
FROM send_log
WHERE account_id = 'your-account-id'
  AND timestamp > strftime('%s', 'now', '-1 hour');

-- Find messages with errors
SELECT id, error, datetime(error_ts, 'unixepoch') as error_time
FROM messages
WHERE error_ts IS NOT NULL
ORDER BY error_ts DESC
LIMIT 10;
```

---

## Getting Help

If issues persist after following this guide:

1. **Collect diagnostic output:**
   ```bash
   python3 diagnose.py > diagnostics.txt
   python3 check_loop.py >> diagnostics.txt
   ```

2. **Include service logs:**
   ```bash
   tail -100 /path/to/service.log > service_logs.txt
   ```

3. **Check database state:**
   ```bash
   sqlite3 /tmp/mail_service.db ".dump messages" > db_dump.txt
   ```

4. **Open issue on GitHub** with:
   - diagnostics.txt
   - service_logs.txt
   - config.ini (redact sensitive values)
   - Description of expected vs actual behavior
