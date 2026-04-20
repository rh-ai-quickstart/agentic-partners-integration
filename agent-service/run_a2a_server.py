"""Standalone A2A server for both partner specialist agents.

Runs without PostgreSQL, RAG API, or any infrastructure.
Serves real A2A protocol endpoints that the supervisor can connect to.

Usage:
    .venv/bin/python run_a2a_server.py
"""

import asyncio
import logging
import os
import uuid

import uvicorn
from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    InvalidParamsError,
    Message,
    Part,
    Role,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_task
from a2a.utils.errors import ServerError
from agent_service.a2a.agent_cards import create_agent_card
from agent_service.agents import AgentManager
from starlette.applications import Starlette
from starlette.routing import Mount

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger("a2a-server")

PORT = int(os.getenv("PORT", "8001"))
HOST = os.getenv("HOST", "0.0.0.0")


def _get_a2a_url(agent_name: str) -> str:
    env_key = agent_name.upper().replace("-", "_") + "_A2A_URL"
    return os.getenv(env_key, f"http://localhost:{PORT}/a2a/{agent_name}/")


SOFTWARE_RESPONSES = {
    "default": (
        "Based on your description, this appears to be a software stability issue. "
        "Here is the recommended troubleshooting approach:\n\n"
        "**Analysis:**\n"
        "The symptoms you described are consistent with memory management issues "
        "commonly seen in similar support cases.\n\n"
        "**Recommended Steps:**\n"
        "1. Check system logs: `journalctl -xe` or application-specific logs\n"
        "2. Verify resource usage: `top`, `free -m`, `df -h`\n"
        "3. Review recent changes: updates, configuration modifications\n"
        "4. Check for known issues in the release notes\n\n"
        "**Knowledge Base Reference:**\n"
        "- [KB-SW-2847] Similar crash pattern resolved by updating to latest patch\n"
        "- [KB-SW-1923] Memory leak fix available in version 2.4.1\n\n"
        "If the issue persists after these steps, please provide:\n"
        "- Full stack trace or error output\n"
        "- System specifications (OS version, RAM, disk space)\n"
        "- Steps to reproduce the issue"
    ),
}

NETWORK_RESPONSES = {
    "default": (
        "Based on your description, this appears to be a network connectivity issue. "
        "Here is a systematic diagnostic approach:\n\n"
        "**Analysis:**\n"
        "The symptoms suggest potential issues at the transport or application layer. "
        "Let's narrow down the root cause.\n\n"
        "**Diagnostic Steps:**\n"
        "1. Verify basic connectivity: `ping <target>` and `traceroute <target>`\n"
        "2. Check DNS resolution: `nslookup <domain>` or `dig <domain>`\n"
        "3. Test port connectivity: `telnet <host> <port>` or `nc -zv <host> <port>`\n"
        "4. Review firewall rules: `iptables -L -n` or firewall management console\n"
        "5. Check VPN status: `ip route show` and VPN client logs\n\n"
        "**Knowledge Base Reference:**\n"
        "- [KB-NW-4521] Intermittent VPN drops caused by MTU mismatch — fix: set MTU to 1400\n"
        "- [KB-NW-3187] DNS timeout resolved by adding secondary DNS server\n\n"
        "If the issue persists, please provide:\n"
        "- Network topology (VPN, proxy, firewall details)\n"
        "- Output of the diagnostic commands above\n"
        "- Time pattern of the failures (constant vs intermittent)"
    ),
}

KUBERNETES_RESPONSES = {
    "default": (
        "Based on your description, this appears to be a Kubernetes issue. "
        "Here is a systematic diagnostic approach:\n\n"
        "**Analysis:**\n"
        "The symptoms suggest a container orchestration issue. "
        "Let's identify the affected layer and narrow down the root cause.\n\n"
        "**Diagnostic Steps:**\n"
        "1. Check pod status: `kubectl get pods -o wide`\n"
        "2. Describe the resource: `kubectl describe pod <pod-name>`\n"
        "3. Check pod logs: `kubectl logs <pod-name> --previous`\n"
        "4. Check events: `kubectl get events --sort-by='.lastTimestamp'`\n"
        "5. Check resource usage: `kubectl top pods` and `kubectl top nodes`\n\n"
        "**Knowledge Base Reference:**\n"
        "- [K8S-TICKET-001] CrashLoopBackOff resolved by increasing memory limits and configuring JVM cgroup support\n"
        "- [K8S-TICKET-004] Service not routing traffic — fixed label selector mismatch\n"
        "- [K8S-TICKET-006] Ingress 502 Bad Gateway — fixed service targetPort to match container port\n\n"
        "If the issue persists, please provide:\n"
        "- Output of `kubectl describe` for the affected resource\n"
        "- Pod logs (`kubectl logs <pod-name>`)\n"
        "- Resource YAML (`kubectl get <resource> -o yaml`)"
    ),
}


class StandaloneExecutor(AgentExecutor):
    """Returns realistic mock responses — no LLM or RAG needed."""

    def __init__(self, agent_name: str, responses: dict[str, str]):
        self._name = agent_name
        self._responses = responses

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_input = context.get_user_input()
        if not user_input:
            raise ServerError(InvalidParamsError(message="No input message provided"))

        logger.info("Execute: agent=%s input='%s'", self._name, user_input[:80])

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

        await asyncio.sleep(0.3)

        response = self._responses.get("default", "No response available.")

        await updater.update_status(
            TaskState.completed,
            message=Message(
                role=Role.agent,
                parts=[Part(root=TextPart(text=response))],
                metadata={"agent": self._name, "rag_used": True},
                message_id=str(uuid.uuid4()),
                task_id=updater.task_id,
                context_id=updater.context_id,
            ),
            final=True,
        )
        logger.info("Completed: agent=%s response_len=%d", self._name, len(response))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(UnsupportedOperationError(message="Not supported"))


STANDALONE_RESPONSES: dict[str, dict[str, str]] = {
    "software-support": SOFTWARE_RESPONSES,
    "network-support": NETWORK_RESPONSES,
    "kubernetes-support": KUBERNETES_RESPONSES,
}


def build_app() -> Starlette:
    agent_manager = AgentManager()
    specialists = agent_manager.get_specialist_agents()

    routes = []
    for name, config in specialists.items():
        url = _get_a2a_url(name)
        card = create_agent_card(name, config, url)
        responses = STANDALONE_RESPONSES.get(
            name, {"default": "No response available."}
        )
        handler = DefaultRequestHandler(
            agent_executor=StandaloneExecutor(name, responses),
            task_store=InMemoryTaskStore(),
        )
        a2a_app = A2AStarletteApplication(agent_card=card, http_handler=handler).build()
        routes.append(Mount(f"/a2a/{name}", app=a2a_app))

    return Starlette(routes=routes)


if __name__ == "__main__":
    agent_manager = AgentManager()
    specialists = agent_manager.get_specialist_agents()
    print("\n  Partner A2A Agent Server")
    print("  " + "=" * 50)
    for name in sorted(specialists):
        print(f"  {name}: http://localhost:{PORT}/a2a/{name}/")
    print("  " + "=" * 50 + "\n")
    uvicorn.run(build_app(), host=HOST, port=PORT, log_level="info")
