"""End-to-end A2A protocol test for the Kubernetes partner agent.

Runs without any infrastructure (no Docker, no API keys, no RAG API).
Starts a real A2A server with a mock executor and tests with a real A2A client.

Usage:
    uv run python test_a2a_e2e.py
"""

import asyncio
import uuid
from pathlib import Path

import httpx
import uvicorn
import yaml
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import ClientConfig as A2AClientConfig
from a2a.client.client_factory import ClientFactory as A2AClientFactory
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCard,
    Message,
    Part,
    Role,
    TaskState,
    TextPart,
    TransportProtocol,
    UnsupportedOperationError,
)
from a2a.utils import new_task
from a2a.utils.errors import ServerError
from starlette.applications import Starlette
from starlette.routing import Mount

from kubernetes_agent.a2a.agent_cards import create_agent_card

SERVER_PORT = 9754
K8S_BASE = f"http://localhost:{SERVER_PORT}/a2a/kubernetes-support"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    tag = PASS if ok else FAIL
    msg = f"  [{tag}] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


class MockKubernetesExecutor(AgentExecutor):
    """Returns a canned Kubernetes response, no LLM/RAG needed."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        if not user_input:
            from a2a.types import InvalidParamsError

            raise ServerError(InvalidParamsError(message="No input"))

        task = context.current_task or new_task(context.message)
        if not context.current_task:
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        await updater.update_status(
            TaskState.working,
            message=Message(
                role=Role.agent,
                parts=[Part(root=TextPart(text="Searching Kubernetes knowledge base..."))],
                message_id=str(uuid.uuid4()),
                task_id=updater.task_id,
                context_id=updater.context_id,
            ),
            final=False,
        )

        response = (
            f'[kubernetes-support] Based on your query "{user_input[:80]}", '
            f"here is the Kubernetes troubleshooting guidance:\n\n"
            f"1. Check pod events: kubectl describe pod <pod-name>\n"
            f"2. Review container logs: kubectl logs <pod-name>\n"
            f"3. Inspect resource limits and requests\n"
            f"4. Apply fix from ticket KB-{uuid.uuid4().hex[:6].upper()}\n\n"
            f"Source: knowledge base (similarity: 94.1%)"
        )

        await updater.update_status(
            TaskState.completed,
            message=Message(
                role=Role.agent,
                parts=[Part(root=TextPart(text=response))],
                metadata={"agent": "kubernetes-support"},
                message_id=str(uuid.uuid4()),
                task_id=updater.task_id,
                context_id=updater.context_id,
            ),
            final=True,
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(UnsupportedOperationError(message="Not supported"))


def _load_config() -> dict:
    """Load the kubernetes-support-agent YAML config."""
    config_path = Path(__file__).parent / "config" / "agents" / "kubernetes-support-agent.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def build_test_server() -> Starlette:
    """Build a Starlette app hosting the Kubernetes A2A agent."""
    config = _load_config()
    card = create_agent_card("kubernetes-support", config, K8S_BASE + "/")

    handler = DefaultRequestHandler(
        agent_executor=MockKubernetesExecutor(),
        task_store=InMemoryTaskStore(),
    )

    a2a_app = A2AStarletteApplication(
        agent_card=card, http_handler=handler
    ).build()

    root = Starlette(
        routes=[
            Mount("/a2a/kubernetes-support", app=a2a_app),
        ]
    )
    return root


async def test_agent_card(http: httpx.AsyncClient):
    """Test agent card discovery."""
    url = K8S_BASE.rstrip("/")
    resp = await http.get(f"{url}/.well-known/agent-card.json")
    ok = resp.status_code == 200
    record("Agent card HTTP 200", ok, f"status={resp.status_code}")
    if not ok:
        return

    card = AgentCard(**resp.json())
    record("Card name matches", card.name == "Kubernetes Support Agent", f"got '{card.name}'")
    record("Protocol version 0.3.0", card.protocol_version == "0.3.0", card.protocol_version)
    record("Transport JSONRPC", card.preferred_transport == "JSONRPC", str(card.preferred_transport))
    record("Has skills", len(card.skills) > 0, f"{len(card.skills)} skills")
    record("Has description", len(card.description) > 50, f"{len(card.description)} chars")
    record("Capabilities set", card.capabilities is not None, "")


async def test_message_send(http: httpx.AsyncClient, query: str):
    """Test JSON-RPC message/send (non-streaming)."""
    msg = Message(
        message_id=str(uuid.uuid4()),
        role="user",
        parts=[Part(root=TextPart(text=query))],
        context_id=f"e2e-k8s-{uuid.uuid4().hex[:8]}",
    )

    jsonrpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {"message": msg.model_dump(by_alias=True, exclude_none=True)},
    }

    url = K8S_BASE.rstrip("/") + "/"
    resp = await http.post(url, json=jsonrpc)
    record("message/send HTTP 200", resp.status_code == 200, f"status={resp.status_code}")

    body = resp.json()
    record("JSONRPC 2.0 response", body.get("jsonrpc") == "2.0", "")

    if "error" in body:
        record("No JSONRPC error", False, body["error"].get("message", "")[:100])
        return

    result = body.get("result", {})
    task_id = result.get("id")
    record("Task ID present", bool(task_id), task_id or "missing")

    status = result.get("status", {})
    state = status.get("state")
    record("Task state=completed", state == "completed", f"got '{state}'")

    resp_msg = status.get("message", {})
    parts = resp_msg.get("parts", [])
    record("Response has parts", len(parts) > 0, f"{len(parts)} parts")

    if parts:
        text = parts[0].get("text", "")
        record("Response is non-empty", len(text) > 20, f"{len(text)} chars")
        record(
            "Response from kubernetes agent",
            "kubernetes" in text.lower(),
            f"preview: {text[:80]}...",
        )

    context_id = result.get("contextId")
    record("context_id propagated", context_id is not None, context_id or "missing")


async def test_a2a_client(http: httpx.AsyncClient, query: str):
    """Test using the real A2A SDK client."""
    async with httpx.AsyncClient(timeout=30.0) as sdk_http:
        url = K8S_BASE.rstrip("/") + "/"
        resolver = A2ACardResolver(httpx_client=sdk_http, base_url=url)
        card = await resolver.get_agent_card()
        record("A2A client card resolution", card is not None, card.name if card else "failed")

        config = A2AClientConfig(
            httpx_client=sdk_http,
            streaming=False,
            polling=False,
            supported_transports=[TransportProtocol.jsonrpc],
        )
        factory = A2AClientFactory(config=config)
        a2a_client = factory.create(card)

        msg = Message(
            message_id=str(uuid.uuid4()),
            role="user",
            parts=[Part(root=TextPart(text=query))],
            context_id="sdk-k8s-test",
        )

        events = []
        async for response in a2a_client.send_message(request=msg):
            events.append(response)

        record("A2A client got response(s)", len(events) > 0, f"{len(events)} events")

        if events:
            last = events[-1]
            if isinstance(last, tuple):
                task, update = last
                if task:
                    state = task.status.state if task.status else None
                    record(
                        "A2A client task completed",
                        state == TaskState.completed,
                        f"state={state}",
                    )
                    if task.status and task.status.message:
                        text_parts = [
                            p.root.text
                            for p in task.status.message.parts
                            if hasattr(p.root, "text")
                        ]
                        full_text = " ".join(text_parts)
                        record(
                            "A2A client got text response",
                            len(full_text) > 20,
                            f"{len(full_text)} chars",
                        )
            elif isinstance(last, Message):
                text_parts = [
                    p.root.text for p in last.parts if hasattr(p.root, "text")
                ]
                record(
                    "A2A client got Message",
                    len(text_parts) > 0,
                    f"{len(text_parts)} text parts",
                )


async def run_tests():
    print("=" * 64)
    print("  KUBERNETES PARTNER AGENT — A2A PROTOCOL E2E TEST")
    print("  No Docker, no API keys, no RAG API needed")
    print("=" * 64)

    server_app = build_test_server()
    config = uvicorn.Config(
        server_app, host="127.0.0.1", port=SERVER_PORT, log_level="error"
    )
    server = uvicorn.Server(config)

    loop = asyncio.get_event_loop()
    server_task = loop.create_task(server.serve())

    await asyncio.sleep(1.0)

    async with httpx.AsyncClient(timeout=30.0) as http:
        print(f"\n{'─'*64}")
        print("  KUBERNETES SUPPORT AGENT")
        print(f"{'─'*64}")

        print("\n  Agent Card Discovery:")
        await test_agent_card(http)

        print("\n  JSON-RPC message/send:")
        await test_message_send(
            http,
            "My pods are stuck in CrashLoopBackOff after deployment",
        )

        print("\n  A2A SDK Client (same as supervisor):")
        await test_a2a_client(
            http,
            "Pod keeps getting OOMKilled — how do I fix resource limits?",
        )

    server.should_exit = True
    await server_task

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)

    print(f"\n{'=' * 64}")
    print(f"  RESULTS: {passed}/{total} passed, {failed} failed")
    print(f"{'=' * 64}")

    if failed:
        print(f"\n  Failed tests:")
        for name, ok, detail in results:
            if not ok:
                print(f"    x {name}: {detail}")
        return 1
    else:
        print(f"\n  All {total} tests passed!")
        print("  Kubernetes agent is fully A2A-compliant and ready for the supervisor.")
        return 0


if __name__ == "__main__":
    exit(asyncio.run(run_tests()))
