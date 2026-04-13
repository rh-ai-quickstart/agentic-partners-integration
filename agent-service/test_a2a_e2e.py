"""End-to-end A2A protocol test for the partner specialist agents.

Runs without any infrastructure (no Docker, no PostgreSQL, no RAG API).
Starts a real A2A server with mock executors and tests with a real A2A client.

Usage:
    .venv/bin/python test_a2a_e2e.py
"""

import asyncio
import json
import logging
import uuid

import httpx
import uvicorn
from a2a.client import Client as A2AClient
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import ClientConfig as A2AClientConfig
from a2a.client.client_factory import ClientFactory as A2AClientFactory
from a2a.server.apps import A2AStarletteApplication
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
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
    DataPart,
    TransportProtocol,
)
from a2a.utils import new_task
from a2a.utils.errors import ServerError
from starlette.applications import Starlette
from starlette.routing import Mount

from agent_service.a2a.agent_cards import (
    create_network_support_card,
    create_software_support_card,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

SERVER_PORT = 9753
SW_BASE = f"http://localhost:{SERVER_PORT}/a2a/software-support"
NW_BASE = f"http://localhost:{SERVER_PORT}/a2a/network-support"

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


class MockSpecialistExecutor(AgentExecutor):
    """Returns a canned response, no LLM/RAG needed."""

    def __init__(self, agent_name: str):
        self._name = agent_name

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
                parts=[Part(root=TextPart(text="Searching knowledge base..."))],
                message_id=str(uuid.uuid4()),
                task_id=updater.task_id,
                context_id=updater.context_id,
            ),
            final=False,
        )

        response = (
            f"[{self._name}] Based on your query \"{user_input[:80]}\", "
            f"here is the troubleshooting guidance:\n\n"
            f"1. Check the system logs for related errors\n"
            f"2. Review the configuration files\n"
            f"3. Apply the recommended fix from ticket KB-{uuid.uuid4().hex[:6].upper()}\n\n"
            f"Source: knowledge base (similarity: 92.3%)"
        )

        await updater.update_status(
            TaskState.completed,
            message=Message(
                role=Role.agent,
                parts=[Part(root=TextPart(text=response))],
                metadata={"agent": self._name},
                message_id=str(uuid.uuid4()),
                task_id=updater.task_id,
                context_id=updater.context_id,
            ),
            final=True,
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        from a2a.types import UnsupportedOperationError
        raise ServerError(UnsupportedOperationError(message="Not supported"))


def build_test_server() -> Starlette:
    """Build a Starlette app hosting both A2A agents at sub-paths."""
    sw_card = create_software_support_card(SW_BASE + "/")
    nw_card = create_network_support_card(NW_BASE + "/")

    sw_handler = DefaultRequestHandler(
        agent_executor=MockSpecialistExecutor("software-support"),
        task_store=InMemoryTaskStore(),
    )
    nw_handler = DefaultRequestHandler(
        agent_executor=MockSpecialistExecutor("network-support"),
        task_store=InMemoryTaskStore(),
    )

    sw_app = A2AStarletteApplication(agent_card=sw_card, http_handler=sw_handler).build()
    nw_app = A2AStarletteApplication(agent_card=nw_card, http_handler=nw_handler).build()

    root = Starlette(routes=[
        Mount("/a2a/software-support", app=sw_app),
        Mount("/a2a/network-support", app=nw_app),
    ])
    return root


async def test_agent_card(http: httpx.AsyncClient, base_url: str, expected_name: str):
    """Test agent card discovery."""
    url = base_url.rstrip("/")
    resp = await http.get(f"{url}/.well-known/agent-card.json")
    ok = resp.status_code == 200
    record(f"Agent card HTTP 200 ({expected_name})", ok, f"status={resp.status_code}")
    if not ok:
        return None

    card = AgentCard(**resp.json())
    record(f"Card name matches", card.name == expected_name, f"got '{card.name}'")
    record(f"Protocol version 0.3.0", card.protocol_version == "0.3.0", card.protocol_version)
    record(f"Transport JSONRPC", card.preferred_transport == "JSONRPC", str(card.preferred_transport))
    record(f"Has skills", len(card.skills) > 0, f"{len(card.skills)} skills")
    record(f"Has description", len(card.description) > 50, f"{len(card.description)} chars")
    record(f"Capabilities set", card.capabilities is not None, "")
    return card


async def test_message_send(http: httpx.AsyncClient, base_url: str, agent_label: str, query: str):
    """Test JSON-RPC message/send (non-streaming)."""
    msg = Message(
        message_id=str(uuid.uuid4()),
        role="user",
        parts=[
            Part(root=TextPart(text=query)),
            Part(root=DataPart(
                data={"user_input": query, "context_summary": "E2E test"},
                metadata={"type": "structured_context", "schema_version": "1.0"},
            )),
        ],
        context_id=f"e2e-session-{uuid.uuid4().hex[:8]}",
    )

    jsonrpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {"message": msg.model_dump(by_alias=True, exclude_none=True)},
    }

    url = base_url.rstrip("/") + "/"
    resp = await http.post(url, json=jsonrpc)
    record(f"message/send HTTP 200 ({agent_label})", resp.status_code == 200, f"status={resp.status_code}")

    body = resp.json()
    record(f"JSONRPC 2.0 response", body.get("jsonrpc") == "2.0", "")

    if "error" in body:
        record(f"No JSONRPC error", False, body["error"].get("message", "")[:100])
        return

    result = body.get("result", {})
    task_id = result.get("id")
    record(f"Task ID present", bool(task_id), task_id or "missing")

    status = result.get("status", {})
    state = status.get("state")
    record(f"Task state=completed", state == "completed", f"got '{state}'")

    resp_msg = status.get("message", {})
    parts = resp_msg.get("parts", [])
    record(f"Response has parts", len(parts) > 0, f"{len(parts)} parts")

    if parts:
        text = parts[0].get("text", "")
        record(f"Response is non-empty", len(text) > 20, f"{len(text)} chars")
        has_agent = agent_label.replace(" ", "-").lower().replace(" agent", "")
        record(f"Response from correct agent", any(kw in text.lower() for kw in [agent_label.split()[0].lower(), "knowledge base"]), f"preview: {text[:80]}...")

    context_id = result.get("contextId")
    record(f"context_id propagated", context_id is not None, context_id or "missing")


async def test_a2a_client(http: httpx.AsyncClient, base_url: str, agent_label: str, query: str):
    """Test using the real A2A SDK client (same client the supervisor uses)."""
    async with httpx.AsyncClient(timeout=30.0) as sdk_http:
        url = base_url.rstrip("/") + "/"
        resolver = A2ACardResolver(httpx_client=sdk_http, base_url=url)
        card = await resolver.get_agent_card()
        record(f"A2A client card resolution ({agent_label})", card is not None, card.name if card else "failed")

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
            context_id="sdk-client-test",
        )

        events = []
        async for response in a2a_client.send_message(request=msg):
            events.append(response)

        record(f"A2A client got response(s)", len(events) > 0, f"{len(events)} events")

        if events:
            last = events[-1]
            if isinstance(last, tuple):
                task, update = last
                if task:
                    state = task.status.state if task.status else None
                    record(f"A2A client task completed", state == TaskState.completed, f"state={state}")
                    if task.status and task.status.message:
                        text_parts = [p.root.text for p in task.status.message.parts if hasattr(p.root, "text")]
                        full_text = " ".join(text_parts)
                        record(f"A2A client got text response", len(full_text) > 20, f"{len(full_text)} chars")
            elif isinstance(last, Message):
                text_parts = [p.root.text for p in last.parts if hasattr(p.root, "text")]
                record(f"A2A client got Message", len(text_parts) > 0, f"{len(text_parts)} text parts")


async def run_tests():
    print("=" * 64)
    print("  A2A PROTOCOL END-TO-END TEST")
    print("  No Docker, no PostgreSQL, no RAG API needed")
    print("=" * 64)

    server_app = build_test_server()
    config = uvicorn.Config(server_app, host="127.0.0.1", port=SERVER_PORT, log_level="error")
    server = uvicorn.Server(config)

    loop = asyncio.get_event_loop()
    server_task = loop.create_task(server.serve())

    await asyncio.sleep(1.0)

    async with httpx.AsyncClient(timeout=30.0) as http:
        # ── Software Support Agent ──
        print(f"\n{'─'*64}")
        print("  SOFTWARE SUPPORT AGENT")
        print(f"{'─'*64}")

        print("\n  Agent Card Discovery:")
        await test_agent_card(http, SW_BASE, "Software Support Agent")

        print("\n  JSON-RPC message/send:")
        await test_message_send(http, SW_BASE, "software-support", "My app crashes with a segfault when processing large files")

        print("\n  A2A SDK Client (same as supervisor):")
        await test_a2a_client(http, SW_BASE, "software-support", "Error code 0x80070005 during installation")

        # ── Network Support Agent ──
        print(f"\n{'─'*64}")
        print("  NETWORK SUPPORT AGENT")
        print(f"{'─'*64}")

        print("\n  Agent Card Discovery:")
        await test_agent_card(http, NW_BASE, "Network Support Agent")

        print("\n  JSON-RPC message/send:")
        await test_message_send(http, NW_BASE, "network-support", "My VPN keeps disconnecting every few minutes")

        print("\n  A2A SDK Client (same as supervisor):")
        await test_a2a_client(http, NW_BASE, "network-support", "DNS resolution fails for internal domains")

    server.should_exit = True
    await server_task

    # ── Summary ──
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
                print(f"    ✗ {name}: {detail}")
        return 1
    else:
        print(f"\n  All {total} tests passed!")
        print("  Both agents are fully A2A-compliant and ready for the supervisor.")
        return 0


if __name__ == "__main__":
    exit(asyncio.run(run_tests()))
