"""OpenReward environment for ADE-Bench (Analytics and Data Engineering Benchmark).

Evaluates AI agents on dbt + DuckDB analytics engineering tasks.
Agents work in a sandbox with a dbt project and DuckDB database, making changes
to fix/build dbt models. Evaluation runs dbt tests comparing agent output against
reference CSVs — binary scoring (all tests pass = 1.0).

Reference: https://github.com/dbt-labs/ade-bench
"""

import json
import os
import re

from openreward import AsyncOpenReward, SandboxBucketConfig, SandboxSettings
from openreward.environments import Environment, JSONObject, Server, TextBlock, ToolOutput, tool
from pydantic import BaseModel

from test_generator import generate_solution_tests, get_equality_macro_content

# ---------------------------------------------------------------------------
# Module-level data loading
# ---------------------------------------------------------------------------

if os.path.exists("/orwd_data"):
    _data_path = "/orwd_data/tasks.json"
else:
    _data_path = os.path.join(os.path.dirname(__file__), "data", "tasks.json")

with open(_data_path) as f:
    _ALL_TASKS: list[JSONObject] = json.load(f)

# Index tasks by id for quick lookup
_TASKS_BY_ID: dict[str, JSONObject] = {t["id"]: t for t in _ALL_TASKS}

# ---------------------------------------------------------------------------
# Tool input models
# ---------------------------------------------------------------------------

BUCKET_PREFIX = "data"


class BashParams(BaseModel, extra="forbid"):
    command: str


class SubmitParams(BaseModel, extra="forbid"):
    """Submit your solution for evaluation. No arguments needed."""
    pass


# ---------------------------------------------------------------------------
# dbt output parsing
# ---------------------------------------------------------------------------

# Match individual test result lines from dbt test output
# "1 of 2 PASS test_one ...... [PASS in 0.01s]"
# "2 of 2 FAIL 1 test_two .... [FAIL 1 in 0.00s]"
_DBT_TEST_RESULT_RE = re.compile(
    r"\d+\s+of\s+\d+\s+(PASS|FAIL|ERROR)(?:\s+\d+)?\s+(\S+)\s+\.+\s+\[(PASS|FAIL|ERROR)"
)

# Match summary line: "Done. PASS=X WARN=Y ERROR=Z SKIP=W TOTAL=T"
_DBT_SUMMARY_RE = re.compile(
    r"Done\.\s+PASS=(\d+)\s+WARN=(\d+)\s+ERROR=(\d+)\s+SKIP=(\d+)"
    r"(?:\s+NO-OP=(\d+))?\s+TOTAL=(\d+)"
)

# Expected test count from run-dbt-test.sh
_EXPECTED_COUNT_RE = re.compile(r"\[ade-bench\] expected_test_count=(\d+)")


def _parse_dbt_output(output: str) -> tuple[bool, str]:
    """Parse dbt test output and determine if all tests passed.

    Returns:
        (resolved, summary_message)
    """
    has_test_results = bool(_DBT_TEST_RESULT_RE.search(output)) or bool(
        _DBT_SUMMARY_RE.search(output)
    )

    # Check for compilation error (only if no test results at all)
    if "Compilation Error" in output and not has_test_results:
        return False, "FAIL - dbt compilation error"

    # Parse individual test results
    test_results: dict[str, str] = {}
    for match in _DBT_TEST_RESULT_RE.finditer(output):
        status = match.group(1)  # PASS, FAIL, or ERROR
        test_name = match.group(2)
        test_results[test_name] = "PASSED" if status == "PASS" else "FAILED"

    # Parse summary
    summary_match = None
    for m in _DBT_SUMMARY_RE.finditer(output):
        summary_match = m  # Use last match

    # Get expected test count
    expected_match = _EXPECTED_COUNT_RE.search(output)
    expected_count = int(expected_match.group(1)) if expected_match else None

    # Determine pass/fail counts
    if test_results:
        pass_count = sum(1 for s in test_results.values() if s == "PASSED")
        fail_count = sum(1 for s in test_results.values() if s == "FAILED")
        total_count = len(test_results)
    elif summary_match:
        pass_count = int(summary_match.group(1))
        fail_count = int(summary_match.group(3))  # ERROR field
        total_count = int(summary_match.group(6))
    else:
        return False, "FAIL - no dbt test results found"

    # Resolution logic (mirrors harness._is_resolved())
    if total_count == 0:
        return False, "FAIL - no tests ran"

    if expected_count is not None and total_count < expected_count:
        return False, (
            f"FAIL - only {total_count}/{expected_count} tests produced results, "
            f"{pass_count} passed, {fail_count} failed"
        )

    if fail_count > 0:
        return False, f"FAIL - {pass_count}/{total_count} passed, {fail_count} failed"

    return True, f"PASS - all {pass_count} tests passed"


def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

class ADEBench(Environment):
    """ADE-Bench: Analytics and Data Engineering benchmark."""

    def __init__(self, task_spec: JSONObject, secrets: dict[str, str] = {}) -> None:
        super().__init__(task_spec)

        self.task_id = task_spec["task_id"]  # e.g. "airbnb001"
        self.prompt_text = task_spec["prompt"]
        self.db_name = task_spec["db_name"]
        self.project_name = task_spec["project_name"]
        self.test_setup = task_spec.get("test_setup")
        self.solution_seeds = task_spec.get("solution_seeds", [])
        self.has_manual_tests = task_spec.get("has_manual_tests", False)
        self.has_seeds = task_spec.get("has_seeds", False)

        if not secrets.get("api_key"):
            raise ValueError("OpenReward API key is required")

        self.sandbox_settings = SandboxSettings(
            environment="GeneralReasoning/ADE-Bench",
            image="generalreasoning/ade-bench:latest",
            machine_size="1:2",
            block_network=False,
            bucket_config=SandboxBucketConfig(
                mount_path="/tmp/gr-datasets",
                read_only=True,
                only_dir=BUCKET_PREFIX,
            ),
        )

        or_client = AsyncOpenReward(api_key=secrets.get("api_key"))
        self.sandbox = or_client.sandbox(self.sandbox_settings)

        self.submitted = False

    # ---- class methods ----

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["test"]

    @classmethod
    def list_tasks(cls, split: str) -> list[JSONObject]:
        if split == "test":
            return _ALL_TASKS
        return []

    # ---- lifecycle ----

    async def setup(self) -> None:
        await self.sandbox.start()

        bucket = f"/tmp/gr-datasets/{BUCKET_PREFIX}"

        # Copy dbt project to /app/
        project_src = f"{bucket}/domains/{self.project_name}/project"
        await self.sandbox.run(f"cp -r {project_src}/* /app/ 2>/dev/null || true")

        # Copy DuckDB database to /app/
        db_src = f"{bucket}/domains/{self.project_name}/{self.db_name}.duckdb"
        await self.sandbox.run(f"cp {db_src} /app/{self.db_name}.duckdb 2>/dev/null || true")

        # Copy shared scripts for evaluation
        await self.sandbox.run(f"cp {bucket}/scripts/* /scripts/ 2>/dev/null || true")

        # Install dbt packages
        await self.sandbox.run("cd /app && dbt deps 2>&1", timeout=120)

        # Copy and run setup.sh to create the "broken" state
        setup_src = f"{bucket}/tasks/{self.task_id}/setup.sh"
        await self.sandbox.run(f"cp {setup_src} /app/setup.sh 2>/dev/null || true")
        await self.sandbox.run(
            "cd /app && bash setup.sh --db-type=duckdb --project-type=dbt 2>&1",
            timeout=120,
        )

        # Remove setup.sh so agent can't see it
        await self.sandbox.run("rm -f /app/setup.sh")

        # Remove solution.sh and solutions/ from bucket mount visibility
        # (they're read-only on the bucket, agent would need to know the path)

    async def teardown(self) -> None:
        await self.sandbox.stop()

    # ---- prompt ----

    def get_prompt(self) -> list[TextBlock]:
        prompt = (
            "You are an expert analytics engineer working on a dbt project with a DuckDB database.\n\n"
            f"## Task\n\n{self.prompt_text}\n\n"
            "## Instructions\n\n"
            "The dbt project is at `/app/`. The DuckDB database is in the same directory.\n"
            "Use `dbt run`, `dbt compile`, `dbt test`, and other dbt commands to explore and fix the project.\n"
            "You can also query the database directly with `duckdb /app/*.duckdb` for data inspection.\n\n"
            "When you are done, call the `submit` tool to evaluate your solution.\n\n"
            "Key principles:\n"
            "- Do exactly what is asked, nothing more\n"
            "- Inspect actual data to understand problems\n"
            "- Check your work by running dbt commands before submitting\n"
            "- Do not add tests, documentation, or refactor unless explicitly asked"
        )
        return [TextBlock(text=prompt)]

    # ---- tools ----

    @tool
    async def bash(self, params: BashParams) -> ToolOutput:
        """Execute a bash command in the dbt project environment."""
        result = await self.sandbox.run(
            f"cd /app && {params.command.strip()}",
            timeout=300,
        )
        output, code = result

        if result.truncated:
            output = f"...(truncated, output exceeded limit)\n{output}"

        return ToolOutput(
            blocks=[TextBlock(text=f"{output}\n\n(exit {code})")],
            metadata={"output": output, "exit_code": code, "truncated": result.truncated},
            reward=0.0,
            finished=False,
        )

    @tool
    async def submit(self, params: SubmitParams) -> ToolOutput:
        """Submit your solution for evaluation. Runs dbt tests to verify correctness."""
        if self.submitted:
            return ToolOutput(
                blocks=[TextBlock(text="Already submitted.")],
                metadata={"error": "already_submitted"},
                reward=0.0,
                finished=True,
            )

        self.submitted = True
        bucket = f"/tmp/gr-datasets/{BUCKET_PREFIX}"

        # Step 1: Clean test and seed directories in the project
        await self.sandbox.run("cd /app && rm -rf tests seeds && mkdir -p tests seeds")

        # Step 2: Run test_setup first (e.g., "dbt run --select model_name")
        # This builds the agent's models BEFORE loading seeds and running tests,
        # matching the order in run-dbt-test.sh
        eval_output = ""
        if self.test_setup:
            # Write test_setup as a script for reliable multiline execution
            await self.sandbox.run(
                f"cat > /tmp/test-setup.sh << 'SETUP_EOF'\n#!/bin/bash\ncd /app\n{self.test_setup}\nSETUP_EOF"
            )
            setup_result = await self.sandbox.run(
                "bash /tmp/test-setup.sh 2>&1",
                timeout=300,
            )
            eval_output += setup_result[0] + "\n"

        # Step 3: Generate AUTO test SQL files from solution_seeds (server-side)
        if self.solution_seeds:
            auto_tests, macro_content = generate_solution_tests(self.solution_seeds)

            # Upload AUTO test files via heredoc
            for filename, content in auto_tests.items():
                await self.sandbox.run(
                    f"cat > /app/tests/{_shell_quote(filename)} << 'AUTOTEST_EOF'\n{content}\nAUTOTEST_EOF"
                )

            # Upload equality macro
            await self.sandbox.run("mkdir -p /app/macros")
            await self.sandbox.run(
                f"cat > /app/macros/ade_bench_equality_test.sql << 'MACRO_EOF'\n{macro_content}\nMACRO_EOF"
            )

        # Step 4: Copy manual test SQL files from bucket
        if self.has_manual_tests:
            await self.sandbox.run(
                f"cp {bucket}/tasks/{self.task_id}/tests/*.sql /app/tests/ 2>/dev/null || true"
            )

        # Step 5: Count included test files (for expected_test_count)
        # Filter by db type (duckdb) like run-dbt-test.sh does
        count_result = await self.sandbox.run(
            """cd /app && included=0; for f in tests/*.sql; do
                [ -f "$f" ] || continue
                include=true
                if grep -q "^-- *db:" "$f"; then
                    grep -q "^-- *db:.*duckdb" "$f" || include=false
                fi
                if grep -q "^-- *project-type:" "$f"; then
                    grep -q "^-- *project-type:.*dbt" "$f" || include=false
                fi
                if [ "$include" = true ]; then
                    included=$((included+1))
                else
                    rm "$f"
                fi
            done; echo "[ade-bench] expected_test_count=$included"
            """
        )
        eval_output += count_result[0] + "\n"

        # Step 6: Copy seed CSVs from bucket and handle schema merging
        if self.has_seeds:
            await self.sandbox.run(
                f"cp -r {bucket}/tasks/{self.task_id}/seeds/* /app/seeds/ 2>/dev/null || true"
            )
            # Merge seed column types into dbt_project.yml if _no-op.txt exists
            await self.sandbox.run(
                "cd /app && if [ -f seeds/_no-op.txt ]; then bash /scripts/seed-schema.sh; fi 2>&1"
            )
            seed_result = await self.sandbox.run(
                "cd /app && dbt seed 2>&1",
                timeout=120,
            )
            eval_output += seed_result[0] + "\n"

        # Step 7: Run dbt test
        test_result = await self.sandbox.run(
            'cd /app && dbt test --select "test_type:singular" 2>&1',
            timeout=300,
        )
        eval_output += test_result[0] + "\n"

        # Step 8: Parse results
        resolved, summary = _parse_dbt_output(eval_output)
        reward = 1.0 if resolved else 0.0

        # Build response with relevant output
        response_text = f"Evaluation result: {summary}\nReward: {reward}"

        # Include test failure details if not resolved
        if not resolved:
            # Extract failing test names from output
            failing_tests = []
            for match in _DBT_TEST_RESULT_RE.finditer(eval_output):
                if match.group(1) in ("FAIL", "ERROR"):
                    failing_tests.append(match.group(2))
            if failing_tests:
                response_text += "\n\nFailing tests:\n" + "\n".join(
                    f"  - {t}" for t in failing_tests
                )

            # Include compilation errors if present
            if "Compilation Error" in eval_output:
                # Extract the error message
                lines = eval_output.split("\n")
                error_lines = []
                capture = False
                for line in lines:
                    if "Compilation Error" in line:
                        capture = True
                    if capture:
                        error_lines.append(line)
                        if len(error_lines) > 15:
                            break
                if error_lines:
                    response_text += "\n\nCompilation error:\n" + "\n".join(error_lines)

        return ToolOutput(
            blocks=[TextBlock(text=response_text)],
            metadata={
                "resolved": resolved,
                "summary": summary,
                "task_id": self.task_id,
            },
            reward=reward,
            finished=True,
        )


if __name__ == "__main__":
    Server([ADEBench]).run()
