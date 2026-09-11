#!/bin/bash
# Start SPIRE auto-registration daemon
# Called by: scripts/MAIN-SETUP.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo "════════════════════════════════════════════════════════════"
echo "STARTING SPIRE AUTO-REGISTRATION"
echo "════════════════════════════════════════════════════════════"
echo ""

# Check if daemon is already running
DAEMON_PID=$(ps aux | grep "[s]pire-auto-register-daemon" | awk '{print $2}')
if [ -n "$DAEMON_PID" ]; then
    echo "  ✓ SPIRE daemon already running (PID: $DAEMON_PID)"
    echo ""
    echo "════════════════════════════════════════════════════════════"
    echo "✓ SPIRE AUTO-REGISTRATION READY"
    echo "════════════════════════════════════════════════════════════"
    exit 0
fi

# Start daemon
if [ -f "$PROJECT_ROOT/scripts/start-spire-auto-register.sh" ]; then
    bash "$PROJECT_ROOT/scripts/start-spire-auto-register.sh" > /dev/null 2>&1

    # Verify it started
    sleep 1
    DAEMON_PID=$(ps aux | grep "[s]pire-auto-register-daemon" | awk '{print $2}')
    if [ -n "$DAEMON_PID" ]; then
        echo "  ✓ SPIRE daemon started (PID: $DAEMON_PID)"
    else
        echo "  ✗ Failed to start SPIRE daemon" >&2
        exit 1
    fi
else
    echo "  ✗ start-spire-auto-register.sh not found" >&2
    exit 1
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "✓ SPIRE AUTO-REGISTRATION STARTED"
echo "════════════════════════════════════════════════════════════"
