"""Tests for shared_models.base module."""

from shared_models.base import Base, TimestampMixin, naming_convention


class TestBase:
    """Tests for SQLAlchemy Base class."""

    def test_base_exists(self):
        """Base class should exist and be usable."""
        assert Base is not None

    def test_base_has_metadata(self):
        """Base class should have SQLAlchemy metadata."""
        assert hasattr(Base, "metadata")
        assert Base.metadata is not None


class TestTimestampMixin:
    """Tests for TimestampMixin."""

    def test_has_created_at_column(self):
        """TimestampMixin should have a created_at column."""
        assert hasattr(TimestampMixin, "created_at")

    def test_has_updated_at_column(self):
        """TimestampMixin should have an updated_at column."""
        assert hasattr(TimestampMixin, "updated_at")

    def test_created_at_is_not_nullable(self):
        """created_at column should be non-nullable."""
        assert TimestampMixin.created_at.nullable is False

    def test_updated_at_is_not_nullable(self):
        """updated_at column should be non-nullable."""
        assert TimestampMixin.updated_at.nullable is False


class TestNamingConvention:
    """Tests for naming_convention dict."""

    def test_has_index_convention(self):
        """naming_convention should have ix key for indexes."""
        assert "ix" in naming_convention

    def test_has_unique_convention(self):
        """naming_convention should have uq key for unique constraints."""
        assert "uq" in naming_convention

    def test_has_check_convention(self):
        """naming_convention should have ck key for check constraints."""
        assert "ck" in naming_convention

    def test_has_foreign_key_convention(self):
        """naming_convention should have fk key for foreign keys."""
        assert "fk" in naming_convention

    def test_has_primary_key_convention(self):
        """naming_convention should have pk key for primary keys."""
        assert "pk" in naming_convention

    def test_all_values_are_strings(self):
        """All naming convention values should be format strings."""
        for key, value in naming_convention.items():
            assert isinstance(value, str), f"Value for {key} is not a string"
