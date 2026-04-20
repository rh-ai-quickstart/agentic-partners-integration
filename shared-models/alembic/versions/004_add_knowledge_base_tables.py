"""Add knowledge base tables for pgvector

Adds:
- pgvector extension for vector similarity search
- knowledge_documents table for RAG document storage
- Indexes for efficient similarity search

Revision ID: 004
Revises: 003
Create Date: 2026-03-04 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create knowledge_documents table
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("knowledge_base", sa.String(255), nullable=False),
        sa.Column("document_id", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "embedding",
            Vector(
                3072
            ),  # Google Gemini embedding-001 produces 3072-dimensional vectors
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    # Create index for knowledge base lookups
    op.create_index(
        "idx_knowledge_base_name",
        "knowledge_documents",
        ["knowledge_base"],
        unique=False,
    )

    # Create index for document_id lookups
    op.create_index(
        "idx_document_id",
        "knowledge_documents",
        ["document_id"],
        unique=False,
    )

    # Create unique composite index for knowledge_base + document_id
    # This prevents duplicate documents and enables ON CONFLICT during ingestion
    op.create_index(
        "idx_kb_document",
        "knowledge_documents",
        ["knowledge_base", "document_id"],
        unique=True,
    )

    # Note: Vector index creation is skipped for 3072-dimensional embeddings
    # Both HNSW and IVFFlat in pgvector have a 2000 dimension limit
    # Similarity search will use sequential scan (acceptable for small datasets)
    # For production with large datasets, consider:
    # - Using an embedding model with <=2000 dimensions
    # - Applying dimensionality reduction (e.g., PCA)
    # - Using a dedicated vector database (e.g., Pinecone, Weaviate)


def downgrade() -> None:
    """Downgrade database schema."""
    # Drop indexes (vector index not created for 3072-dim embeddings)
    op.drop_index("idx_kb_document", table_name="knowledge_documents")
    op.drop_index("idx_document_id", table_name="knowledge_documents")
    op.drop_index("idx_knowledge_base_name", table_name="knowledge_documents")

    # Drop table
    op.drop_table("knowledge_documents")

    # Drop pgvector extension (be careful - other tables might use it)
    op.execute("DROP EXTENSION IF EXISTS vector")
