#!/usr/bin/env python3
"""Prepare ADE-Bench data for the OpenReward bucket.

This script:
1. Clones the ade-bench repo (or uses existing clone)
2. Downloads DuckDB databases from Google Drive
3. Parses all task.yaml files and generates tasks.json
4. Organizes data into the bucket structure:
   data/
     tasks.json
     domains/{domain}/project/   # dbt project files
     domains/{domain}/{db_name}.duckdb
     tasks/{task_id}/setup.sh
     tasks/{task_id}/solution.sh
     tasks/{task_id}/solutions/  # (if exists)
     tasks/{task_id}/seeds/
     tasks/{task_id}/tests/
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO_URL = "https://github.com/dbt-labs/ade-bench.git"
GDRIVE_FOLDER = "https://drive.google.com/drive/folders/1CNS_8mf81to02868HA-celmcPEFu4BPE"

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
CLONE_DIR = SCRIPT_DIR / "ade-bench-clone"


def clone_repo():
    """Clone the ade-bench repo if not already present."""
    if CLONE_DIR.exists():
        print(f"Using existing clone at {CLONE_DIR}")
        return
    print(f"Cloning {REPO_URL}...")
    subprocess.run(
        ["git", "clone", "--depth", "1", REPO_URL, str(CLONE_DIR)],
        check=True,
    )


def download_databases():
    """Download DuckDB databases from Google Drive."""
    db_dir = DATA_DIR / "databases"
    if db_dir.exists() and any(db_dir.glob("*.duckdb")):
        print(f"DuckDB databases already present in {db_dir}")
        return

    db_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading DuckDB databases from Google Drive...")
    subprocess.run(
        [
            sys.executable, "-m", "gdown",
            "--folder", GDRIVE_FOLDER,
            "-O", str(db_dir),
        ],
        check=True,
    )
    print(f"Downloaded databases to {db_dir}")


def parse_tasks() -> list[dict]:
    """Parse all task.yaml files and build task specs for DuckDB+dbt variant only."""
    tasks_dir = CLONE_DIR / "tasks"
    all_tasks = []

    for task_dir in sorted(tasks_dir.iterdir()):
        # Skip hidden directories (e.g., .template)
        if task_dir.name.startswith("."):
            continue
        task_yaml = task_dir / "task.yaml"
        if not task_yaml.exists():
            continue

        with open(task_yaml) as f:
            task_data = yaml.safe_load(f)

        task_id = task_data["task_id"]

        # Find the DuckDB+dbt variant
        duckdb_variant = None
        for variant in task_data.get("variants", []):
            if variant.get("db_type") == "duckdb" and variant.get("project_type") == "dbt":
                duckdb_variant = variant
                break

        if not duckdb_variant:
            print(f"  Skipping {task_id}: no duckdb+dbt variant")
            continue

        # Check that the database file exists
        db_name = duckdb_variant["db_name"]
        db_path = DATA_DIR / "databases" / f"{db_name}.duckdb"
        if not db_path.exists():
            print(f"  Skipping {task_id}: database {db_name}.duckdb not found")
            continue

        # Normalize solution_seeds
        solution_seeds = []
        for seed in task_data.get("solution_seeds", []):
            if isinstance(seed, str):
                solution_seeds.append({"table_name": seed})
            else:
                solution_seeds.append(seed)

        # Check for manual tests (non-AUTO SQL files)
        tests_dir = task_dir / "tests"
        has_manual_tests = False
        if tests_dir.exists():
            for sql_file in tests_dir.glob("*.sql"):
                if not sql_file.name.startswith("AUTO_"):
                    has_manual_tests = True
                    break

        # Check for seeds
        seeds_dir = task_dir / "seeds"
        has_seeds = seeds_dir.exists() and any(seeds_dir.iterdir())

        # Create a task spec for each prompt variant
        for prompt_info in task_data.get("prompts", []):
            prompt_key = prompt_info["key"]

            # Task ID format: {task_id}.{prompt_key} if multiple prompts,
            # otherwise just {task_id}
            if len(task_data.get("prompts", [])) > 1:
                full_id = f"{task_id}.{prompt_key}"
            else:
                full_id = task_id

            task_spec = {
                "id": full_id,
                "task_id": task_id,
                "prompt_key": prompt_key,
                "prompt": prompt_info["prompt"],
                "db_name": duckdb_variant["db_name"],
                "project_name": duckdb_variant["project_name"],
                "difficulty": task_data.get("difficulty", "unknown"),
                "tags": task_data.get("tags", []),
                "test_setup": task_data.get("test_setup"),
                "solution_seeds": solution_seeds,
                "has_manual_tests": has_manual_tests,
                "has_seeds": has_seeds,
            }

            all_tasks.append(task_spec)

    return all_tasks


def organize_data(tasks: list[dict]):
    """Organize data into the bucket directory structure."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Write tasks.json
    tasks_json_path = DATA_DIR / "tasks.json"
    with open(tasks_json_path, "w") as f:
        json.dump(tasks, f, indent=2)
    print(f"Wrote {len(tasks)} task specs to {tasks_json_path}")

    # Collect unique domains (project_name -> db_name mapping)
    domains = {}
    for task in tasks:
        project_name = task["project_name"]
        db_name = task["db_name"]
        if project_name not in domains:
            domains[project_name] = db_name

    # Copy dbt projects to domains/
    projects_src = CLONE_DIR / "shared" / "projects" / "dbt"
    for project_name, db_name in sorted(domains.items()):
        domain_dir = DATA_DIR / "domains" / project_name
        project_dst = domain_dir / "project"

        if project_dst.exists():
            shutil.rmtree(project_dst)

        project_src = projects_src / project_name
        if project_src.exists() and any(project_src.iterdir()):
            shutil.copytree(project_src, project_dst)
            print(f"  Copied project {project_name} -> {project_dst}")
        else:
            project_dst.mkdir(parents=True, exist_ok=True)
            print(f"  Created empty project dir for {project_name}")

        # Copy DuckDB database
        db_src = DATA_DIR / "databases" / f"{db_name}.duckdb"
        db_dst = domain_dir / f"{db_name}.duckdb"
        if db_src.exists():
            shutil.copy2(db_src, db_dst)
            print(f"  Copied database {db_name}.duckdb -> {db_dst}")
        else:
            print(f"  WARNING: Database {db_name}.duckdb not found at {db_src}")

    # Copy per-task data
    seen_task_ids = set()
    for task in tasks:
        task_id = task["task_id"]
        if task_id in seen_task_ids:
            continue
        seen_task_ids.add(task_id)

        task_src = CLONE_DIR / "tasks" / task_id
        task_dst = DATA_DIR / "tasks" / task_id

        if task_dst.exists():
            shutil.rmtree(task_dst)
        task_dst.mkdir(parents=True, exist_ok=True)

        # Copy setup.sh
        setup_sh = task_src / "setup.sh"
        if setup_sh.exists():
            shutil.copy2(setup_sh, task_dst / "setup.sh")

        # Copy solution.sh (for testing/verification)
        solution_sh = task_src / "solution.sh"
        if solution_sh.exists():
            shutil.copy2(solution_sh, task_dst / "solution.sh")

        # Copy solutions/ directory (used by solution.sh)
        solutions_dir = task_src / "solutions"
        if solutions_dir.exists():
            shutil.copytree(solutions_dir, task_dst / "solutions")

        # Copy seeds/ (reference CSVs for evaluation)
        seeds_dir = task_src / "seeds"
        if seeds_dir.exists():
            shutil.copytree(seeds_dir, task_dst / "seeds")

        # Copy tests/ (manual test SQL files, excluding AUTO-generated ones)
        tests_dir = task_src / "tests"
        if tests_dir.exists():
            tests_dst = task_dst / "tests"
            tests_dst.mkdir(parents=True, exist_ok=True)
            for sql_file in tests_dir.glob("*.sql"):
                if not sql_file.name.startswith("AUTO_"):
                    shutil.copy2(sql_file, tests_dst / sql_file.name)

        print(f"  Prepared task data: {task_id}")

    # Copy shared scripts (needed for evaluation in sandbox)
    scripts_src = CLONE_DIR / "shared" / "scripts"
    scripts_dst = DATA_DIR / "scripts"
    if scripts_dst.exists():
        shutil.rmtree(scripts_dst)
    shutil.copytree(scripts_src, scripts_dst)
    print(f"  Copied shared scripts -> {scripts_dst}")

    # Copy agent config (system prompt for agents)
    config_src = CLONE_DIR / "shared" / "config"
    config_dst = DATA_DIR / "config"
    if config_dst.exists():
        shutil.rmtree(config_dst)
    shutil.copytree(config_src, config_dst)
    print(f"  Copied agent config -> {config_dst}")


def main():
    print("=== ADE-Bench Data Preparation ===\n")

    # Step 1: Clone repo
    clone_repo()

    # Step 2: Download DuckDB databases
    download_databases()

    # Step 3: Parse tasks
    print("\nParsing task.yaml files...")
    tasks = parse_tasks()
    print(f"\nFound {len(tasks)} tasks (DuckDB+dbt variant, all prompt variants)")

    # Step 4: Organize data
    print("\nOrganizing data for bucket upload...")
    organize_data(tasks)

    print(f"\n=== Done! Data ready at {DATA_DIR} ===")
    print(f"Upload to bucket with: openreward bucket upload {DATA_DIR} ade-bench/")


if __name__ == "__main__":
    main()
