#!/bin/bash
# SPIRE Auto-Registration Daemon
#
# ⚠️  DEPRECATED: This daemon is no longer needed with docker label attestation.
# Use scripts/register-workloads.sh for one-time registration instead.
#
# This daemon was designed for dynamic registration but creates duplicate entries
# with each container restart. The new design uses persistent docker label selectors
# that survive restarts, eliminating the need for continuous monitoring.
#
# Monitors SPIRE server for new agents and automatically registers workloads
# Run this in background during environment setup

set -e

SPIRE_SERVER="${SPIRE_SERVER_CONTAINER:-partner-spire-server}"
CHECK_INTERVAL="${CHECK_INTERVAL:-5}"
STATE_FILE="/tmp/spire-auto-register-state.txt"

# Workload definitions: service_name|selector
# NOTE: These should be docker:label selectors, not unix:uid selectors
WORKLOADS=(
    "request-manager|docker:label:com.docker.compose.service:request-manager"
    "agent-service|docker:label:com.docker.compose.service:agent-service"
    "kubernetes-agent|docker:label:com.docker.compose.service:kubernetes-agent"
)

echo "═══════════════════════════════════════════════════════════"
echo "SPIRE AUTO-REGISTRATION DAEMON STARTED"
echo "═══════════════════════════════════════════════════════════"
echo "Monitoring: $SPIRE_SERVER"
echo "Check interval: ${CHECK_INTERVAL}s"
echo "Workloads: ${#WORKLOADS[@]} services"
echo ""

# Initialize state file
touch "$STATE_FILE"

while true; do
    # Get all current agents
    AGENTS=$(docker exec "$SPIRE_SERVER" /opt/spire/bin/spire-server agent list 2>/dev/null | \
        grep "SPIFFE ID" | awk '{print $3}' || echo "")

    if [ -z "$AGENTS" ]; then
        sleep "$CHECK_INTERVAL"
        continue
    fi

    # Process each agent
    while IFS= read -r agent_id; do
        [ -z "$agent_id" ] && continue

        # Check each workload
        for workload_def in "${WORKLOADS[@]}"; do
            IFS='|' read -r service_name selector <<< "$workload_def"

            spiffe_id="spiffe://partner.example.com/service/${service_name}"
            state_key="${agent_id}__${service_name}"

            # Check if already registered
            if grep -q "$state_key" "$STATE_FILE" 2>/dev/null; then
                continue
            fi

            # Check if entry exists
            EXISTING=$(docker exec "$SPIRE_SERVER" \
                /opt/spire/bin/spire-server entry show \
                -spiffeID "$spiffe_id" \
                -parentID "$agent_id" 2>/dev/null | grep "Entry ID" || echo "")

            if [ -n "$EXISTING" ]; then
                echo "$state_key" >> "$STATE_FILE"
                continue
            fi

            # Register workload
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] AUTO-REGISTERING:"
            echo "  Service: $service_name"
            echo "  Parent: ${agent_id:0:50}..."
            echo "  Selector: $selector"

            docker exec "$SPIRE_SERVER" \
                /opt/spire/bin/spire-server entry create \
                -spiffeID "$spiffe_id" \
                -parentID "$agent_id" \
                -selector "$selector" 2>&1 | grep -E "Entry ID|Created" || true

            echo "$state_key" >> "$STATE_FILE"
            echo "  ✓ Registered"
            echo ""
        done
    done <<< "$AGENTS"

    sleep "$CHECK_INTERVAL"
done
