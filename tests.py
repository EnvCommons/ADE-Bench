"""
Tests for ADE-Bench environment.

These are integration tests that require:
- The sandbox namespace GeneralReasoning/ADE-Bench to exist on openreward.ai
- The Docker image generalreasoning/ade-bench:latest to be built and pushed
- The bucket data to be uploaded (run prepare_data.py first)
- OPENREWARD_API_KEY environment variable set

Gold tests run solution.sh before submitting (should get reward=1.0).
xfail tests submit without changes (should get reward=0.0).
"""

import os

import pytest
from dotenv import load_dotenv
from openreward.environments.types import JSONObject

from ade_bench import ADEBench, BashParams, SubmitParams

load_dotenv()

SECRETS = {"api_key": os.environ.get("OPENREWARD_API_KEY", "")}
TASKS = ADEBench.list_tasks("test")

# Use simple tasks for testing (they're fast and lightweight)
SIMPLE_TASKS = [t for t in TASKS if t["task_id"].startswith("simple")]
BUCKET_PREFIX = "data"


@pytest.mark.asyncio
@pytest.mark.parametrize("task", SIMPLE_TASKS[:2])
async def test_gold(task: JSONObject):
    """Run solution.sh then submit — should get reward=1.0."""
    env = ADEBench(task_spec=task, secrets=SECRETS)
    await env.setup()
    try:
        bucket = f"/tmp/gr-datasets/{BUCKET_PREFIX}"
        task_id = task["task_id"]

        # Copy solution.sh and solutions/ directory into the sandbox
        await env.sandbox.run(
            f"cp {bucket}/tasks/{task_id}/solution.sh /app/solution.sh 2>/dev/null || true"
        )
        await env.sandbox.run(
            f"cp -r {bucket}/tasks/{task_id}/solutions /app/solutions 2>/dev/null || true"
        )

        # Run the reference solution
        result = await env.bash(BashParams(command="bash solution.sh --db-type=duckdb --project-type=dbt 2>&1"))
        assert result.metadata["exit_code"] == 0, f"solution.sh failed: {result.blocks[0].text}"

        # Submit for evaluation
        result = await env.submit(SubmitParams())
        assert result.reward == 1.0, f"Expected reward=1.0, got {result.reward}: {result.blocks[0].text}"
    finally:
        await env.teardown()


@pytest.mark.asyncio
@pytest.mark.parametrize("task", SIMPLE_TASKS[:2])
async def test_xfail(task: JSONObject):
    """Submit without changes — should get reward=0.0."""
    env = ADEBench(task_spec=task, secrets=SECRETS)
    await env.setup()
    try:
        # Submit without making any changes (project is still in "broken" state)
        result = await env.submit(SubmitParams())
        assert result.reward == 0.0, f"Expected reward=0.0, got {result.reward}: {result.blocks[0].text}"
    finally:
        await env.teardown()
