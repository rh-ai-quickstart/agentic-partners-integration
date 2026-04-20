"""Tests for request normalizer."""

from request_manager.normalizer import RequestNormalizer
from request_manager.schemas import WebRequest
from shared_models.models import IntegrationType


class TestRequestNormalizer:
    """Test cases for RequestNormalizer."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.normalizer = RequestNormalizer()
        self.session_id = "test-session-123"

    def test_normalize_web_request(self) -> None:
        """Test web request normalization."""
        web_request = WebRequest(
            user_id="webuser123",
            content="I want to refresh my laptop",
            session_token="token123",
            client_ip="192.168.1.1",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        )

        normalized = self.normalizer.normalize_request(web_request, self.session_id)

        assert normalized.integration_type == IntegrationType.WEB
        assert normalized.integration_context["platform"] == "web"
        assert normalized.integration_context["client_ip"] == "192.168.1.1"
        assert normalized.user_context["browser"] == "chrome"
        assert normalized.user_context["os"] == "windows"
        assert normalized.user_context["is_mobile"] is False

    def test_user_agent_parsing(self) -> None:
        """Test user agent parsing."""
        test_cases = [
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                {"browser": "chrome", "os": "windows", "is_mobile": False},
            ),
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
                {"browser": "safari", "os": "macos", "is_mobile": False},
            ),
            (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
                {"browser": "safari", "os": "ios", "is_mobile": True},
            ),
        ]

        for user_agent, expected in test_cases:
            result = self.normalizer._parse_user_agent(user_agent)
            for key, value in expected.items():
                assert result[key] == value


# ---------------------------------------------------------------------------
# _normalize_base_request
# ---------------------------------------------------------------------------


class TestNormalizeBaseRequest:
    """Tests for _normalize_base_request (non-web request) - lines 67-82."""

    def setup_method(self) -> None:
        self.normalizer = RequestNormalizer()
        self.session_id = "test-session-base"

    def test_normalize_base_request(self) -> None:
        """Test base request normalization produces correct integration_context."""
        from request_manager.schemas import BaseRequest

        base_request = BaseRequest(
            user_id="baseuser123",
            content="Help me with networking",
            integration_type=IntegrationType.WEB,
            metadata={"key": "value"},
        )

        normalized = self.normalizer.normalize_request(base_request, self.session_id)

        assert normalized.session_id == self.session_id
        assert normalized.user_id == "baseuser123"
        assert normalized.content == "Help me with networking"
        assert normalized.integration_context["platform"] == "WEB"
        assert normalized.integration_context["metadata"] == {"key": "value"}
        assert normalized.user_context == {}
        assert normalized.requires_routing is True

    def test_normalize_base_request_empty_metadata(self) -> None:
        """Base request with empty metadata."""
        from request_manager.schemas import BaseRequest

        base_request = BaseRequest(
            user_id="user456",
            content="Simple question",
            integration_type=IntegrationType.WEB,
        )

        normalized = self.normalizer.normalize_request(base_request, self.session_id)

        assert normalized.integration_context["metadata"] == {}


# ---------------------------------------------------------------------------
# _extract_web_user_context - missing user_agent
# ---------------------------------------------------------------------------


class TestExtractWebUserContext:
    """Tests for _extract_web_user_context - lines 84-98."""

    def setup_method(self) -> None:
        self.normalizer = RequestNormalizer()

    def test_no_user_agent(self) -> None:
        """When user_agent is None, skip user agent parsing (line 92-93)."""
        from request_manager.schemas import WebRequest

        web_request = WebRequest(
            user_id="user-no-ua",
            content="test",
            session_token="tok",
            client_ip="10.0.0.1",
            user_agent=None,
        )

        context = self.normalizer._extract_web_user_context(web_request)

        assert context["client_ip"] == "10.0.0.1"
        assert context["user_agent"] is None
        assert context["has_session"] is True
        # No browser/os keys since user_agent is None
        assert "browser" not in context
        assert "os" not in context

    def test_with_metadata(self) -> None:
        """When metadata is present, it should be merged into context (lines 95-96)."""
        from request_manager.schemas import WebRequest

        web_request = WebRequest(
            user_id="user-meta",
            content="test",
            session_token=None,
            client_ip="10.0.0.2",
            user_agent="Mozilla/5.0 Chrome",
            metadata={"theme": "dark", "lang": "en"},
        )

        context = self.normalizer._extract_web_user_context(web_request)

        assert context["has_session"] is False
        assert context["theme"] == "dark"
        assert context["lang"] == "en"
        assert context["browser"] == "chrome"


# ---------------------------------------------------------------------------
# _parse_user_agent - Firefox, Edge, Linux, Android
# ---------------------------------------------------------------------------


class TestParseUserAgentExtended:
    """Extended tests for _parse_user_agent - lines 109, 112-113, 124, 127-128."""

    def setup_method(self) -> None:
        self.normalizer = RequestNormalizer()

    def test_firefox_browser(self) -> None:
        """Detect Firefox browser (line 109)."""
        result = self.normalizer._parse_user_agent(
            "Mozilla/5.0 (X11; Linux x86_64; rv:89.0) Gecko/20100101 Firefox/89.0"
        )
        assert result["browser"] == "firefox"
        assert result["os"] == "linux"
        assert result["is_mobile"] is False

    def test_edge_browser(self) -> None:
        """Detect Edge browser (lines 112-113)."""
        result = self.normalizer._parse_user_agent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Edge/91.0.864.59"
        )
        assert result["browser"] == "edge"
        assert result["os"] == "windows"

    def test_linux_os(self) -> None:
        """Detect Linux OS (lines 127-128)."""
        result = self.normalizer._parse_user_agent(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0"
        )
        assert result["os"] == "linux"
        assert result["browser"] == "chrome"

    def test_android_os(self) -> None:
        """Detect Android OS (line 124)."""
        result = self.normalizer._parse_user_agent(
            "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 Mobile Chrome/91.0"
        )
        assert result["os"] == "android"
        assert result["is_mobile"] is True

    def test_ipad_detection(self) -> None:
        """Detect iPad as iOS and mobile (lines 118-121)."""
        result = self.normalizer._parse_user_agent(
            "Mozilla/5.0 (iPad; CPU OS 14_6 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
        )
        assert result["os"] == "ios"
        assert result["is_mobile"] is True
        assert result["browser"] == "safari"

    def test_unknown_browser_and_os(self) -> None:
        """Unknown user agent has no browser or os keys."""
        result = self.normalizer._parse_user_agent("CustomBot/1.0")
        assert "browser" not in result
        assert "os" not in result
        assert result["is_mobile"] is False
        assert result["raw_user_agent"] == "CustomBot/1.0"


# ---------------------------------------------------------------------------
# normalize_request with target_agent in metadata
# ---------------------------------------------------------------------------


class TestNormalizeRequestTargetAgent:
    """Tests for normalize_request with target_agent - line 30."""

    def setup_method(self) -> None:
        self.normalizer = RequestNormalizer()
        self.session_id = "test-session-target"

    def test_target_agent_extracted_from_metadata(self) -> None:
        """target_agent_id is extracted from metadata when present (line 30)."""
        from request_manager.schemas import WebRequest

        web_request = WebRequest(
            user_id="user-target",
            content="route me",
            metadata={"target_agent": "software-support"},
        )

        normalized = self.normalizer.normalize_request(web_request, self.session_id)

        assert normalized.target_agent_id == "software-support"

    def test_no_target_agent_when_not_in_metadata(self) -> None:
        """target_agent_id is None when not in metadata."""
        from request_manager.schemas import WebRequest

        web_request = WebRequest(
            user_id="user-no-target",
            content="no routing",
            metadata={"other_key": "other_value"},
        )

        normalized = self.normalizer.normalize_request(web_request, self.session_id)

        assert normalized.target_agent_id is None

    def test_no_target_agent_when_metadata_is_empty(self) -> None:
        """target_agent_id is None when metadata is empty dict."""
        from request_manager.schemas import WebRequest

        web_request = WebRequest(
            user_id="user-empty-meta",
            content="test",
        )

        normalized = self.normalizer.normalize_request(web_request, self.session_id)

        assert normalized.target_agent_id is None

    def test_current_agent_id_passed_through(self) -> None:
        """current_agent_id parameter is passed but not used for target_agent_id."""
        from request_manager.schemas import WebRequest

        web_request = WebRequest(
            user_id="user-agent-id",
            content="test with agent",
        )

        normalized = self.normalizer.normalize_request(
            web_request, self.session_id, current_agent_id="current-agent"
        )

        # target_agent_id comes from metadata, not current_agent_id
        assert normalized.target_agent_id is None
