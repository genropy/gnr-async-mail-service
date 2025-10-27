#!/bin/bash
# Setup clean test environment for genro-mail-proxy

set -e

echo "🧹 Cleaning up test environment..."

# Stop all services
pkill -9 -f "python3 main.py" 2>/dev/null || true
pkill -9 -f "python3 example_client.py" 2>/dev/null || true

# Remove old test database
rm -f /tmp/mail_service_test.db
rm -f /tmp/mail_service_test.db-shm
rm -f /tmp/mail_service_test.db-wal
rm -f example_client.db

# Clear logs
rm -f /tmp/mail_service.log
rm -f /tmp/example_client.log

echo "✅ Cleanup complete"
echo ""
echo "📝 Creating test configuration..."

# Update config.ini to use test database
cat > config_test.ini << 'EOF'
[storage]
db_path = /tmp/mail_service_test.db

[server]
# host = 0.0.0.0
# port = 8000
# api_token = your-api-token

[client]
# Endpoint invoked by the dispatcher to deliver batched reports
client_sync_url = http://127.0.0.1:9090/email/mailproxy/mp_endpoint/proxy_sync
client_sync_user = syncuser
client_sync_password = syncpass

[scheduler]
active = true
timezone = Europe/Rome
rules = [{"name": "default", "enabled": true, "interval_minutes": 5}]

[delivery]
# Fast polling for testing
send_interval_seconds = 0.5
test_mode = false
default_priority = 2
delivery_report_retention_seconds = 604800

[logging]
delivery_activity = true
EOF

echo "✅ Test configuration created: config_test.ini"
echo ""
echo "🚀 Starting services..."

# Start mail service with test config and DEBUG logging
echo "   Starting mail service..."
bash run_test.sh > /tmp/mail_service.log 2>&1 &
MAIL_PID=$!

sleep 3

# Check if mail service started
if ! curl -s http://localhost:8000/status > /dev/null 2>&1; then
    echo "❌ Mail service failed to start"
    cat /tmp/mail_service.log
    exit 1
fi

echo "   ✅ Mail service started (PID: $MAIL_PID)"

# Create fake SMTP account (no authentication required for fake server)
echo "   Creating fake SMTP account..."
curl -X POST http://localhost:8000/account \
  -H "X-API-Token: your-secret-token" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "fake-smtp-test",
    "host": "127.0.0.1",
    "port": 1025,
    "user": null,
    "password": null,
    "use_tls": false
  }' > /dev/null 2>&1

echo "   ✅ Fake SMTP account created"

# Start example client
echo "   Starting example client..."
python3 example_client.py > /tmp/example_client.log 2>&1 &
CLIENT_PID=$!

sleep 2

# Check if example client started
if ! curl -s http://localhost:9090/ > /dev/null 2>&1; then
    echo "❌ Example client failed to start"
    cat /tmp/example_client.log
    exit 1
fi

echo "   ✅ Example client started (PID: $CLIENT_PID)"
echo ""
echo "✅ Test environment ready!"
echo ""
echo "📊 Service status:"
echo "   Mail service:    http://localhost:8000 (PID: $MAIL_PID)"
echo "   Example client:  http://localhost:9090 (PID: $CLIENT_PID)"
echo "   Fake SMTP:       127.0.0.1:1025"
echo "   Test database:   /tmp/mail_service_test.db"
echo ""
echo "🧪 Try sending a test email:"
echo "   curl -X POST http://localhost:9090/send-test-email"
echo ""
echo "📋 Monitor logs:"
echo "   tail -f /tmp/mail_service.log"
echo "   tail -f /tmp/fake_smtp.log"
echo ""
