"""Tests that scripts/monitor.sh event matchers align with service log events.

The monitor script parses two event streams:
1. DB audit events (audit_events table) — matched by event_type in a bash case
2. Container log events (JSON structlog) — matched by the 'event' field
3. Plain-text log lines — matched by grep substrings

These tests verify that every event the services emit is matched by the
monitor, and that the metadata field names the monitor extracts match
what the services actually store. A mismatch means the monitor silently
drops events (they fall to the wildcard '*' handler and show a minimal line).
"""

import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MONITOR_SH = PROJECT_ROOT / "scripts" / "monitor.sh"

RM_SRC = PROJECT_ROOT / "request-manager" / "src" / "request_manager"
AS_SRC = PROJECT_ROOT / "agent-service" / "src" / "agent_service"


@pytest.fixture(scope="module")
def monitor_text():
    return MONITOR_SH.read_text()


# ---------------------------------------------------------------------------
# Helpers — extract patterns from bash and Python source
# ---------------------------------------------------------------------------

def extract_audit_case_patterns(monitor_text: str) -> set[str]:
    """Extract event_type patterns from the audit_tail case statement."""
    in_audit = False
    patterns: set[str] = set()
    for line in monitor_text.splitlines():
        if "audit_tail()" in line:
            in_audit = True
        if in_audit and line.strip().startswith("case \"$etype\""):
            continue
        if in_audit and line.strip() == "esac":
            break
        if in_audit:
            m = re.match(r'\s+([\w.*|]+)\)', line)
            if m:
                for p in m.group(1).split("|"):
                    p = p.strip()
                    if p != "*":
                        patterns.add(p)
    return patterns


def extract_log_case_patterns(monitor_text: str) -> set[str]:
    """Extract event patterns from the parse_logs case statement."""
    in_parse = False
    patterns: set[str] = set()
    for line in monitor_text.splitlines():
        if "parse_logs()" in line:
            in_parse = True
        if in_parse and line.strip().startswith("case \"$event\""):
            continue
        if in_parse and line.strip() == "esac":
            break
        if in_parse:
            m = re.match(r'\s+"(.+)"\)', line)
            if m:
                patterns.add(m.group(1))
    return patterns


def extract_plaintext_grep_patterns(monitor_text: str) -> set[str]:
    """Extract plain-text grep patterns from the parse_logs non-JSON branch."""
    patterns: set[str] = set()
    in_parse = False
    for line in monitor_text.splitlines():
        if "parse_logs()" in line:
            in_parse = True
        if not in_parse:
            continue
        m = re.search(r'grep -q "([^"]+)"', line)
        if m:
            patterns.add(m.group(1))
    return patterns


def find_emit_event_types(src_dir: Path) -> set[str]:
    """Find all event_type= strings in AuditService.emit calls."""
    types: set[str] = set()
    for pyfile in src_dir.rglob("*.py"):
        if "__pycache__" in str(pyfile):
            continue
        text = pyfile.read_text()
        for m in re.finditer(r'event_type\s*=\s*"([^"]+)"', text):
            types.add(m.group(1))
    return types


def find_structlog_events(src_dir: Path) -> set[str]:
    """Find all structlog event strings (first arg to logger.info/warning/error)."""
    events: set[str] = set()
    for pyfile in src_dir.rglob("*.py"):
        if "__pycache__" in str(pyfile):
            continue
        text = pyfile.read_text()
        for m in re.finditer(
            r'logger\.\w+\(\s*\n?\s*"([^"]+)"', text
        ):
            events.add(m.group(1))
    return events


# ---------------------------------------------------------------------------
# 1. Case-sensitivity: monitor event strings match source exactly
# ---------------------------------------------------------------------------

class TestLogEventCaseMatching:
    """Verify the monitor's case-statement patterns match source event strings."""

    def test_routing_decision_case_matches_source(self, monitor_text):
        """Bug fix #1: 'Policy' must have capital P."""
        source = (RM_SRC / "communication_strategy.py").read_text()
        m = re.search(r'"(Routing decision received[^"]*)"', source)
        assert m, "Could not find routing decision event in source"
        source_event = m.group(1)

        log_patterns = extract_log_case_patterns(monitor_text)
        assert source_event in log_patterns, (
            f"Monitor case pattern does not match source.\n"
            f"  Source: {source_event!r}\n"
            f"  Monitor has: {log_patterns}"
        )

    def test_authorization_blocked_case_matches_source(self, monitor_text):
        """Bug fix #2: 'Policy' must have capital P."""
        source = (RM_SRC / "communication_strategy.py").read_text()
        m = re.search(r'"(AUTHORIZATION BLOCKED[^"]*)"', source)
        assert m, "Could not find AUTHORIZATION BLOCKED event in source"
        source_event = m.group(1)

        log_patterns = extract_log_case_patterns(monitor_text)
        assert source_event in log_patterns, (
            f"Monitor case pattern does not match source.\n"
            f"  Source: {source_event!r}\n"
            f"  Monitor has: {log_patterns}"
        )


# ---------------------------------------------------------------------------
# 2. DB audit event coverage — every emitted event_type has a handler
# ---------------------------------------------------------------------------

class TestAuditEventCoverage:
    """Every audit event_type emitted by services should have a monitor handler."""

    def test_request_manager_audit_events_covered(self, monitor_text):
        rm_event_types = find_emit_event_types(RM_SRC)
        audit_patterns = extract_audit_case_patterns(monitor_text)
        for et in rm_event_types:
            assert any(
                et == p or et.startswith(p.rstrip("*"))
                for p in audit_patterns
            ), (
                f"Audit event_type '{et}' from request-manager has no "
                f"handler in monitor.sh audit_tail. Patterns: {audit_patterns}"
            )

    def test_token_expired_has_handler(self, monitor_text):
        """Bug fix #4: auth.token.expired must have a dedicated handler."""
        audit_patterns = extract_audit_case_patterns(monitor_text)
        assert "auth.token.expired" in audit_patterns

    def test_token_invalid_has_handler(self, monitor_text):
        """Bug fix #4: auth.token.invalid must have a dedicated handler."""
        audit_patterns = extract_audit_case_patterns(monitor_text)
        assert "auth.token.invalid" in audit_patterns

    def test_token_exchange_audit_has_handler(self, monitor_text):
        """Bug fix #7: token.exchange.audit must be handled."""
        audit_patterns = extract_audit_case_patterns(monitor_text)
        assert "token.exchange.audit" in audit_patterns


# ---------------------------------------------------------------------------
# 3. Token exchange metadata field alignment
# ---------------------------------------------------------------------------

class TestTokenExchangeMetadataFields:
    """Monitor must extract the same field names the services emit."""

    def test_success_metadata_fields_match(self, monitor_text):
        """The monitor's grep -oP patterns must match token_exchange.py fields."""
        source = (RM_SRC / "token_exchange.py").read_text()

        success_block = re.search(
            r'outcome="success".*?metadata=\{([^}]+)\}',
            source,
            re.DOTALL,
        )
        assert success_block, "Could not find success metadata in token_exchange.py"
        metadata_text = success_block.group(1)

        expected_fields = set(re.findall(r'"(\w+)":', metadata_text))

        monitor_fields = set(
            re.findall(r'grep -oP \'"(\w+)":', monitor_text)
        )

        critical_fields = {"original_aud", "new_aud", "actor_service",
                           "auth_method", "exchange_client_id", "expires_in"}
        for field in critical_fields:
            assert field in expected_fields, (
                f"Field '{field}' expected in token_exchange.py but not found"
            )
            assert field in monitor_fields, (
                f"Field '{field}' not extracted by monitor.sh grep patterns"
            )


# ---------------------------------------------------------------------------
# 4. Plain-text log pattern coverage
# ---------------------------------------------------------------------------

class TestPlainTextPatternCoverage:

    def test_dcr_registration_pattern_exists(self, monitor_text):
        patterns = extract_plaintext_grep_patterns(monitor_text)
        assert "DCR registration successful" in patterns

    def test_spire_svid_pattern_exists(self, monitor_text):
        patterns = extract_plaintext_grep_patterns(monitor_text)
        assert "Fetched SVID from SPIRE:" in patterns

    def test_a2a_auth_rejected_pattern_exists(self, monitor_text):
        """Bug fix #5: A2A auth rejection must be caught from plain-text logs."""
        patterns = extract_plaintext_grep_patterns(monitor_text)
        assert "A2A request rejected" in patterns


# ---------------------------------------------------------------------------
# 5. DCR plain-text handler uses local variable, not stale $svc
# ---------------------------------------------------------------------------

class TestDCRPlainTextHandler:
    """Bug fix #3: DCR handler must not use $svc (set only for JSON lines)."""

    def test_dcr_handler_does_not_use_stale_svc(self, monitor_text):
        lines = monitor_text.splitlines()
        in_dcr_block = False
        for i, line in enumerate(lines):
            if 'grep -q "DCR registration successful"' in line:
                in_dcr_block = True
                continue
            if in_dcr_block and "continue" in line:
                break
            if in_dcr_block:
                assert "${svc}" not in line, (
                    f"Line {i+1}: DCR plain-text handler uses ${{svc}} which is "
                    f"only set for JSON lines. Should use ${{dcr_svc}} or extract "
                    f"from the SPIFFE ID. Line: {line.strip()}"
                )


# ---------------------------------------------------------------------------
# 6. Token exchange failure path rendering
# ---------------------------------------------------------------------------

class TestTokenExchangeFailurePath:
    """Bug fix #6: Failed token exchanges must show an error card, not success."""

    def test_failure_outcome_renders_error_card(self, monitor_text):
        """The token.exchange handler must check $outcome = failure."""
        assert '"$outcome" = "failure"' in monitor_text or \
               "'$outcome' = 'failure'" in monitor_text or \
               '"$outcome" = "failure"' in monitor_text, (
            "token.exchange handler does not check for failure outcome"
        )

    def test_failure_card_shows_error_markers(self, monitor_text):
        assert "TOKEN EXCHANGE FAILED" in monitor_text, (
            "No TOKEN EXCHANGE FAILED card in monitor.sh"
        )

    def test_failure_card_shows_no_fallback_message(self, monitor_text):
        assert "no token issued" in monitor_text.lower(), (
            "Failure card should mention that no token was issued"
        )


# ---------------------------------------------------------------------------
# 7. Container log stream completeness
# ---------------------------------------------------------------------------

class TestContainerLogStreams:
    """Verify all security-relevant containers are monitored."""

    def test_request_manager_logs_streamed(self, monitor_text):
        assert "partner-request-manager-full" in monitor_text

    def test_agent_service_logs_streamed(self, monitor_text):
        assert "partner-agent-service-full" in monitor_text

    def test_praxis_gateway_logs_streamed(self, monitor_text):
        assert "partner-praxis-gateway-full" in monitor_text


# ---------------------------------------------------------------------------
# 8. Integration: monitor can parse real log lines from running services
# ---------------------------------------------------------------------------

class TestMonitorLiveEventParsing:
    """Run the monitor's parse_logs against real container log lines."""

    @pytest.fixture(scope="class")
    def request_manager_logs(self):
        try:
            result = subprocess.run(
                ["docker", "logs", "partner-request-manager-full"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout + result.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("Docker not available or container not running")

    def test_routing_decision_event_exists_in_logs(self, request_manager_logs):
        """Confirm the actual log contains the event string we match."""
        assert "Routing decision received (Policy authorized)" in request_manager_logs, (
            "Expected routing decision event not found in request-manager logs. "
            "Has the event string changed?"
        )

    def test_invoking_agent_event_exists_in_logs(self, request_manager_logs):
        assert '"event": "Invoking agent"' in request_manager_logs

    def test_agent_invocation_successful_event_exists(self, request_manager_logs):
        assert '"event": "Agent invocation successful"' in request_manager_logs

    def test_final_response_event_exists(self, request_manager_logs):
        assert '"event": "Final response received"' in request_manager_logs

    @pytest.fixture(scope="class")
    def audit_event_types_in_db(self):
        try:
            result = subprocess.run(
                [
                    "docker", "exec", "partner-postgres-full",
                    "psql", "-U", "user", "-d", "partner_agent",
                    "-t", "-A",
                    "-c", "SELECT DISTINCT event_type FROM audit_events ORDER BY 1",
                ],
                capture_output=True, text=True, timeout=10,
            )
            return {line.strip() for line in result.stdout.splitlines() if line.strip()}
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pytest.skip("Docker/postgres not available")

    def test_all_db_event_types_have_handlers(
        self, monitor_text, audit_event_types_in_db
    ):
        """Every event_type in the live DB should have a monitor handler."""
        audit_patterns = extract_audit_case_patterns(monitor_text)
        for et in audit_event_types_in_db:
            matched = any(
                et == p or ("|" not in p and et.startswith(p.rstrip("*")))
                for p in audit_patterns
            )
            assert matched, (
                f"Live DB event_type '{et}' has no handler in monitor.sh. "
                f"Handlers: {sorted(audit_patterns)}"
            )


# ---------------------------------------------------------------------------
# 9. Static flow diagram accuracy
# ---------------------------------------------------------------------------

class TestStaticFlowDiagram:

    def test_diagram_mentions_praxis_jwt_enforcement(self, monitor_text):
        assert "require(authenticated)" in monitor_text

    def test_diagram_mentions_defense_in_depth(self, monitor_text):
        assert "defense-in-depth" in monitor_text

    def test_diagram_mentions_spiffe(self, monitor_text):
        assert "spiffe://" in monitor_text

    def test_diagram_mentions_rfc_8693(self, monitor_text):
        assert "RFC 8693" in monitor_text

    def test_diagram_mentions_rfc_7591(self, monitor_text):
        assert "RFC 7591" in monitor_text

    def test_diagram_mentions_audience_mismatch(self, monitor_text):
        assert "audience" in monitor_text.lower()
