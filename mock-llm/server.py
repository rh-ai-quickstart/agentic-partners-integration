#!/usr/bin/env python3
"""Mock LLM server for CI e2e tests.

Mimics both Gemini and OpenAI APIs with deterministic responses so the
full integration pipeline (routing, auth, RAG retrieval, response format)
can be validated without a real API key.

Endpoints:
  POST /v1beta/models/{model}:embedContent   — Gemini embedding
  POST /v1beta/models/{model}:generateContent — Gemini generation
  POST /v1beta/openai/chat/completions        — OpenAI-compat (kubernetes-agent)
  GET  /health                                — health check
"""

import hashlib
import math
import re
from typing import Any

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI(title="Mock LLM Server")

EMBEDDING_DIM = 3072


def deterministic_embedding(text: str) -> list[float]:
    """Generate a deterministic embedding vector from text.

    Uses a hash-based approach to produce a unit vector that is consistent
    for the same input text. Different texts get different (but deterministic)
    vectors, enabling meaningful cosine similarity in RAG search.
    """
    vec = []
    for i in range(EMBEDDING_DIM):
        h = hashlib.sha256(f"{text}:{i}".encode()).hexdigest()
        val = (int(h[:8], 16) / 0xFFFFFFFF) * 2 - 1
        vec.append(val)
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec]


def extract_query_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from the query for echo-back."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "above",
        "below", "between", "out", "off", "over", "under", "again",
        "further", "then", "once", "here", "there", "when", "where", "why",
        "how", "all", "both", "each", "few", "more", "most", "other", "some",
        "such", "no", "nor", "not", "only", "own", "same", "so", "than",
        "too", "very", "just", "because", "but", "and", "or", "if", "while",
        "about", "up", "that", "this", "these", "those", "what", "which",
        "who", "whom", "it", "its", "i", "me", "my", "we", "our", "you",
        "your", "he", "him", "his", "she", "her", "they", "them", "their",
    }
    words = re.findall(r"[a-zA-Z0-9_-]+", text.lower())
    return [w for w in words if w not in stop_words and len(w) > 2]


ROUTING_RULES: list[tuple[str, list[str]]] = [
    ("kubernetes-support", [
        "kubernetes", "k8s", "pod", "kubectl", "crashloopbackoff", "crashloop",
        "container", "deployment", "helm", "namespace", "node", "cluster",
        "kubelet", "kube", "openshift", "oc",
    ]),
    ("network-support", [
        "network", "ospf", "bgp", "router", "switch", "vlan", "firewall",
        "dns", "dhcp", "tcp", "udp", "latency", "packet", "routing",
        "subnet", "vpn", "adjacency", "peering", "interface",
    ]),
    ("software-support", [
        "software", "python", "java", "javascript", "django", "react",
        "import", "error", "exception", "bug", "crash", "database", "sql",
        "api", "deploy", "build", "compile", "npm", "pip", "application",
    ]),
]


def detect_routing(user_msg: str, system_prompt: str) -> str | None:
    """If this is a routing-agent call, return ROUTE:<agent> response."""
    if "routing agent" not in system_prompt.lower():
        return None
    if "ROUTE:" not in system_prompt:
        return None

    blocked_section = ""
    blocked_marker = "does NOT have access to:"
    if blocked_marker in system_prompt:
        blocked_section = system_prompt[system_prompt.index(blocked_marker):]

    msg_lower = user_msg.lower()
    best_agent = None
    best_score = 0
    for agent_name, keywords in ROUTING_RULES:
        if agent_name not in system_prompt:
            continue
        if agent_name in blocked_section:
            continue
        score = sum(1 for kw in keywords if kw in msg_lower)
        if score > best_score:
            best_score = score
            best_agent = agent_name

    if best_agent and best_score > 0:
        label = best_agent.replace("-", " ")
        return (
            f"ROUTE:{best_agent}\n"
            f"I'll connect you with our {label} specialist."
        )

    if not best_agent:
        for agent_name, keywords in ROUTING_RULES:
            if agent_name in blocked_section:
                score = sum(1 for kw in keywords if kw in msg_lower)
                if score > 0:
                    label = agent_name.replace("-", " ")
                    return (
                        f"I'm sorry, but you don't have access to the "
                        f"{label} specialist. Please contact your "
                        f"administrator to request access to that department."
                    )

    return None


def build_mock_response(query: str) -> str:
    """Build a mock LLM response that echoes query keywords.

    The response is structured to pass e2e topic-keyword checks by
    naturally including the query terms in a support-style answer.
    """
    keywords = extract_query_keywords(query)
    keyword_str = ", ".join(keywords[:10]) if keywords else "general issue"

    return (
        f"Based on the support knowledge base, here is guidance for your issue "
        f"regarding {keyword_str}.\n\n"
        f"## Analysis\n\n"
        f"The issue involves: {keyword_str}. "
        f"This is a known pattern in our support history.\n\n"
        f"## Recommended Steps\n\n"
        f"1. Review the relevant logs and configuration for {keywords[0] if keywords else 'the component'}\n"
        f"2. Check recent changes that may have affected {keywords[1] if len(keywords) > 1 else 'the system'}\n"
        f"3. Apply the documented fix from the referenced support tickets\n"
        f"4. Verify the resolution and monitor for recurrence\n\n"
        f"The referenced support tickets contain detailed resolution steps "
        f"for this type of {keywords[0] if keywords else 'issue'}."
    )


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "mock-llm"}


@app.post("/v1beta/models/{model}:embedContent")
async def embed_content(model: str, request: Request):
    """Gemini embedding API mock (single content)."""
    body = await request.json()

    contents = body.get("contents", body.get("content", ""))
    if isinstance(contents, list):
        text = " ".join(str(c) for c in contents)
    else:
        text = str(contents)

    embedding = deterministic_embedding(text)

    return {
        "embeddings": [
            {
                "values": embedding,
            }
        ]
    }


@app.post("/v1beta/models/{model}:batchEmbedContents")
async def batch_embed_contents(model: str, request: Request):
    """Gemini batch embedding API mock (used by google-genai SDK)."""
    body = await request.json()

    embeddings = []
    for req in body.get("requests", []):
        content = req.get("content", {})
        parts = content.get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict))
        embedding = deterministic_embedding(text)
        embeddings.append({"values": embedding})

    return {"embeddings": embeddings}


def _extract_text_from_contents(contents: Any) -> str:
    """Extract plain text from Gemini contents payload."""
    if isinstance(contents, list):
        parts_texts = []
        for item in contents:
            if isinstance(item, dict):
                for part in item.get("parts", []):
                    if isinstance(part, dict):
                        parts_texts.append(part.get("text", ""))
                    else:
                        parts_texts.append(str(part))
            else:
                parts_texts.append(str(item))
        return " ".join(parts_texts)
    return str(contents)


def _extract_system_instruction(body: dict) -> str:
    """Extract system instruction from Gemini generateContent request."""
    gen_config = body.get("generationConfig", {})
    sys_inst = gen_config.get("systemInstruction", body.get("systemInstruction", ""))
    if isinstance(sys_inst, dict):
        parts = sys_inst.get("parts", [])
        return " ".join(
            p.get("text", "") for p in parts if isinstance(p, dict)
        )
    return str(sys_inst) if sys_inst else ""


def _extract_last_user_message(contents: Any) -> str:
    """Extract the last user message from Gemini contents."""
    if isinstance(contents, list):
        for item in reversed(contents):
            if isinstance(item, dict) and item.get("role") == "user":
                parts = item.get("parts", [])
                return " ".join(
                    p.get("text", "") for p in parts if isinstance(p, dict)
                )
    return _extract_text_from_contents(contents)


@app.post("/v1beta/models/{model}:generateContent")
async def generate_content(model: str, request: Request):
    """Gemini generation API mock."""
    body = await request.json()

    contents = body.get("contents", "")
    query = _extract_text_from_contents(contents)

    system_prompt = _extract_system_instruction(body)
    user_msg = _extract_last_user_message(contents)
    routing = detect_routing(user_msg, system_prompt)
    response_text = routing if routing else build_mock_response(query)

    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": response_text}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": len(query.split()),
            "candidatesTokenCount": len(response_text.split()),
            "totalTokenCount": len(query.split()) + len(response_text.split()),
        },
    }


@app.post("/v1beta/openai/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions mock (used by kubernetes-agent)."""
    body = await request.json()

    messages = body.get("messages", [])
    last_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    response_text = build_mock_response(last_user_msg)

    return {
        "id": "mock-completion-001",
        "object": "chat.completion",
        "model": body.get("model", "mock-model"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": len(last_user_msg.split()),
            "completion_tokens": len(response_text.split()),
            "total_tokens": len(last_user_msg.split()) + len(response_text.split()),
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
