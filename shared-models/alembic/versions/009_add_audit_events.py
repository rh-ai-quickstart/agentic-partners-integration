"""Add audit_events table for SOC 2 compliance (CC7.1, CC7.2).

Append-only table capturing authentication, authorization,
and data-access events with full actor/resource context.

Revision ID: 009
Revises: 008
Create Date: 2026-04-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create audit_events table."""
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.String(36),
            unique=True,
            nullable=False,
            index=True,
        ),
        sa.Column("event_type", sa.String(100), nullable=False, index=True),
        sa.Column("actor", sa.String(255), nullable=False, index=True),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("resource", sa.String(255), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="success"),
        sa.Column("reason", sa.String(1000), nullable=False, server_default=""),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("service", sa.String(100), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
    )

    # Composite index for common queries: filter by type + time range
    op.create_index(
        "ix_audit_events_type_created",
        "audit_events",
        ["event_type", "created_at"],
    )
    # Composite index for actor + time range (who did what recently?)
    op.create_index(
        "ix_audit_events_actor_created",
        "audit_events",
        ["actor", "created_at"],
    )


def downgrade() -> None:
    """Drop audit_events table."""
    op.drop_index("ix_audit_events_actor_created", table_name="audit_events")
    op.drop_index("ix_audit_events_type_created", table_name="audit_events")
    op.drop_table("audit_events")
