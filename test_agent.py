"""End-to-end agent test for ADE-Bench (terminal-tool style).

No-arg @terminal env: the agent uses `bash` inside a dbt-project sandbox to
edit and inspect models, then finishes by replying with a plain message.
The environment then rebuilds test/seed directories, applies AUTO + manual
dbt test files, runs `dbt test`, and returns reward 1.0 iff all tests pass.

Runs against the deployed env by default; set LOCAL=1 for localhost:8080.
"""

import asyncio
import json
import os

from openai import AsyncOpenAI
from openreward import AsyncOpenReward
from openreward.api.environments.types import ToolCallError


def _text_of(response) -> str:
    parts = []
    for item in response.output:
        if item.type == "message":
            for block in item.content:
                if block.type == "output_text":
                    parts.append(block.text)
    return "\n".join(parts).strip()


async def main():
    or_client = AsyncOpenReward()
    oai_client = AsyncOpenAI()

    MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-5.2")
    ENV_NAME = "GeneralReasoning/ADE-Bench"
    SPLIT = os.environ.get("SPLIT", "test")
    NUM_TASKS = int(os.environ.get("NUM_TASKS", "1"))
    MAX_TURNS = int(os.environ.get("MAX_TURNS", "50"))
    OR_API_KEY = os.getenv("OPENREWARD_API_KEY")

    base_url = "http://localhost:8080" if os.environ.get("LOCAL") else None
    environment = or_client.environments.get(name=ENV_NAME, base_url=base_url)

    tasks = await environment.list_tasks(split=SPLIT)
    tools = await environment.list_tools(format="openai")
    terminal_tool = await environment.terminal_tool()

    print(f"Environment: {ENV_NAME} ({base_url or 'deployed'})")
    print(f"Found {len(tasks)} tasks; visible tools: {[t['name'] for t in tools]}")
    print(f"Terminal tool (hidden): {terminal_tool}")

    rewards = []
    for task in tasks[:NUM_TASKS]:
        task_id = task.task_spec.get("id")
        print(f"\n=== Task {task_id} ===")

        async with environment.session(
            task=task, secrets={"api_key": OR_API_KEY},
        ) as session:
            assistant_ends_rollout = await session.is_assistant_message_final()
            session_tools = await session.list_tools()
            assert "submit" not in [t.name for t in session_tools], \
                "terminal tool leaked into the model's tool list"

            prompt = await session.get_prompt()
            input_list = [{"role": "user", "content": prompt[0].text}]

            reward = None
            turn = 0
            while turn < MAX_TURNS:
                turn += 1
                response = await oai_client.responses.create(
                    model=MODEL_NAME, tools=tools, input=input_list,
                )
                input_list += response.output

                calls = [i for i in response.output if i.type == "function_call"]
                if calls:
                    for item in calls:
                        args = json.loads(str(item.arguments))
                        try:
                            tr = await session.call_tool(item.name, args)
                            text = tr.blocks[0].text if tr.blocks else ""
                        except ToolCallError as e:
                            text = f"Error: {e}"
                        input_list.append({
                            "type": "function_call_output",
                            "call_id": item.call_id,
                            "output": text,
                        })
                        print(f"[{turn}] {item.name}: {json.dumps(args)[:110]}")
                    continue

                final_message = _text_of(response)
                print(f"\n[{turn}] Final message: {final_message[:200]}")

                if not assistant_ends_rollout:
                    print("Not a terminal-tool environment; stopping.")
                    break

                out = await session.call_terminal_tool()
                reward = out.reward
                print(f"call_terminal_tool() -> reward={reward} finished={out.finished}")
                if out.blocks:
                    print(out.blocks[0].text[:600])
                break

            rewards.append(reward)

    scored = [r for r in rewards if r is not None]
    print(f"\n=== Summary ===")
    print(f"num_tasks={len(rewards)} num_scored={len(scored)} "
          f"mean_reward={sum(scored)/len(scored) if scored else None}")
    print(f"rewards={rewards}")


if __name__ == "__main__":
    asyncio.run(main())
