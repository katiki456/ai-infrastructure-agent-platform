import asyncio

import pytest
from agents import Agent, ModelResponse, Runner, Usage, handoff, set_tracing_disabled
from agents.models.interface import Model
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from platform_app.agent import (
    RoutingDecision,
    classify_request,
    gpu_agent,
    incident_agent,
    infrastructure_scope_guardrail,
    kubernetes_agent,
    triage_agent,
)

set_tracing_disabled(True)


class ScriptedModel(Model):
    """Small deterministic model double for routing and tool-loop tests."""

    def __init__(self, target_agent: str | None = None) -> None:
        self.target_agent = target_agent
        self.handoff_calls: list[str] = []
        self.tool_calls: list[str] = []

    async def get_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id,
        conversation_id,
        prompt,
    ) -> ModelResponse:
        del system_instructions, input, model_settings, output_schema
        del tracing, previous_response_id, conversation_id, prompt
        if handoffs:
            selected = next(
                item
                for item in handoffs
                if item.agent_name == self.target_agent
            )
            self.handoff_calls.append(selected.agent_name)
            return ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        arguments="{}",
                        call_id="handoff-call",
                        name=selected.tool_name,
                        type="function_call",
                        id="handoff-item",
                        status="completed",
                    )
                ],
                usage=Usage(),
                response_id="handoff-response",
            )
        if tools and not self.tool_calls:
            self.tool_calls.append(tools[0].name)
            return ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        arguments="{}",
                        call_id="tool-call",
                        name=tools[0].name,
                        type="function_call",
                        id="tool-item",
                        status="completed",
                    )
                ],
                usage=Usage(),
                response_id="tool-response",
            )
        message = ResponseOutputMessage(
            id="message-item",
            content=[
                ResponseOutputText(
                    text="Mock infrastructure operations result.",
                    type="output_text",
                    annotations=[],
                    logprobs=[],
                )
            ],
            role="assistant",
            status="completed",
            type="message",
        )
        return ModelResponse(
            output=[message],
            usage=Usage(),
            response_id="message-response",
        )

    async def stream_response(self, *args, **kwargs):
        del args, kwargs
        if False:
            yield None


@pytest.mark.parametrize(
    ("prompt", "decision"),
    [
        ("Which Kubernetes pods are failing?", RoutingDecision.KUBERNETES),
        ("What is the GPU utilization?", RoutingDecision.GPU),
        ("Show the incident history for checkout.", RoutingDecision.INCIDENT),
    ],
)
def test_specialist_questions_route_by_policy(
    prompt: str, decision: RoutingDecision
) -> None:
    assert classify_request(prompt) is decision


def test_ambiguous_infrastructure_question_stays_in_triage() -> None:
    decision = classify_request("What is happening with production infrastructure?")
    assert decision is RoutingDecision.AMBIGUOUS


def test_scope_guardrail_blocks_unrelated_requests() -> None:
    result = infrastructure_scope_guardrail.guardrail_function(
        None, triage_agent, "Tell me a joke."
    )
    assert result.tripwire_triggered is True


def _agent_copy(source: Agent, model: Model) -> Agent:
    return Agent(
        name=source.name,
        instructions=source.instructions,
        tools=source.tools,
        model=model,
        output_guardrails=[],
    )


def test_triage_handoff_reaches_kubernetes_agent() -> None:
    model = ScriptedModel(target_agent="KubernetesAgent")
    kubernetes = _agent_copy(kubernetes_agent, model)
    gpu = _agent_copy(gpu_agent, model)
    incident = _agent_copy(incident_agent, model)
    triage = Agent(
        name="TriageAgent",
        instructions="Hand off to the selected specialist.",
        model=model,
        handoffs=[handoff(kubernetes), handoff(gpu), handoff(incident)],
    )

    result = asyncio.run(Runner.run(triage, "Check Kubernetes cluster health."))

    assert result.last_agent.name == "KubernetesAgent"
    assert model.handoff_calls == ["KubernetesAgent"]
    assert model.tool_calls == ["get_cluster_health"]


@pytest.mark.parametrize(
    ("source", "expected_tool", "prompt"),
    [
        (kubernetes_agent, "get_cluster_health", "Check cluster health."),
        (gpu_agent, "get_gpu_utilization", "Check GPU utilization."),
        (incident_agent, "get_incident_history", "Show incident history."),
    ],
)
def test_specialists_can_call_their_tools(
    source: Agent, expected_tool: str, prompt: str
) -> None:
    model = ScriptedModel()
    specialist = _agent_copy(source, model)

    result = asyncio.run(Runner.run(specialist, prompt))

    assert result.final_output == "Mock infrastructure operations result."
    assert model.tool_calls == [expected_tool]
