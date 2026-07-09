"""The agent loop: call the model, run any tool it asks for, repeat.

The loop is deliberately tiny. Send the running ``messages`` to the model with
the tool schemas attached. If the reply carries tool calls, run each one, append
its result to ``messages`` as a ``role: "tool"`` turn, and go round again. If the
reply is plain text, that's the answer and we stop. A ``max_steps`` guard stops a
model that never stops asking for tools.

This is the bare loop a checker would wrap: ``/goal``-style, you'd inspect the
final reply (or the dashboard it wrote) and either accept it or feed the agent a
correction and loop once more. That checker is not built here — this is the
mechanism it would drive.

``run_agent`` **mutates** the ``messages`` list it is given: the assistant turns
and tool results are appended in place, so a chat loop can keep one growing
thread across many user turns and hand the same list back in next time.
"""

from collections.abc import Callable
from dataclasses import dataclass

from .llm import ChatModel
from .tools import ToolRegistry

DEFAULT_MAX_STEPS = 8


@dataclass(frozen=True)
class ToolInvocation:
    """Audit record of one tool the agent ran, for the caller to display/log."""

    name: str
    arguments_json: str
    result: str


@dataclass(frozen=True)
class AgentResult:
    """Outcome of one agent turn (which may span several model calls)."""

    ok: bool
    reply: str | None = None
    error: str | None = None
    steps: int = 0
    invocations: tuple[ToolInvocation, ...] = ()


def run_agent(
    model: ChatModel,
    messages: list[dict],
    registry: ToolRegistry,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    on_tool: Callable[[ToolInvocation], None] | None = None,
) -> AgentResult:
    """Drive the model/tool loop until a plain-text reply or the step guard.

    ``on_tool`` (optional) is called as each tool finishes, so a CLI can show
    activity live instead of only after the whole turn returns.
    """
    tools_schema = registry.schema()
    invocations: list[ToolInvocation] = []

    for step in range(1, max_steps + 1):
        result = model.complete(messages, tools=tools_schema)
        if not result.ok:
            return AgentResult(
                ok=False, error=result.error, steps=step, invocations=tuple(invocations)
            )

        # Append the assistant turn before any tool results — the protocol
        # requires the tool_calls message to precede its role:"tool" replies.
        if result.message is not None:
            messages.append(result.message)

        if not result.tool_calls:
            return AgentResult(
                ok=True, reply=result.text, steps=step, invocations=tuple(invocations)
            )

        for call in result.tool_calls:
            output = registry.dispatch(call)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": output}
            )
            invocation = ToolInvocation(
                name=call.name, arguments_json=call.arguments_json, result=output
            )
            invocations.append(invocation)
            if on_tool is not None:
                on_tool(invocation)

    return AgentResult(
        ok=False,
        error=f"stopped after {max_steps} steps without a final reply",
        steps=max_steps,
        invocations=tuple(invocations),
    )
