#!/usr/bin/env python3
"""
Real RAG API Service for Partner Agents.

This service provides RAG-based question answering using:
- PostgreSQL with pgvector for vector storage
- Google Gemini for embeddings and LLM
- Support ticket knowledge base
"""

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

import numpy as np
import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google import genai
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Integer, String, Text, select, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

logger = structlog.get_logger()

# Configuration from environment
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@postgres:5432/partner_agent")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")
EMBEDDING_DIM = 3072  # Google Gemini embedding-001 dimension (updated from 768)

if not GOOGLE_API_KEY:
    logger.error("GOOGLE_API_KEY is not set - cannot initialize RAG service")
    raise RuntimeError(
        "GOOGLE_API_KEY environment variable is required. "
        "Set it before starting the RAG service."
    )

logger.info(
    "Initializing RAG service with Google GenAI",
    model=LLM_MODEL,
    api_key_prefix=GOOGLE_API_KEY[:10] if GOOGLE_API_KEY else "NOT_SET"
)
genai_client = genai.Client(api_key=GOOGLE_API_KEY)
logger.info("Google GenAI client initialized successfully")

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


# Database engine and session
engine = None
async_session_maker = None


def generate_embedding(text: str) -> np.ndarray:
    """
    Generate embedding for a single text using Google Gemini.

    Args:
        text: Text to embed

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


async def search_knowledge_base(
    query: str,
    session: AsyncSession,
    collection_name: str = "support_tickets",
    num_results: int = 3,
    min_similarity: float = 0.0
) -> List[Dict[str, Any]]:
    """
    Search the knowledge base for relevant documents using pgvector similarity search.

    Args:
        query: User's search query
        session: Database session
        collection_name: Knowledge base name to search
        num_results: Number of results to return
        min_similarity: Minimum similarity threshold (0-1)

    Returns:
        List of relevant documents with metadata
    """
    try:
        # Generate query embedding
        query_embedding = generate_embedding(query)

        # Perform vector similarity search using cosine distance
        # pgvector's <=> operator computes cosine distance (0 = identical, 2 = opposite)
        stmt = (
            select(
                KnowledgeDocument,
                (1 - KnowledgeDocument.embedding.cosine_distance(query_embedding)).label("similarity")
            )
            .where(KnowledgeDocument.knowledge_base == collection_name)
            .where(KnowledgeDocument.embedding.isnot(None))
            .order_by(KnowledgeDocument.embedding.cosine_distance(query_embedding))
            .limit(num_results)
        )

        result = await session.execute(stmt)
        rows = result.all()

        # Format results
        documents = []
        for doc, similarity in rows:
            # Filter by minimum similarity
            if similarity >= min_similarity:
                documents.append({
                    "id": doc.document_id,
                    "content": doc.content,
                    "metadata": doc.metadata_ or {},
                    "similarity": float(similarity),
                    "distance": float(1 - similarity)
                })

        logger.info("Knowledge base search complete", result_count=len(documents), query_preview=query[:50])
        return documents

    except Exception:
        logger.exception("Error searching knowledge base")
        return []


def generate_answer(query: str, context_docs: List[Dict[str, Any]]) -> str:
    """
    Generate an answer using LLM with retrieved context.

    Args:
        query: User's question
        context_docs: Retrieved documents from knowledge base

    Returns:
        Generated answer
    """
    try:
        # Build context from retrieved documents
        context_parts = []
        for i, doc in enumerate(context_docs):
            metadata = doc.get('metadata', {})
            similarity = doc.get('similarity', 0)

            context_parts.append(
                f"[Document {i+1}] (Relevance: {similarity:.2f})\n"
                f"Ticket ID: {metadata.get('ticket_id', 'N/A')}\n"
                f"Category: {metadata.get('category', 'N/A')}\n"
                f"Content: {doc['content']}\n"
            )

        context = "\n---\n".join(context_parts)

        # Create prompt for LLM
        prompt = f"""You are a helpful IT support specialist. Answer the user's question based on the following support ticket history and documentation.

Retrieved Context:
{context}

User Question: {query}

Instructions:
- Provide a clear, actionable answer based on the retrieved context
- If the context contains a solution, explain it step-by-step
- If multiple solutions exist, mention the most relevant one
- If the context doesn't fully answer the question, say so and provide general guidance
- Keep your answer concise and practical

Answer:"""

        # Generate response using new Google GenAI SDK
        response = genai_client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt
        )

        answer = response.text.strip()
        logger.info("Generated answer", query_preview=query[:50])

        return answer

    except Exception as e:
        logger.error("Error generating answer", error=str(e))
        return "I encountered an error while generating a response. Please try again."


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialize services on startup."""
    global engine, async_session_maker

    logger.info("Starting RAG API Service...")

    # Initialize database engine
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10
    )

    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    logger.info("Database connection initialized", database_url=DATABASE_URL.split('@')[-1])
    logger.info("RAG API Service ready")

    yield

    # Cleanup
    if engine:
        await engine.dispose()
        logger.info("Database connection closed")


app = FastAPI(title="RAG API Service", version="1.0.0", lifespan=lifespan)


async def get_session() -> AsyncSession:
    """Get database session."""
    async with async_session_maker() as session:
        return session


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "RAG API Service",
        "version": "1.0.0",
        "description": "Real RAG-based question answering for partner agents using PostgreSQL/pgvector",
        "endpoints": {
            "/answer": "POST - Query the RAG knowledge base",
            "/health": "GET - Health check",
            "/stats": "GET - Knowledge base statistics"
        }
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    try:
        # Check database connection
        async with async_session_maker() as session:
            result = await session.execute(select(func.count()).select_from(KnowledgeDocument))
            total_docs = result.scalar()

        return {
            "status": "healthy",
            "service": "rag-api",
            "version": "1.0.0",
            "database": {
                "type": "postgresql+pgvector",
                "total_documents": total_docs
            },
            "llm_model": LLM_MODEL,
            "embedding_model": EMBEDDING_MODEL
        }
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": "Service dependencies unavailable"
            }
        )


@app.get("/stats")
async def stats():
    """Get knowledge base statistics."""
    try:
        async with async_session_maker() as session:
            # Get total documents
            result = await session.execute(select(func.count()).select_from(KnowledgeDocument))
            total_docs = result.scalar()

            # Get documents per knowledge base
            stmt = (
                select(
                    KnowledgeDocument.knowledge_base,
                    func.count(KnowledgeDocument.id).label("count")
                )
                .group_by(KnowledgeDocument.knowledge_base)
            )
            result = await session.execute(stmt)
            kb_stats = [{"knowledge_base": kb, "count": count} for kb, count in result.all()]

        return {
            "total_documents": total_docs,
            "knowledge_bases": kb_stats
        }
    except Exception as e:
        logger.error("Error getting stats", error=str(e))
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to retrieve statistics"}
        )


@app.post("/answer")
async def answer(request: Request):
    """
    Main RAG endpoint for answering questions.

    Request body:
    {
        "user_query": "My application crashes with error 500",
        "num_sources": 3,
        "only_high_similarity_nodes": false,
        "collection": "support_tickets",  # optional
        "min_similarity": 0.5  # optional
    }

    Response:
    {
        "response": "Generated answer...",
        "sources": [
            {
                "id": "doc-123",
                "content": "Document content...",
                "similarity": 0.85,
                "metadata": {...}
            }
        ],
        "metadata": {
            "num_sources": 3,
            "query_type": "software_support",
            "collection": "support_tickets"
        }
    }
    """
    try:
        body = await request.json()
        logger.info("Received RAG query", query_preview=body.get("user_query", "")[:100])

        user_query = body.get("user_query", "")
        num_sources = body.get("num_sources", 3)
        only_high_similarity = body.get("only_high_similarity_nodes", False)
        collection_name = body.get("collection", "support_tickets")
        min_similarity = body.get("min_similarity", 0.7 if only_high_similarity else 0.0)

        if not user_query:
            return JSONResponse(
                status_code=400,
                content={"error": "user_query is required"}
            )

        # Search knowledge base
        async with async_session_maker() as session:
            documents = await search_knowledge_base(
                query=user_query,
                session=session,
                collection_name=collection_name,
                num_results=num_sources,
                min_similarity=min_similarity
            )

        # Generate answer
        if documents:
            answer_text = generate_answer(user_query, documents)
        else:
            answer_text = (
                "I couldn't find relevant information in the knowledge base to answer your question. "
                "Please provide more details or contact support directly."
            )

        # Format response
        response_data = {
            "response": answer_text,
            "sources": [
                {
                    "id": doc["id"],
                    "content": doc["content"][:500],  # Truncate for response
                    "similarity": doc["similarity"],
                    "metadata": doc["metadata"]
                }
                for doc in documents
            ],
            "metadata": {
                "num_sources": len(documents),
                "collection": collection_name,
                "min_similarity": min_similarity,
                "query_length": len(user_query)
            }
        }

        logger.info("Returning answer", source_count=len(documents))
        return JSONResponse(content=response_data)

    except Exception as e:
        logger.error("Error processing RAG query", error=str(e))
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "response": "An error occurred processing your query. Please try again."
            }
        )


if __name__ == "__main__":
    logger.info(
        "Starting RAG API Service",
        url="http://0.0.0.0:8080",
        database_url=DATABASE_URL.split('@')[-1],
        llm_model=LLM_MODEL,
        embedding_model=EMBEDDING_MODEL,
    )
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
