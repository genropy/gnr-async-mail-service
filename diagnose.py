#!/usr/bin/env python3
"""
Script di diagnostica per genro-mail-proxy
Controlla lo stato dei messaggi e degli account nel database
"""

import asyncio
import aiosqlite
import time
from datetime import datetime, timezone
import sys

DB_PATH = "/tmp/mail_service.db"

async def diagnose():
    print("=" * 80)
    print("DIAGNOSTICA genro-mail-proxy")
    print("=" * 80)
    print(f"\nDatabase: {DB_PATH}\n")

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # 1. Conta messaggi totali
            print("📊 STATO MESSAGGI")
            print("-" * 80)

            async with db.execute("SELECT COUNT(*) FROM messages") as cur:
                total = (await cur.fetchone())[0]
                print(f"Messaggi totali:        {total}")

            # 2. Conta per stato
            async with db.execute("SELECT COUNT(*) FROM messages WHERE sent_ts IS NULL AND error_ts IS NULL") as cur:
                pending = (await cur.fetchone())[0]
                print(f"Messaggi pending:       {pending} (non spediti, no errori)")

            async with db.execute("SELECT COUNT(*) FROM messages WHERE sent_ts IS NOT NULL") as cur:
                sent = (await cur.fetchone())[0]
                print(f"Messaggi inviati:       {sent}")

            async with db.execute("SELECT COUNT(*) FROM messages WHERE error_ts IS NOT NULL") as cur:
                errors = (await cur.fetchone())[0]
                print(f"Messaggi con errore:    {errors}")

            async with db.execute("SELECT COUNT(*) FROM messages WHERE deferred_ts IS NOT NULL") as cur:
                deferred = (await cur.fetchone())[0]
                print(f"Messaggi deferred:      {deferred}")

            # 3. Verifica timestamp deferred
            now_ts = int(time.time())
            print(f"\n⏰ CONTROLLO TIMESTAMP (now={now_ts})")
            print("-" * 80)

            async with db.execute("""
                SELECT id, deferred_ts, sent_ts, error_ts,
                       CASE
                         WHEN deferred_ts IS NULL THEN 'NULL'
                         WHEN deferred_ts <= ? THEN 'READY (past)'
                         ELSE 'FUTURE'
                       END as deferred_status
                FROM messages
                WHERE sent_ts IS NULL AND error_ts IS NULL
            """, (now_ts,)) as cur:
                rows = await cur.fetchall()
                if rows:
                    for row in rows:
                        msg_id, def_ts, sent_ts, err_ts, status = row
                        if def_ts:
                            def_dt = datetime.fromtimestamp(def_ts, timezone.utc)
                            print(f"  {msg_id[:30]:<30} deferred_ts={def_dt.isoformat()} [{status}]")
                        else:
                            print(f"  {msg_id[:30]:<30} deferred_ts=NULL [{status}]")
                else:
                    print("  Nessun messaggio pending trovato")

            # 4. Query ESATTA del loop SMTP
            print(f"\n🔍 SIMULAZIONE QUERY LOOP SMTP")
            print("-" * 80)
            print("Query: SELECT * FROM messages WHERE sent_ts IS NULL AND error_ts IS NULL")
            print(f"       AND (deferred_ts IS NULL OR deferred_ts <= {now_ts})")

            async with db.execute("""
                SELECT id, account_id, priority, deferred_ts, created_at
                FROM messages
                WHERE sent_ts IS NULL
                  AND error_ts IS NULL
                  AND (deferred_ts IS NULL OR deferred_ts <= ?)
                ORDER BY priority ASC, created_at ASC
                LIMIT 10
            """, (now_ts,)) as cur:
                rows = await cur.fetchall()
                if rows:
                    print(f"\n✅ TROVATI {len(rows)} messaggi pronti per invio:")
                    for row in rows:
                        msg_id, account_id, priority, def_ts, created_at = row
                        print(f"  - ID: {msg_id}")
                        print(f"    Account: {account_id or 'default'}")
                        print(f"    Priority: {priority}")
                        print(f"    Created: {created_at}")
                        print()
                else:
                    print("\n❌ NESSUN messaggio trovato dalla query loop SMTP!")
                    print("\nPossibili cause:")
                    print("  1. Tutti i messaggi hanno sent_ts o error_ts impostati")
                    print("  2. Tutti i messaggi hanno deferred_ts nel futuro")

            # 5. Controlla account SMTP
            print("\n🔐 ACCOUNT SMTP CONFIGURATI")
            print("-" * 80)

            async with db.execute("SELECT id, host, port, user FROM accounts") as cur:
                accounts = await cur.fetchall()
                if accounts:
                    for acc in accounts:
                        acc_id, host, port, user = acc
                        print(f"  ✓ {acc_id}: {host}:{port} (user={user or 'none'})")
                else:
                    print("  ⚠️  Nessun account configurato nel database")

            # 6. Dettaglio completo primo messaggio pending
            print("\n📋 DETTAGLIO PRIMO MESSAGGIO PENDING")
            print("-" * 80)

            async with db.execute("""
                SELECT id, account_id, priority, payload, deferred_ts, sent_ts, error_ts, error, created_at
                FROM messages
                WHERE sent_ts IS NULL AND error_ts IS NULL
                LIMIT 1
            """) as cur:
                row = await cur.fetchone()
                if row:
                    import json
                    msg_id, acc_id, prio, payload, def_ts, sent_ts, err_ts, err, created = row
                    print(f"ID:           {msg_id}")
                    print(f"Account ID:   {acc_id or '(default)'}")
                    print(f"Priority:     {prio}")
                    print(f"Created:      {created}")
                    print(f"Deferred TS:  {def_ts} {'(future)' if def_ts and def_ts > now_ts else '(past/null)'}")
                    print(f"Sent TS:      {sent_ts}")
                    print(f"Error TS:     {err_ts}")
                    print(f"Error:        {err}")

                    try:
                        payload_obj = json.loads(payload)
                        print(f"\nPayload:")
                        print(f"  From: {payload_obj.get('from')}")
                        print(f"  To:   {payload_obj.get('to')}")
                        print(f"  Subj: {payload_obj.get('subject')}")
                    except:
                        print(f"\nPayload (raw): {payload[:200]}...")
                else:
                    print("  Nessun messaggio pending")

            # 7. Check send_log per rate limiting
            print("\n📈 SEND LOG (ultimi invii)")
            print("-" * 80)

            async with db.execute("""
                SELECT account_id, timestamp, datetime(timestamp, 'unixepoch') as dt
                FROM send_log
                ORDER BY timestamp DESC
                LIMIT 5
            """) as cur:
                logs = await cur.fetchall()
                if logs:
                    for log in logs:
                        print(f"  {log[0]}: {log[2]}")
                else:
                    print("  Nessun invio registrato")

    except Exception as e:
        print(f"\n❌ ERRORE: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print("\n" + "=" * 80)
    print("FINE DIAGNOSTICA")
    print("=" * 80)
    return 0

if __name__ == "__main__":
    if len(sys.argv) > 1:
        DB_PATH = sys.argv[1]

    exit_code = asyncio.run(diagnose())
    sys.exit(exit_code)
