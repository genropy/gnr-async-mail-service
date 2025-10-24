"""SQLite backed persistence used by the mail dispatcher."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import aiosqlite


class Persistence:
    """Helper class responsible for reading and writing service state."""

    def __init__(self, db_path: str = "/data/mail_service.db"):
        """Persist data to the given database path (``:memory:`` allowed)."""
        self.db_path = db_path or ":memory:"

    async def init_db(self) -> None:
        """Create (or migrate) the database schema."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    user TEXT,
                    password TEXT,
                    ttl INTEGER DEFAULT 300,
                    limit_per_minute INTEGER,
                    limit_per_hour INTEGER,
                    limit_per_day INTEGER,
                    limit_behavior TEXT,
                    use_tls INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN use_tls INTEGER")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN batch_size INTEGER")
            except aiosqlite.OperationalError:
                pass
            # IMAP receiving columns
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN is_receive_account INTEGER DEFAULT 0")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN imap_host TEXT")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN imap_port INTEGER")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN imap_ssl INTEGER DEFAULT 1")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN imap_folder TEXT DEFAULT 'INBOX'")
            except aiosqlite.OperationalError:
                pass

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS send_log (
                    account_id TEXT,
                    timestamp INTEGER
                )
                """
            )

            await db.execute("DROP TABLE IF EXISTS pending_messages")
            await db.execute("DROP TABLE IF EXISTS deferred_messages")
            await db.execute("DROP TABLE IF EXISTS delivery_reports")
            await db.execute("DROP TABLE IF EXISTS queued_messages")

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    account_id TEXT,
                    priority INTEGER NOT NULL DEFAULT 2,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    deferred_ts INTEGER,
                    sent_ts INTEGER,
                    error_ts INTEGER,
                    error TEXT,
                    reported_ts INTEGER
                )
                """
            )

            # IMAP receiving: buffer for received messages
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS received_messages (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    imap_uid INTEGER NOT NULL,
                    message_id TEXT,
                    from_address TEXT,
                    from_name TEXT,
                    to_address TEXT,
                    cc_address TEXT,
                    bcc_address TEXT,
                    subject TEXT,
                    body_html TEXT,
                    body_plain TEXT,
                    send_date TEXT,
                    received_ts INTEGER NOT NULL,
                    has_attachments INTEGER DEFAULT 0,
                    attachment_count INTEGER DEFAULT 0,
                    attachments TEXT,
                    headers TEXT,
                    synced_ts INTEGER,
                    UNIQUE(account_id, imap_uid)
                )
                """
            )

            await db.execute("CREATE INDEX IF NOT EXISTS idx_received_synced ON received_messages(synced_ts) WHERE synced_ts IS NULL")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_received_cleanup ON received_messages(synced_ts) WHERE synced_ts IS NOT NULL")

            # IMAP sync state: track last UID per account
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS imap_sync_state (
                    account_id TEXT PRIMARY KEY,
                    last_uid INTEGER,
                    last_sync_ts INTEGER
                )
                """
            )

            await db.commit()

    # Accounts -----------------------------------------------------------------
    async def add_account(self, acc: Dict[str, Any]) -> None:
        """Insert or overwrite an SMTP account definition."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO accounts
                (id, host, port, user, password, ttl, limit_per_minute, limit_per_hour, limit_per_day, limit_behavior, use_tls, batch_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    acc["id"],
                    acc["host"],
                    int(acc["port"]),
                    acc.get("user"),
                    acc.get("password"),
                    int(acc.get("ttl", 300)),
                    acc.get("limit_per_minute"),
                    acc.get("limit_per_hour"),
                    acc.get("limit_per_day"),
                    acc.get("limit_behavior", "defer"),
                    None if acc.get("use_tls") is None else (1 if acc.get("use_tls") else 0),
                    acc.get("batch_size"),
                ),
            )
            await db.commit()

    async def list_accounts(self) -> List[Dict[str, Any]]:
        """Return all known SMTP accounts."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, host, port, user, ttl, limit_per_minute, limit_per_hour,
                       limit_per_day, limit_behavior, use_tls, batch_size, created_at
                FROM accounts
                """
            ) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        result = [dict(zip(cols, row)) for row in rows]
        for acc in result:
            if "use_tls" in acc:
                acc["use_tls"] = bool(acc["use_tls"]) if acc["use_tls"] is not None else None
        return result

    async def delete_account(self, account_id: str) -> None:
        """Remove a previously stored SMTP account and related state."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            await db.execute("DELETE FROM messages WHERE account_id=?", (account_id,))
            await db.execute("DELETE FROM send_log WHERE account_id=?", (account_id,))
            await db.commit()

    async def get_account(self, account_id: str) -> Dict[str, Any]:
        """Fetch a single SMTP account or raise if it does not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)) as cur:
                row = await cur.fetchone()
                if not row:
                    raise ValueError(f"Account '{account_id}' not found")
                cols = [c[0] for c in cur.description]
                account = dict(zip(cols, row))
        if "use_tls" in account:
            account["use_tls"] = bool(account["use_tls"]) if account["use_tls"] is not None else None
        return account

    # Messages -----------------------------------------------------------------
    @staticmethod
    def _decode_message_row(row: Tuple[Any, ...], columns: Sequence[str]) -> Dict[str, Any]:
        data = dict(zip(columns, row))
        payload = data.pop("payload", None)
        if payload is not None:
            try:
                data["message"] = json.loads(payload)
            except json.JSONDecodeError:
                data["message"] = {"raw_payload": payload}
        else:
            data["message"] = None
        return data

    async def insert_messages(self, entries: Sequence[Dict[str, Any]]) -> List[str]:
        """Persist a batch of messages, returning the ids that were stored.

        If a message with the same id already exists but has NOT been sent (sent_ts IS NULL),
        it will be replaced with the new data. This allows clients to correct errors or
        retry with different parameters. Messages that have been sent are never replaced.
        """
        if not entries:
            return []
        inserted: List[str] = []
        async with aiosqlite.connect(self.db_path) as db:
            for entry in entries:
                msg_id = entry["id"]
                payload = json.dumps(entry["payload"])
                account_id = entry.get("account_id")
                priority = int(entry.get("priority", 2))
                deferred_ts = entry.get("deferred_ts")

                cursor = await db.execute(
                    """
                    INSERT INTO messages (id, account_id, priority, payload, deferred_ts)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        account_id = excluded.account_id,
                        priority = excluded.priority,
                        payload = excluded.payload,
                        deferred_ts = excluded.deferred_ts,
                        error_ts = NULL,
                        error = NULL,
                        reported_ts = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE sent_ts IS NULL
                    """,
                    (msg_id, account_id, priority, payload, deferred_ts),
                )

                # Check if operation succeeded (INSERT or UPDATE)
                if cursor.rowcount:
                    inserted.append(msg_id)
            await db.commit()
        return inserted

    async def fetch_ready_messages(self, *, limit: int, now_ts: int) -> List[Dict[str, Any]]:
        """Return messages eligible for SMTP dispatch."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, account_id, priority, payload, deferred_ts
                FROM messages
                WHERE sent_ts IS NULL
                  AND error_ts IS NULL
                  AND (deferred_ts IS NULL OR deferred_ts <= ?)
                ORDER BY priority ASC, created_at ASC, id ASC
                LIMIT ?
                """,
                (now_ts, limit),
            ) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        return [self._decode_message_row(row, cols) for row in rows]

    async def set_deferred(self, msg_id: str, deferred_ts: int) -> None:
        """Update the deferred timestamp for a message."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET deferred_ts=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND sent_ts IS NULL AND error_ts IS NULL
                """,
                (deferred_ts, msg_id),
            )
            await db.commit()

    async def clear_deferred(self, msg_id: str) -> None:
        """Clear the deferred timestamp for a message."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET deferred_ts=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (msg_id,),
            )
            await db.commit()

    async def mark_sent(self, msg_id: str, sent_ts: int) -> None:
        """Mark a message as sent.

        Resets reported_ts so the message will be reported with final state.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET sent_ts=?, error_ts=NULL, error=NULL, deferred_ts=NULL, reported_ts=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (sent_ts, msg_id),
            )
            await db.commit()

    async def mark_error(self, msg_id: str, error_ts: int, error: str) -> None:
        """Mark a message as failed.

        Resets reported_ts and deferred_ts so the message will be reported with final error state.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET error_ts=?, error=?, sent_ts=NULL, deferred_ts=NULL, reported_ts=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (error_ts, error, msg_id),
            )
            await db.commit()

    async def update_message_payload(self, msg_id: str, payload: Dict[str, Any]) -> None:
        """Update the payload field of a message (used for retry count tracking)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET payload=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (json.dumps(payload), msg_id),
            )
            await db.commit()

    async def delete_message(self, msg_id: str) -> bool:
        """Remove a message regardless of its state."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM messages WHERE id=?", (msg_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def purge_messages_for_account(self, account_id: str) -> None:
        """Delete every message linked to the given account."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM messages WHERE account_id=?", (account_id,))
            await db.commit()

    async def existing_message_ids(self, ids: Iterable[str]) -> set[str]:
        """Return the subset of ids that already exist in storage."""
        id_list = [mid for mid in ids if mid]
        if not id_list:
            return set()
        placeholders = ",".join("?" for _ in id_list)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT id FROM messages WHERE id IN ({placeholders})",
                id_list,
            ) as cur:
                rows = await cur.fetchall()
        return {row[0] for row in rows}

    async def fetch_reports(self, limit: int) -> List[Dict[str, Any]]:
        """Return messages that need to be reported back to the client.

        Only returns messages in final states (sent or error).
        Messages with only deferred_ts are not reported (internal retry logic).
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, account_id, priority, payload, sent_ts, error_ts, error, deferred_ts
                FROM messages
                WHERE reported_ts IS NULL
                  AND (sent_ts IS NOT NULL OR error_ts IS NOT NULL)
                ORDER BY updated_at ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        return [self._decode_message_row(row, cols) for row in rows]

    async def mark_reported(self, message_ids: Iterable[str], reported_ts: int) -> None:
        """Set the reported timestamp for the provided messages."""
        ids = [mid for mid in message_ids if mid]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"""
                UPDATE messages
                SET reported_ts=?, updated_at=CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                (reported_ts, *ids),
            )
            await db.commit()

    async def remove_reported_before(self, threshold_ts: int) -> int:
        """Delete reported messages older than ``threshold_ts``.

        Only deletes messages in final states (sent or error).
        Messages with only deferred_ts are kept in queue until they reach a final state.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM messages
                WHERE reported_ts IS NOT NULL
                  AND reported_ts < ?
                  AND (sent_ts IS NOT NULL OR error_ts IS NOT NULL)
                """,
                (threshold_ts,),
            )
            await db.commit()
            return cursor.rowcount

    async def list_messages(self, *, active_only: bool = False) -> List[Dict[str, Any]]:
        """Return messages for inspection purposes."""
        query = """
            SELECT id, account_id, priority, payload, deferred_ts, sent_ts, error_ts,
                   error, reported_ts, created_at, updated_at
            FROM messages
        """
        params: Tuple[Any, ...] = ()
        if active_only:
            query += " WHERE sent_ts IS NULL AND error_ts IS NULL"
        query += " ORDER BY priority ASC, created_at ASC, id ASC"
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        return [self._decode_message_row(row, cols) for row in rows]

    async def count_active_messages(self) -> int:
        """Return the number of messages still awaiting delivery."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT COUNT(*) FROM messages
                WHERE sent_ts IS NULL AND error_ts IS NULL
                """
            ) as cur:
                row = await cur.fetchone()
        return int(row[0] if row else 0)

    # Send log -----------------------------------------------------------------
    async def log_send(self, account_id: str, timestamp: int) -> None:
        """Record a delivery event for rate limiting purposes."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT INTO send_log (account_id, timestamp) VALUES (?, ?)", (account_id, timestamp))
            await db.commit()

    async def count_sends_since(self, account_id: str, since_ts: int) -> int:
        """Count messages sent after ``since_ts`` for the given account."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM send_log WHERE account_id=? AND timestamp > ?",
                (account_id, since_ts),
            ) as cur:
                row = await cur.fetchone()
        return int(row[0] if row else 0)

    # IMAP Receiving -----------------------------------------------------------
    async def list_receive_accounts(self) -> List[Dict[str, Any]]:
        """Return all accounts configured for IMAP receiving."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, host, port, user, password, imap_host, imap_port, imap_ssl, imap_folder
                FROM accounts
                WHERE is_receive_account = 1
                """
            ) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    async def insert_received_message(self, message: Dict[str, Any]) -> None:
        """Store a received message in the buffer."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO received_messages
                (id, account_id, imap_uid, message_id, from_address, from_name,
                 to_address, cc_address, bcc_address, subject, body_html, body_plain,
                 send_date, received_ts, has_attachments, attachment_count, attachments, headers)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message['id'],
                    message['account_id'],
                    message['imap_uid'],
                    message.get('message_id'),
                    message.get('from_address'),
                    message.get('from_name'),
                    message.get('to_address'),
                    message.get('cc_address'),
                    message.get('bcc_address'),
                    message.get('subject'),
                    message.get('body_html'),
                    message.get('body_plain'),
                    message.get('send_date'),
                    message['received_ts'],
                    message.get('has_attachments', 0),
                    message.get('attachment_count', 0),
                    message.get('attachments'),
                    message.get('headers'),
                )
            )
            await db.commit()

    async def fetch_received_messages(self, limit: int) -> List[Dict[str, Any]]:
        """Return received messages that need to be synced to client."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT id, account_id, imap_uid, message_id, from_address, from_name,
                       to_address, cc_address, bcc_address, subject, body_html, body_plain,
                       send_date, has_attachments, attachment_count, attachments, headers
                FROM received_messages
                WHERE synced_ts IS NULL
                ORDER BY received_ts ASC
                LIMIT ?
                """,
                (limit,)
            ) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    async def mark_received_synced(self, message_ids: Iterable[str], synced_ts: int) -> None:
        """Mark received messages as synced to client."""
        ids = [mid for mid in message_ids if mid]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE received_messages SET synced_ts=? WHERE id IN ({placeholders})",
                (synced_ts, *ids),
            )
            await db.commit()

    async def cleanup_synced_received_messages(self, older_than_seconds: int) -> int:
        """Delete received messages synced more than X seconds ago."""
        import time
        threshold = int(time.time()) - older_than_seconds
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM received_messages WHERE synced_ts IS NOT NULL AND synced_ts < ?",
                (threshold,)
            )
            await db.commit()
            return cursor.rowcount

    async def get_imap_last_uid(self, account_id: str) -> Optional[int]:
        """Get the last processed UID for an IMAP account."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT last_uid FROM imap_sync_state WHERE account_id=?",
                (account_id,)
            ) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row and row[0] else None

    async def update_imap_last_uid(self, account_id: str, last_uid: int) -> None:
        """Update the last processed UID for an IMAP account."""
        import time
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO imap_sync_state (account_id, last_uid, last_sync_ts)
                VALUES (?, ?, ?)
                """,
                (account_id, last_uid, int(time.time()))
            )
            await db.commit()

