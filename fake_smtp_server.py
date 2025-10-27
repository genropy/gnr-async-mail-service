#!/usr/bin/env python3
"""
Fake SMTP server for testing.

This server accepts all emails and logs them without actually sending anything.
Perfect for testing the mail service without external dependencies.

Usage:
    python3 fake_smtp_server.py

The server will listen on localhost:1025 (SMTP without TLS)
"""

import asyncio
import logging
from datetime import datetime
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPProtocol

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class FakeSMTPHandler:
    """Handler that accepts all emails and logs them."""

    def __init__(self):
        self.messages_received = 0

    async def handle_DATA(self, server, session, envelope):
        """Handle incoming email data."""
        self.messages_received += 1

        logger.info("=" * 80)
        logger.info(f"📧 Message #{self.messages_received} received")
        logger.info("-" * 80)
        logger.info(f"From: {envelope.mail_from}")
        logger.info(f"To: {', '.join(envelope.rcpt_tos)}")
        logger.info(f"Size: {len(envelope.content)} bytes")
        logger.info("-" * 80)

        # Decode and log the content (first 500 chars)
        try:
            content = envelope.content.decode('utf-8', errors='replace')
            # Extract subject if present
            for line in content.split('\n')[:20]:
                if line.lower().startswith('subject:'):
                    logger.info(f"Subject: {line[8:].strip()}")
                    break

            # Log first part of body
            logger.info("Content preview:")
            preview = content[:500]
            if len(content) > 500:
                preview += "..."
            logger.info(preview)
        except Exception as e:
            logger.warning(f"Could not decode content: {e}")

        logger.info("=" * 80)
        logger.info(f"✅ Message accepted (total received: {self.messages_received})")
        logger.info("")

        # Return success
        return '250 Message accepted for delivery'


class CustomController(Controller):
    """Custom controller with better logging."""

    def __init__(self, handler, hostname='127.0.0.1', port=1025):
        super().__init__(handler, hostname=hostname, port=port)

    def factory(self):
        """Create SMTP protocol instance."""
        return SMTPProtocol(self.handler, enable_SMTPUTF8=True)


def main():
    """Start the fake SMTP server."""
    handler = FakeSMTPHandler()
    controller = CustomController(handler, hostname='127.0.0.1', port=1025)

    logger.info("🚀 Starting Fake SMTP Server")
    logger.info(f"   Host: 127.0.0.1")
    logger.info(f"   Port: 1025")
    logger.info(f"   Protocol: SMTP (no TLS)")
    logger.info("")
    logger.info("This server accepts all emails without authentication.")
    logger.info("Perfect for testing genro-mail-proxy!")
    logger.info("")
    logger.info("Press Ctrl+C to stop")
    logger.info("-" * 80)
    logger.info("")

    controller.start()

    try:
        # Keep running
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        logger.info("")
        logger.info("-" * 80)
        logger.info("Shutting down...")
        logger.info(f"Total messages received: {handler.messages_received}")
        controller.stop()


if __name__ == "__main__":
    main()
