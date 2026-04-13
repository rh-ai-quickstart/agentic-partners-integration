"""Replace password auth with SPIFFE identity and departments.

Adds spiffe_id and departments columns to users table.
Removes password_hash and allowed_agents columns.
Migrates allowed_agents data to departments.

Revision ID: 008
Revises: 007
Create Date: 2026-04-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add SPIFFE identity and departments, remove password auth."""
    # Add new columns
    op.add_column(
        "users",
        sa.Column("spiffe_id", sa.String(255), nullable=True, unique=True),
    )
    op.add_column(
        "users",
        sa.Column("departments", sa.JSON(), nullable=False, server_default="[]"),
    )

    # Migrate allowed_agents -> departments
    # Map agent names to department tags
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            UPDATE users SET departments =
                CASE
                    WHEN allowed_agents::text = '["*"]'
                        THEN '["engineering", "software", "network", "admin"]'::jsonb
                    WHEN allowed_agents::text LIKE '%software-support%'
                        AND allowed_agents::text LIKE '%network-support%'
                        THEN '["engineering", "software", "network"]'::jsonb
                    WHEN allowed_agents::text LIKE '%software-support%'
                        THEN '["engineering", "software"]'::jsonb
                    WHEN allowed_agents::text LIKE '%network-support%'
                        THEN '["engineering", "network"]'::jsonb
                    ELSE '[]'::jsonb
                END
        """)
    )

    # Drop old columns
    op.drop_column("users", "password_hash")
    op.drop_column("users", "allowed_agents")


def downgrade() -> None:
    """Restore password auth and allowed_agents."""
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("allowed_agents", sa.JSON(), nullable=False, server_default="[]"),
    )

    # Reverse migrate departments -> allowed_agents
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            UPDATE users SET allowed_agents =
                CASE
                    WHEN departments::text LIKE '%admin%'
                        THEN '["*"]'::jsonb
                    WHEN departments::text LIKE '%software%'
                        AND departments::text LIKE '%network%'
                        THEN '["software-support", "network-support"]'::jsonb
                    WHEN departments::text LIKE '%software%'
                        THEN '["software-support"]'::jsonb
                    WHEN departments::text LIKE '%network%'
                        THEN '["network-support"]'::jsonb
                    ELSE '[]'::jsonb
                END
        """)
    )

    op.drop_column("users", "departments")
    op.drop_column("users", "spiffe_id")
