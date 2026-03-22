# ADE-Bench

[![OpenReward Environment](https://img.shields.io/badge/%E2%AD%90%20OpenReward-Environment-f7e6cc)](https://openreward.ai/GeneralReasoning/ADE-Bench)

## Description

**ADE-Bench** (Analytics and Data Engineering Benchmark) is an environment for evaluating AI agents on analytics and data engineering tasks using dbt projects and DuckDB databases. Agents work in isolated containers to fix bugs, create models, refactor projects, and debug data quality issues in realistic dbt workflows.

## Capabilities

- Bash command execution in a dbt + DuckDB environment
- Full dbt project access (models, macros, packages, profiles)
- DuckDB database access for data inspection and querying
- Automated evaluation via dbt tests comparing output against reference data

## Compute Requirements

Sandbox: 1 CPU / 2GB RAM per session. Network access enabled (for `dbt deps`).

## License

[Apache License 2.0](https://github.com/dbt-labs/ade-bench/blob/main/LICENSE)

## Tasks

Single `test` split with 46 tasks across 9 domains:

| Domain | Count | Description |
|--------|-------|-------------|
| Airbnb | 11 | Airbnb analytics (reviews, listings, NPS) |
| Formula 1 | 11 | F1 racing data (drivers, results, standings) |
| Analytics Engineering | 8 | General AE tasks (refactoring, best practices) |
| Asana | 5 | Project management data |
| QuickBooks | 4 | Accounting/financial data |
| Intercom | 3 | Customer messaging data |
| Simple | 2 | Lightweight diagnostic tasks |
| Shopify Analytics | 1 | E-commerce analytics |
| Workday | 1 | HR/workforce data |

Tasks span easy, medium, and hard difficulty. Some tasks have multiple prompt variants (base, medium, hard) which appear as separate tasks.

## Reward Structure

Binary reward (0.0 or 1.0). Evaluation runs `dbt test --select "test_type:singular"` which includes:
- **Existence tests**: Verify expected tables exist
- **Equality tests**: Row-by-row comparison of agent output tables against reference CSVs (supports column filtering and alternate correct answers)
- **Manual tests**: Task-specific SQL assertions (e.g., checking model materialization, source references)

All tests must pass for reward=1.0.

## Data

Source: [dbt-labs/ade-bench](https://github.com/dbt-labs/ade-bench) on GitHub.
DuckDB databases: [Google Drive](https://drive.google.com/drive/folders/1CNS_8mf81to02868HA-celmcPEFu4BPE).

Each domain has a dbt project and DuckDB database. Per-task data includes setup scripts, reference CSVs (seeds), and test SQL files.

## Tools

- `bash` — Execute bash commands in the dbt project directory (`/app/`)
- `submit` — Submit solution for evaluation (runs dbt tests, returns binary reward)

## Time Horizon

Multi-turn. Agents typically need 5-30 tool calls depending on task complexity. Easy tasks (renaming a model) may need ~5 calls; hard tasks (debugging complex dbt package issues, creating new analytical models) may need 20+.

## Environment Difficulty

- Easy: ~30% of tasks (simple renames, straightforward fixes)
- Medium: ~50% of tasks (model creation, refactoring, data inspection)
- Hard: ~20% of tasks (complex debugging, multi-file changes, analytical reasoning)

## Safety

Tasks involve SQL queries and file modifications in isolated containers. No access to external systems beyond dbt package registries. All execution is sandboxed.
