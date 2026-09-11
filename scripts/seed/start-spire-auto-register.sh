#!/bin/bash
# Start SPIRE auto-registration daemon in background
# This should be called during environment setup

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Starting SPIRE auto-registration daemon..."

# Kill any existing daemon
pkill -f "spire-auto-register-daemon.sh" 2>/dev/null || true

# Start daemon in background
nohup bash "$SCRIPT_DIR/spire-auto-register-daemon.sh" > /tmp/spire-auto-register.log 2>&1 &

DAEMON_PID=$!

echo "✓ SPIRE auto-registration daemon started (PID: $DAEMON_PID)"
echo "  Logs: /tmp/spire-auto-register.log"
echo ""
echo "To stop: pkill -f spire-auto-register-daemon.sh"
echo "To view logs: tail -f /tmp/spire-auto-register.log"
