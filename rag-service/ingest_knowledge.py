#!/usr/bin/env python3
"""
Knowledge Base Ingestion Script.

Loads support tickets and documentation into PostgreSQL/pgvector for RAG.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import structlog
from google import genai
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Integer, String, Text, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

logger = structlog.get_logger()

# Configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@postgres:5432/partner_agent")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")
EMBEDDING_DIM = 3072  # Google Gemini embedding-001 dimension (updated from 768)

# Database setup
Base = declarative_base()


class KnowledgeDocument(Base):
    """Model for knowledge documents stored in PostgreSQL."""
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    knowledge_base = Column(String(255), nullable=False, index=True)
    document_id = Column(String(255), nullable=False, index=True)
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=True)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


def generate_embedding(text: str, genai_client: genai.Client) -> np.ndarray:
    """
    Generate embedding for a single text using Google Gemini.

    Args:
        text: Text to embed
        genai_client: Google GenAI client

    Returns:
        Embedding vector as numpy array
    """
    try:
        response = genai_client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text
        )
        if not response.embeddings or not response.embeddings[0].values:
            raise ValueError(f"Empty embedding response for text: {text[:50]}...")
        return np.array(response.embeddings[0].values)
    except Exception as e:
        logger.error("Error generating embedding", error=str(e), text_preview=text[:50])
        raise


def load_from_json_files(data_dir: str = "data") -> Dict[str, List[Dict[str, Any]]]:
    """
    Load support ticket data from JSON files.

    Args:
        data_dir: Directory containing JSON data files

    Returns:
        Dictionary with collections and their documents
    """
    # In container, script is at /app/ingest_knowledge.py and data is at /app/data
    script_dir = Path(__file__).parent  # /app
    data_path = script_dir / data_dir  # /app/data

    collections = {}

    # Load software support tickets
    software_file = data_path / "software_support_tickets.json"
    if software_file.exists():
        logger.info("Loading software support tickets", path=str(software_file))
        with open(software_file, 'r') as f:
            software_tickets = json.load(f)
            collections["software_support"] = software_tickets
            logger.info("Loaded software support documents", count=len(software_tickets))

    # Load network support tickets
    network_file = data_path / "network_support_tickets.json"
    if network_file.exists():
        logger.info("Loading network support tickets", path=str(network_file))
        with open(network_file, 'r') as f:
            network_tickets = json.load(f)
            collections["network_support"] = network_tickets
            logger.info("Loaded network support documents", count=len(network_tickets))

    # Also create combined collection for general search
    all_tickets = []
    if "software_support" in collections:
        all_tickets.extend(collections["software_support"])
    if "network_support" in collections:
        all_tickets.extend(collections["network_support"])

    if all_tickets:
        collections["support_tickets"] = all_tickets
        logger.info("Created combined collection", total=len(all_tickets))

    return collections


async def ingest_collection(
    collection_name: str,
    documents: List[Dict[str, Any]],
    session: AsyncSession,
    genai_client: genai.Client
):
    """
    Ingest documents into PostgreSQL/pgvector.

    Args:
        collection_name: Name of the knowledge base
        documents: List of documents to ingest
        session: Database session
        genai_client: Google GenAI client for embeddings
    """
    try:
        logger.info("Ingesting collection", collection=collection_name)

        # Clear existing data (for fresh ingestion)
        stmt = delete(KnowledgeDocument).where(KnowledgeDocument.knowledge_base == collection_name)
        result = await session.execute(stmt)
        await session.commit()
        logger.info("Cleared existing documents", count=result.rowcount)

        # Ingest documents with embeddings
        for i, doc in enumerate(documents):
            # Generate embedding
            content = doc["content"]
            embedding = generate_embedding(content, genai_client)

            # Create document record
            knowledge_doc = KnowledgeDocument(
                knowledge_base=collection_name,
                document_id=doc["id"],
                content=content,
                metadata=doc["metadata"],
                embedding=embedding.tolist()  # Convert numpy array to list for pgvector
            )
            session.add(knowledge_doc)

            if (i + 1) % 10 == 0:
                logger.info("Ingesting documents", collection=collection_name, progress=f"{i+1}/{len(documents)}")

        await session.commit()

        # Verify ingestion
        stmt = select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.knowledge_base == collection_name)
        result = await session.execute(stmt)
        final_count = result.scalar()
        logger.info("Ingestion complete", collection=collection_name, document_count=final_count)

        # Show sample
        stmt = select(KnowledgeDocument.document_id).where(KnowledgeDocument.knowledge_base == collection_name).limit(3)
        result = await session.execute(stmt)
        sample_ids = [row[0] for row in result.all()]
        logger.info("Sample IDs", ids=sample_ids)

    except Exception as e:
        logger.error("Failed to ingest collection", collection=collection_name, error=str(e))
        await session.rollback()
        raise


async def main_async():
    """Main ingestion function (async)."""
    logger.info("Starting knowledge base ingestion...")

    # Check Google API key
    if not GOOGLE_API_KEY:
        logger.error("GOOGLE_API_KEY environment variable not set")
        return

    # Initialize Google GenAI client
    genai_client = genai.Client(api_key=GOOGLE_API_KEY)
    logger.info("Google GenAI client initialized", model=EMBEDDING_MODEL)

    # Initialize database connection
    logger.info("Connecting to PostgreSQL", database_url=DATABASE_URL.split('@')[-1])
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True
    )

    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    # Load data from JSON files
    logger.info("Loading knowledge base data from JSON files...")
    data = load_from_json_files()

    # Ingest each collection
    async with async_session_maker() as session:
        for collection_name, documents in data.items():
            await ingest_collection(
                collection_name=collection_name,
                documents=documents,
                session=session,
                genai_client=genai_client
            )

    # Show final summary
    async with async_session_maker() as session:
        stmt = select(
            KnowledgeDocument.knowledge_base,
            func.count(KnowledgeDocument.id).label("count")
        ).group_by(KnowledgeDocument.knowledge_base)
        result = await session.execute(stmt)
        summary = {kb: count for kb, count in result.all()}

    logger.info("Knowledge base ingestion complete", collections=summary)
    logger.info("RAG service is ready to use!")

    # Close database
    await engine.dispose()


def main():
    """Main entry point."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
