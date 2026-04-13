"""Tests for shared_models.utils module."""

from enum import Enum

from shared_models.models import IntegrationType, SessionStatus
from shared_models.utils import get_enum_value


class TestGetEnumValue:
    """Tests for get_enum_value()."""

    def test_with_integration_type_enum(self):
        """get_enum_value should extract .value from IntegrationType enum."""
        result = get_enum_value(IntegrationType.WEB)
        assert result == "WEB"

    def test_with_session_status_enum(self):
        """get_enum_value should extract .value from SessionStatus enum."""
        assert get_enum_value(SessionStatus.ACTIVE) == "ACTIVE"
        assert get_enum_value(SessionStatus.INACTIVE) == "INACTIVE"
        assert get_enum_value(SessionStatus.EXPIRED) == "EXPIRED"
        assert get_enum_value(SessionStatus.ARCHIVED) == "ARCHIVED"

    def test_with_plain_string(self):
        """get_enum_value should return string as-is when not an enum."""
        assert get_enum_value("WEB") == "WEB"
        assert get_enum_value("some_value") == "some_value"

    def test_with_integer(self):
        """get_enum_value should convert integers to string."""
        assert get_enum_value(42) == "42"
        assert get_enum_value(0) == "0"

    def test_with_none(self):
        """get_enum_value should convert None to string 'None'."""
        assert get_enum_value(None) == "None"

    def test_with_custom_enum(self):
        """get_enum_value should work with any enum that has .value."""

        class CustomEnum(Enum):
            FOO = "bar"

        assert get_enum_value(CustomEnum.FOO) == "bar"
