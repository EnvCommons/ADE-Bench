"""Utility for generating ADE-Bench solution seed tests.

Ported from ade_bench/utils/test_generator.py in https://github.com/dbt-labs/ade-bench
"""

from typing import Optional

EQUALITY_MACRO_FILENAME = "ade_bench_equality_test.sql"

_EQUALITY_MACRO_CONTENT = """\
{% macro ade_bench_equality_test(table_name, answer_keys, cols_to_exclude=[]) %}
    {% if not execute %}
        select 1 where 1=0
    {% else %}
        {% set ns = namespace(matched=false) %}
        {% set actual_rel = load_relation(ref(table_name)) %}

        {% if actual_rel is not none %}
            {% set actual_columns = adapter.get_columns_in_relation(actual_rel) %}
            {% set exclude_lower = cols_to_exclude | map('lower') | list %}

            {%- set actual_col_names = [] -%}
            {%- for col in actual_columns -%}
                {%- if col.name | lower not in exclude_lower -%}
                    {%- do actual_col_names.append(col.name | lower) -%}
                {%- endif -%}
            {%- endfor -%}
            {% set actual_set = actual_col_names | sort %}

            {% for answer_key in answer_keys %}
                {% if not ns.matched %}
                    {% set seed_rel = load_relation(ref(answer_key)) %}
                    {% if seed_rel is not none %}
                        {% set seed_columns = adapter.get_columns_in_relation(seed_rel) %}

                        {%- set seed_col_names = [] -%}
                        {%- for col in seed_columns -%}
                            {%- if col.name | lower not in exclude_lower -%}
                                {%- do seed_col_names.append(col.name | lower) -%}
                            {%- endif -%}
                        {%- endfor -%}
                        {% set seed_set = seed_col_names | sort %}

                        {% if actual_set == seed_set %}
                            {%- set compare_cols = [] -%}
                            {%- for col in actual_columns -%}
                                {%- if col.name | lower not in exclude_lower -%}
                                    {%- do compare_cols.append(col.quoted) -%}
                                {%- endif -%}
                            {%- endfor -%}
                            {% set compare_cols_csv = compare_cols | join(', ') %}

                            {% set query %}
                                with a_minus_b as (
                                    select {{ compare_cols_csv }} from {{ ref(answer_key) }}
                                    except
                                    select {{ compare_cols_csv }} from {{ ref(table_name) }}
                                ),
                                b_minus_a as (
                                    select {{ compare_cols_csv }} from {{ ref(table_name) }}
                                    except
                                    select {{ compare_cols_csv }} from {{ ref(answer_key) }}
                                ),
                                unioned as (
                                    select * from a_minus_b
                                    union all
                                    select * from b_minus_a
                                )
                                select count(*) as diff_count from unioned
                            {% endset %}

                            {% set result = run_query(query) %}
                            {% if result.rows[0][0] == 0 %}
                                {% set ns.matched = true %}
                            {% endif %}
                        {% endif %}
                    {% endif %}
                {% endif %}
            {% endfor %}
        {% endif %}

        {% if ns.matched %}
            select 1 where 1=0
        {% else %}
            select 1
        {% endif %}
    {% endif %}
{% endmacro %}
"""


def get_equality_macro_content() -> str:
    """Return the Jinja macro that implements equality test logic."""
    return _EQUALITY_MACRO_CONTENT


def generate_existence_test(table_name: str) -> str:
    """Generate an existence test for a solution seed table."""
    return f"""{{% set table_name = '{table_name}' %}}



-------------------------------------
---- DO NOT EDIT BELOW THIS LINE ----
{{% set answer_key = 'solution__' + table_name %}}

{{% set table_a = load_relation(ref(answer_key)) %}}
{{% set table_b = load_relation(ref(table_name)) %}}

{{% if table_a is none or table_b is none %}}
    select 1
{{% else %}}
    select 1 where false
{{% endif %}}
"""


def generate_equality_test(
    table_name: str,
    include_columns: Optional[list[str]] = None,
    exclude_columns: Optional[list[str]] = None,
    alternates: Optional[list[str]] = None,
) -> str:
    """Generate an equality test for a solution seed table."""
    answer_keys = [f"solution__{table_name}"]
    if alternates:
        for alt in alternates:
            answer_keys.append(f"solution__{alt}")

    answer_keys_jinja = ", ".join(f"'{k}'" for k in answer_keys)

    include_list = (
        ",\n    ".join([f"'{col}'" for col in include_columns]) if include_columns else ""
    )
    exclude_list = (
        ",\n    ".join([f"'{col}'" for col in exclude_columns]) if exclude_columns else ""
    )

    depends_on_lines = "\n".join(f"-- depends_on: {{{{ ref('{k}') }}}}" for k in answer_keys)

    return f"""-- Define columns to compare
{{% set table_name = '{table_name}' %}}
{{% set answer_keys = [{answer_keys_jinja}] %}}

{{% set cols_to_include = [
    {include_list}
] %}}

{{% set cols_to_exclude = [
    {exclude_list}
] %}}


-------------------------------------
---- DO NOT EDIT BELOW THIS LINE ----
-- depends_on: {{{{ ref(table_name) }}}}
{depends_on_lines}

{{{{ ade_bench_equality_test(table_name=table_name, answer_keys=answer_keys, cols_to_exclude=cols_to_exclude) }}}}
"""


def generate_solution_tests(
    solution_seeds: list[dict],
) -> tuple[dict[str, str], str]:
    """Generate all AUTO test files and the equality macro.

    Args:
        solution_seeds: List of seed configs from task.yaml, each with at least
            'table_name' and optionally 'include_columns', 'exclude_columns',
            'exclude_tests', 'alternates'.

    Returns:
        Tuple of (test_files dict {filename: content}, macro_content)
    """
    test_files: dict[str, str] = {}

    for seed_config in solution_seeds:
        table_name = seed_config["table_name"]
        excluded_tests = set(seed_config.get("exclude_tests", []))

        if "existence_test" not in excluded_tests:
            filename = f"AUTO_{table_name}_existence.sql"
            test_files[filename] = generate_existence_test(table_name)

        if "equality_test" not in excluded_tests:
            filename = f"AUTO_{table_name}_equality.sql"
            test_files[filename] = generate_equality_test(
                table_name,
                include_columns=seed_config.get("include_columns"),
                exclude_columns=seed_config.get("exclude_columns"),
                alternates=seed_config.get("alternates"),
            )

    return test_files, get_equality_macro_content()
