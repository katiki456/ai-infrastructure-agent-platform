import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agents import (
    Agent,
    AgentUpdatedStreamEvent,
    GuardrailFunctionOutput,
    HandoffCallItem,
    HandoffOutputItem,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    RawResponsesStreamEvent,
    RunContextWrapper,
    RunItemStreamEvent,
    Runner,
    TResponseInputItem,
    function_tool,
    handoff,
    input_guardrail,
    output_guardrail,
)
from agents import (
    trace as agents_trace,
)
from openai.types.responses import ResponseTextDeltaEvent
from opentelemetry import trace as otel_trace

from .config import get_settings
from .metrics import GUARDRAIL_TRIPS, HANDOFFS, TOOL_CALLS

_tracer = otel_trace.get_tracer(__name__)


class RoutingDecision(StrEnum):
    KUBERNETES = "kubernetes"
    GPU = "gpu"
    INCIDENT = "incident"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class AgentStreamEvent:
    """A normalized event for the API's SSE adapter."""

    kind: str
    payload: str | dict[str, Any]


class AgentGuardrailError(RuntimeError):
    """Safe, user-facing representation of a triggered agent guardrail."""

    def __init__(self, message: str, guardrail: str) -> None:
        super().__init__(message)
        self.message = message
        self.guardrail = guardrail


class AgentTimeoutError(RuntimeError):
    """Safe, user-facing representation of an exceeded agent deadline."""

    message = "The infrastructure request exceeded its execution deadline."


def _input_text(input_value: str | list[TResponseInputItem]) -> str:
    if isinstance(input_value, str):
        return input_value
    return json.dumps(input_value, default=str)


def classify_request(prompt: str) -> RoutingDecision:
    """Return the deterministic routing hint supplied to TriageAgent.

    The model remains responsible for invoking a handoff. This classifier makes the
    policy legible, keeps ambiguous requests from being guessed at, and gives tests a
    stable contract without depending on model wording.
    """

    text = prompt.casefold()
    matches: list[RoutingDecision] = []
    if any(
        term in text
        for term in (
            "kubernetes",
            "k8s",
            "pod",
            "node",
            "namespace",
            "deployment",
            "readiness",
            "cluster health",
            "workload",
        )
    ):
        matches.append(RoutingDecision.KUBERNETES)
    if any(term in text for term in ("gpu", "cuda", "nvidia", "accelerator")):
        matches.append(RoutingDecision.GPU)
    if any(
        term in text
        for term in ("incident", "outage", "postmortem", "root cause", "sev1", "sev2")
    ):
        matches.append(RoutingDecision.INCIDENT)
    return matches[0] if len(matches) == 1 else RoutingDecision.AMBIGUOUS


def _record_tool_call(name: str) -> None:
    TOOL_CALLS.labels(tool=name).inc()


@function_tool
def get_cluster_health() -> str:
    """Return mock Kubernetes cluster health data."""

    _record_tool_call("get_cluster_health")
    return json.dumps(
        {
            "cluster_name": "mock-prod-us-west-2",
            "overall_status": "healthy",
            "control_plane": {"status": "healthy", "version": "v1.30.4"},
            "nodes": {"total": 3, "ready": 3, "not_ready": 0},
            "workloads": {
                "deployments": 8,
                "pods_running": 24,
                "pods_pending": 0,
                "pods_failed": 0,
            },
            "alerts": [],
        }
    )


@function_tool
def get_pod_failures(namespace: str = "all") -> str:
    """Return mock failed-pod data for a Kubernetes namespace."""

    _record_tool_call("get_pod_failures")
    return json.dumps(
        {
            "namespace": namespace,
            "failures": [
                {
                    "pod": "payments-worker-7d8f6d9f5c-q2m8k",
                    "reason": "CrashLoopBackOff",
                    "restarts": 4,
                    "last_exit_code": 137,
                }
            ],
            "observed_at": "2026-08-18T18:00:00Z",
        }
    )


@function_tool
def get_gpu_utilization(cluster: str = "mock-prod-us-west-2") -> str:
    """Return mock GPU utilization and capacity data."""

    _record_tool_call("get_gpu_utilization")
    return json.dumps(
        {
            "cluster": cluster,
            "accelerator": "NVIDIA H100",
            "total_devices": 8,
            "utilization_percent": 71,
            "memory_used_percent": 64,
            "temperature_celsius": 68,
            "saturated_devices": 1,
        }
    )


@function_tool
def get_prometheus_metrics(query: str = "up") -> str:
    """Return mock Prometheus time-series data for a safe read-only query."""

    _record_tool_call("get_prometheus_metrics")
    return json.dumps(
        {
            "query": query[:200],
            "result_type": "vector",
            "samples": [
                {"metric": "up", "labels": {"job": "mock-platform"}, "value": 1},
                {
                    "metric": "container_cpu_usage_seconds_total",
                    "labels": {"service": "payments"},
                    "value": 0.42,
                },
            ],
        }
    )


@function_tool
def get_incident_history(service: str = "all") -> str:
    """Return mock historical incidents for a service."""

    _record_tool_call("get_incident_history")
    return json.dumps(
        {
            "service": service,
            "incidents": [
                {
                    "id": "INC-2048",
                    "severity": "SEV2",
                    "summary": "Elevated checkout latency",
                    "status": "resolved",
                    "occurred_at": "2026-08-15T14:32:00Z",
                    "root_cause": "Database connection pool exhaustion",
                },
                {
                    "id": "INC-2039",
                    "severity": "SEV3",
                    "summary": "Worker pod restart spike",
                    "status": "resolved",
                    "occurred_at": "2026-08-11T09:18:00Z",
                    "root_cause": "Upstream timeout configuration",
                },
            ],
        }
    )


@input_guardrail(name="infrastructure_scope_guardrail")
def infrastructure_scope_guardrail(
    ctx: RunContextWrapper[None],
    agent: Agent[Any],
    input: str | list[TResponseInputItem],
) -> GuardrailFunctionOutput:
    """Block clearly unrelated requests before the triage model runs."""

    del ctx, agent
    text = _input_text(input).casefold()
    in_scope = any(
        term in text
        for term in (
            "infrastructure",
            "platform",
            "production",
            "service",
            "system",
            "kubernetes",
            "k8s",
            "pod",
            "cluster",
            "gpu",
            "cuda",
            "prometheus",
            "metric",
            "incident",
            "outage",
            "alert",
            "on-call",
            "oncall",
            "deployment",
            "workload",
            "node",
            "availability",
            "latency",
            "error",
            "failure",
            "health",
            "utilization",
        )
    )
    return GuardrailFunctionOutput(
        output_info={"infrastructure_scope": in_scope},
        tripwire_triggered=not in_scope,
    )


@output_guardrail(name="operations_output_guardrail")
def operations_output_guardrail(
    ctx: RunContextWrapper[None],
    agent: Agent[Any],
    agent_output: Any,
) -> GuardrailFunctionOutput:
    """Ensure specialist replies stay grounded in operations topics."""

    del ctx
    text = str(agent_output).casefold()
    grounded = any(
        term in text
        for term in (
            "cluster",
            "pod",
            "gpu",
            "incident",
            "metric",
            "prometheus",
            "service",
            "deployment",
            "workload",
            "infrastructure",
            "operations",
            "mock",
            "clarif",
        )
    )
    return GuardrailFunctionOutput(
        output_info={"agent": agent.name, "operations_grounded": grounded},
        tripwire_triggered=not grounded,
    )


kubernetes_agent = Agent(
    name="KubernetesAgent",
    handoff_description="Handles Kubernetes cluster, node, workload, pod, and readiness questions.",
    instructions=(
        "You are the Kubernetes operations specialist. Use get_cluster_health for cluster, "
        "node, workload, readiness, or alert status. Use get_pod_failures for pod failures, "
        "CrashLoopBackOff, restarts, or namespace-specific problems. Use "
        "get_prometheus_metrics for supporting read-only measurements. Always call the "
        "most relevant tool before making an operational claim. State that data is mocked. "
        "Do not perform changes or claim to have remediated anything."
    ),
    model=get_settings().openai_model,
    tools=[get_cluster_health, get_pod_failures, get_prometheus_metrics],
    output_guardrails=[operations_output_guardrail],
)

gpu_agent = Agent(
    name="GPUAgent",
    handoff_description=(
        "Handles GPU, CUDA, accelerator capacity, saturation, and utilization questions."
    ),
    instructions=(
        "You are the GPU infrastructure specialist. Use get_gpu_utilization for GPU or "
        "accelerator capacity, saturation, memory, or temperature questions. Use "
        "get_prometheus_metrics when the user asks for supporting time-series evidence. "
        "Always call a relevant tool before making an operational claim. State that data "
        "is mocked, and never claim to change scheduling or workloads."
    ),
    model=get_settings().openai_model,
    tools=[get_gpu_utilization, get_prometheus_metrics],
    output_guardrails=[operations_output_guardrail],
)

incident_agent = Agent(
    name="IncidentAgent",
    handoff_description=(
        "Handles incidents, outages, postmortems, severity, and root-cause history."
    ),
    instructions=(
        "You are the incident operations specialist. Use get_incident_history for incident, "
        "outage, postmortem, severity, or root-cause history questions. Use "
        "get_prometheus_metrics when current supporting measurements are requested. "
        "Always call a relevant tool before making an operational claim. State that data "
        "is mocked, distinguish historical facts from hypotheses, and do not claim to "
        "close or remediate incidents."
    ),
    model=get_settings().openai_model,
    tools=[get_incident_history, get_prometheus_metrics],
    output_guardrails=[operations_output_guardrail],
)

triage_agent = Agent(
    name="TriageAgent",
    instructions=(
        "You are the infrastructure operations triage agent. You own the initial request "
        "and must hand off clearly scoped Kubernetes, GPU, or incident requests to the "
        "matching specialist. Do not answer specialist questions yourself. If the request "
        "is ambiguous between domains or is broad infrastructure status, ask one concise "
        "clarifying question instead of guessing. The runtime supplies a routing policy "
        "hint; follow it and immediately invoke the corresponding handoff when it names a "
        "specialist. Keep the final response concise and grounded in mock data."
    ),
    model=get_settings().openai_model,
    handoffs=[
        handoff(
            kubernetes_agent,
            tool_name_override="route_to_kubernetes_agent",
            tool_description_override="Hand off Kubernetes and pod operations requests.",
        ),
        handoff(
            gpu_agent,
            tool_name_override="route_to_gpu_agent",
            tool_description_override="Hand off GPU and accelerator operations requests.",
        ),
        handoff(
            incident_agent,
            tool_name_override="route_to_incident_agent",
            tool_description_override="Hand off incident and outage operations requests.",
        ),
    ],
    input_guardrails=[infrastructure_scope_guardrail],
)


def _runtime_prompt(prompt: str) -> str:
    decision = classify_request(prompt)
    if decision is RoutingDecision.KUBERNETES:
        hint = (
            "The deterministic policy classified this as Kubernetes. "
            "Hand off to KubernetesAgent now."
        )
    elif decision is RoutingDecision.GPU:
        hint = (
            "The deterministic policy classified this as GPU infrastructure. "
            "Hand off to GPUAgent now."
        )
    elif decision is RoutingDecision.INCIDENT:
        hint = (
            "The deterministic policy classified this as incident operations. "
            "Hand off to IncidentAgent now."
        )
    else:
        hint = "The policy found no single specialist. Do not guess; ask one clarifying question."
    return f"Routing policy hint: {hint}\n\nOriginal user request:\n{prompt}"


def _tool_name(item: Any) -> str:
    raw_item = getattr(item, "raw_item", None)
    return str(
        getattr(item, "name", None)
        or getattr(raw_item, "name", None)
        or getattr(item, "tool_name", None)
        or "unknown_tool"
    )


async def stream_agent_response(prompt: str) -> AsyncIterator[AgentStreamEvent]:
    """Run TriageAgent and normalize handoff, tool, and text events for SSE."""

    current_agent = triage_agent.name
    yield AgentStreamEvent("agent", {"name": current_agent, "role": "triage"})
    try:
        with agents_trace("infrastructure_operations"):
            with _tracer.start_as_current_span("agents.infrastructure_operations") as span:
                span.set_attribute("ai.agent.entrypoint", triage_agent.name)
                span.set_attribute("ai.routing.decision", classify_request(prompt).value)
                stream = Runner.run_streamed(
                    triage_agent,
                    _runtime_prompt(prompt),
                    max_turns=get_settings().agent_max_turns,
                )
                async with asyncio.timeout(get_settings().agent_timeout_seconds):
                    async for event in stream.stream_events():
                        if isinstance(event, AgentUpdatedStreamEvent):
                            current_agent = event.new_agent.name
                            yield AgentStreamEvent(
                                "agent",
                                {"name": current_agent, "role": "specialist"},
                            )
                            continue

                        if isinstance(event, RunItemStreamEvent):
                            if event.name in {"handoff_requested", "handoff_occured"}:
                                item = event.item
                                source = current_agent
                                target = "unknown_agent"
                                if isinstance(item, HandoffCallItem):
                                    target = item.agent.name
                                elif isinstance(item, HandoffOutputItem):
                                    source = item.source_agent.name
                                    target = item.target_agent.name
                                if event.name == "handoff_occured":
                                    HANDOFFS.labels(from_agent=source, to_agent=target).inc()
                                yield AgentStreamEvent(
                                    "handoff",
                                    {
                                        "status": "requested"
                                        if event.name == "handoff_requested"
                                        else "completed",
                                        "from_agent": source,
                                        "to_agent": target,
                                    },
                                )
                            elif event.name in {"tool_called", "tool_output"}:
                                yield AgentStreamEvent(
                                    "tool",
                                    {
                                        "status": "called"
                                        if event.name == "tool_called"
                                        else "completed",
                                        "agent": current_agent,
                                        "name": _tool_name(event.item),
                                    },
                                )
                            continue

                        if isinstance(event, RawResponsesStreamEvent) and isinstance(
                            event.data, ResponseTextDeltaEvent
                        ):
                            yield AgentStreamEvent("delta", event.data.delta)
    except TimeoutError as exc:
        raise AgentTimeoutError from exc
    except InputGuardrailTripwireTriggered as exc:
        GUARDRAIL_TRIPS.labels(guardrail="infrastructure_scope_guardrail").inc()
        raise AgentGuardrailError(
            "I can help with infrastructure operations, Kubernetes, GPUs, metrics, or incidents.",
            "infrastructure_scope_guardrail",
        ) from exc
    except OutputGuardrailTripwireTriggered as exc:
        GUARDRAIL_TRIPS.labels(guardrail="operations_output_guardrail").inc()
        raise AgentGuardrailError(
            "The operations specialist could not produce a grounded infrastructure response.",
            "operations_output_guardrail",
        ) from exc


# Backward-compatible alias for callers that imported the original single agent.
agent = triage_agent
