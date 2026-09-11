"""Add agent_dcr_registrations table for DCR persistence.

Stores the Keycloak Registration Access Token (RAT) returned when an
agent self-registers via Dynamic Client Registration (RFC 7591).  The RAT
is needed for later update/delete operations on the registration and to
detect whether a fresh DCR POST is required after a container restart.

Revision ID: 010
Revises: 009
Create Date: 2026-09-10 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create agent_dcr_registrations table."""
    op.create_table(
        "agent_dcr_registrations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # The agent's SPIFFE URI — used as the Keycloak client_id.
        sa.Column(
            "spiffe_id",
            sa.String(512),
            nullable=False,
            unique=True,
            index=True,
            comment="Agent SPIFFE URI, e.g. spiffe://partner.example.com/agent/kubernetes-agent",
        ),
        # Human-readable client name registered in Keycloak.
        sa.Column(
            "client_name",
            sa.String(255),
            nullable=False,
            comment="Keycloak client_name from the DCR registration response",
        ),
        # Keycloak Registration Access Token — required for GET/PATCH/DELETE
        # on the registration endpoint.
        sa.Column(
            "registration_access_token",
            sa.Text(),
            nullable=False,
            comment="Keycloak Registration Access Token (opaque)",
        ),
        # Keycloak management endpoint for this client registration.
        sa.Column(
            "registration_client_uri",
            sa.String(1024),
            nullable=False,
            comment="Keycloak registration management URL for GET/PATCH/DELETE",
        ),
        # When the registration was created (for TTL / audit purposes).
        sa.Column(
            "registered_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="UTC timestamp of the DCR POST",
        ),
        # When the RAT was last successfully validated against Keycloak.
        sa.Column(
            "last_verified_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="UTC timestamp of the last successful GET on registration_client_uri",
        ),
    )


def downgrade() -> None:
    """Drop agent_dcr_registrations table."""
    op.drop_table("agent_dcr_registrations")
