#!/usr/bin/env python3
"""
Test manuale del dispatch di un messaggio per vedere dove fallisce
"""

import asyncio
import aiosqlite
import json
import sys
sys.path.insert(0, '/Users/fporcari/Development/genro-ng/genro-mail-proxy')

from async_mail_service.core import AsyncMailCore
from async_mail_service.persistence import Persistence

async def test():
    print("🧪 Test manuale dispatch messaggio\n")

    # Crea core con config minima
    core = AsyncMailCore(
        db_path="/tmp/mail_service.db",
        start_active=False,
        test_mode=True,  # Non avvia loop automatici
        log_delivery_activity=True
    )

    await core.init()

    print("✅ Core inizializzato\n")

    # Fetch un messaggio pronto
    now_ts = core._utc_now_epoch()
    print(f"🕐 Now timestamp: {now_ts}\n")

    messages = await core.persistence.fetch_ready_messages(limit=1, now_ts=now_ts)

    if not messages:
        print("❌ Nessun messaggio trovato!")
        return

    print(f"✅ Trovato messaggio: {messages[0]['id']}\n")
    print(f"📋 Dettagli:")
    print(f"   Account ID: {messages[0].get('account_id')}")
    print(f"   Priority: {messages[0].get('priority')}")
    print(f"   Message: {json.dumps(messages[0].get('message'), indent=2)}")
    print()

    # Prova dispatch
    print("🚀 Tento dispatch...\n")

    try:
        await core._dispatch_message(messages[0], now_ts)
        print("\n✅ Dispatch completato senza eccezioni!")

        # Ricontrolla stato messaggio
        async with aiosqlite.connect("/tmp/mail_service.db") as db:
            async with db.execute("""
                SELECT sent_ts, error_ts, error, deferred_ts
                FROM messages
                WHERE id = ?
            """, (messages[0]['id'],)) as cur:
                row = await cur.fetchone()
                sent, err_ts, err, def_ts = row
                print(f"\n📊 Stato messaggio dopo dispatch:")
                print(f"   sent_ts: {sent}")
                print(f"   error_ts: {err_ts}")
                print(f"   error: {err}")
                print(f"   deferred_ts: {def_ts}")

                if sent:
                    print("\n🎉 MESSAGGIO INVIATO CON SUCCESSO!")
                elif err_ts:
                    print(f"\n❌ MESSAGGIO IN ERRORE: {err}")
                elif def_ts:
                    print(f"\n⏸️  MESSAGGIO DEFERITO FINO A: {def_ts}")
                else:
                    print("\n⚠️  STATO SCONOSCIUTO!")

    except Exception as exc:
        print(f"\n💥 ECCEZIONE DURANTE DISPATCH:")
        print(f"   Tipo: {type(exc).__name__}")
        print(f"   Messaggio: {exc}")
        import traceback
        print(f"\n📚 Stack trace:")
        traceback.print_exc()

    await core.stop()

if __name__ == "__main__":
    asyncio.run(test())
