"""数据表结构、缺失情况及统计类型的确定性画像模块。"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import duckdb
from openpyxl import load_workbook

logger = logging.getLogger(__name__)

# 支持的表文件后缀集合
SUPPORTED_FORMATS: frozenset[str] = frozenset({".csv", ".parquet", ".xlsx"})
# 列画像单页展示的最大列数
PROFILE_PAGE_SIZE = 10
# 限制单页上下文 Token 预算对应的字符大小上限
PROFILE_CONTEXT_LIMIT = 12000


def profile_table(
    file_path: Path,
    run_dir: Path,
    sheet_name: str | None = None,
    column_offset: int = 0,
) -> dict[str, Any]:
    """在本地全量扫描数据表行，返回受大小限制的列统计画像分页。

    :param file_path: 本地 UTF-8 CSV、Parquet 或 XLSX 数据文件物理路径。
    :type file_path: pathlib.Path
    :param run_dir: 当前任务或会话用于存储画像产物和临时溢出文件的物理目录。
    :type run_dir: pathlib.Path
    :param sheet_name: 需要扫描的 XLSX 工作表名称；若未指定则仅返回可用工作表列表。
    :type sheet_name: str | None
    :param column_offset: 从零开始的列分页偏移量，绝非行采样偏移量。
    :type column_offset: int
    :return: 包含整表行数统计及指定列画像子集的字典。
    :rtype: dict[str, typing.Any]
    :raises ValueError: 当格式不支持、分页偏移量越界、表头非法或产物越界时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "table_profile_started",
        extra={
            "event_fields": {
                "file_path": str(file_path),
                "sheet_name": sheet_name,
                "column_offset": column_offset,
            }
        },
    )
    # 第一步：校验数据文件格式及列偏移量参数合法性
    resolved_file = file_path.resolve()
    if not resolved_file.is_file():
        raise ValueError(f"Data file does not exist: {file_path}")
    if resolved_file.suffix.lower() not in (".csv", ".parquet", ".xlsx"):
        raise ValueError("Supported profile formats: UTF-8 comma-separated CSV, Parquet and XLSX")
    if column_offset < 0:
        raise ValueError("column_offset must be nonnegative")

    # 第二步：针对未指定 sheet_name 的 XLSX 文件，只读加载并返回所有工作表供用户选择
    if resolved_file.suffix.lower() == ".xlsx" and sheet_name is None:
        with resolved_file.open("rb") as source:
            workbook = load_workbook(source, read_only=True, data_only=False)
            names = list(workbook.sheetnames)
            workbook.close()
        logger.info(
            "table_profile_xlsx_sheets_discovered",
            extra={"event_fields": {"sheet_count": len(names)}},
        )
        return {
            "source": str(resolved_file),
            "sheet_names": names,
            "next_action": "Choose sheet_name to scan all rows",
        }

    # 第三步：计算源文件与工作表 SHA256 摘要，检查本地产物缓存
    with resolved_file.open("rb") as source:
        source_hash = hashlib.file_digest(source, "sha256").hexdigest()
    sheet_hash = hashlib.sha256(
        (str(resolved_file) + "\0" + (sheet_name or "")).encode("utf-8")
    ).hexdigest()[:8]

    directory = (run_dir / "artifacts" / "table_profiles").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{source_hash[:16]}_{sheet_hash}.json"

    # 第四步：若已有画像缓存则直接读取；否则使用 DuckDB 计算全表统计指标
    if destination.exists():
        logger.info(
            "table_profile_cache_hit", extra={"event_fields": {"cache_file": str(destination)}}
        )
        profile: dict[str, Any] = json.loads(destination.read_text(encoding="utf-8"))
    else:
        input_file = resolved_file
        formula_cells = 0
        if resolved_file.suffix.lower() == ".xlsx":
            input_file = directory / f"{source_hash[:16]}_{sheet_hash}.csv"
            with resolved_file.open("rb") as source:
                workbook = load_workbook(source, read_only=True, data_only=False)
                try:
                    if sheet_name not in workbook.sheetnames:
                        raise ValueError(
                            f"Worksheet {sheet_name!r} does not exist. Available sheets: {json.dumps(workbook.sheetnames, ensure_ascii=False)}"
                        )
                    rows = workbook[sheet_name].iter_rows()
                    first_row = next(rows, None)
                    if first_row is None:
                        raise ValueError("XLSX worksheet is empty")
                    headers = [cell.value for cell in first_row]
                    if any(
                        not isinstance(name, str) or not name.strip() for name in headers
                    ) or len(set(headers)) != len(headers):
                        raise ValueError(
                            "XLSX requires unique nonempty text column headers in the first row"
                        )
                    with input_file.open("w", encoding="utf-8", newline="") as output:
                        writer = csv.writer(output)
                        writer.writerow(headers)
                        for row in rows:
                            formula_cells += sum(cell.data_type == "f" for cell in row)
                            writer.writerow([cell.value for cell in row])
                finally:
                    workbook.close()

        spill = directory / "duckdb_spill"
        spill.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(
            config={"memory_limit": "512MB", "threads": "4", "temp_directory": str(spill)}
        ) as database:
            if input_file.suffix.lower() == ".parquet":
                relation = database.read_parquet(str(input_file))
            else:
                with input_file.open(encoding="utf-8-sig", newline="") as source:
                    header_row = next(csv.reader(source), None)
                    if header_row is None:
                        raise ValueError("CSV file is empty")
                    headers = header_row
                if any(not name.strip() for name in headers) or len(set(headers)) != len(headers):
                    raise ValueError("CSV requires unique nonempty column headers")
                relation = database.sql(
                    "SELECT * FROM read_csv(?, header=true, delim=',', all_varchar=true, nullstr='', encoding='utf-8')",
                    params=[str(input_file)],
                )

            relation.create_view("source")
            names = relation.columns
            types = [str(dtype) for dtype in relation.types]
            expressions = ["count(*)"]
            for name in names:
                identifier = '"' + name.replace('"', '""') + '"'
                numeric = f"TRY_CAST({identifier} AS DOUBLE)"
                finite = f"isfinite({numeric})"
                expressions.extend(
                    [
                        f"count(*) FILTER (WHERE {identifier} IS NULL)",
                        f"approx_count_distinct({identifier})",
                        f"count(*) FILTER (WHERE {finite})",
                        f"count(*) FILTER (WHERE {numeric} IS NOT NULL AND NOT {finite})",
                        f"count(*) FILTER (WHERE regexp_matches(CAST({identifier} AS VARCHAR), '^[+-]?0[0-9]+'))",
                        f"min({numeric}) FILTER (WHERE {finite})",
                        f"max({numeric}) FILTER (WHERE {finite})",
                        f"avg({numeric}) FILTER (WHERE {finite})",
                        f"stddev_samp({numeric}) FILTER (WHERE {finite})",
                        f"approx_quantile({numeric}, [0.25, 0.5, 0.75]) FILTER (WHERE {finite})",
                    ]
                )

            stats = database.sql("SELECT " + ", ".join(expressions) + " FROM source").fetchone()
            assert stats is not None
            row_count = stats[0]
            columns = []
            for index, (name, dtype) in enumerate(zip(names, types, strict=False)):
                (
                    nulls,
                    distinct,
                    numeric_count,
                    nonfinite,
                    leading_zero,
                    minimum,
                    maximum,
                    mean,
                    std,
                    quantiles,
                ) = stats[1 + index * 10 : 11 + index * 10]
                column: dict[str, Any] = {
                    "name": name,
                    "storage_type": dtype,
                    "missing_count": nulls,
                    "distinct_estimate": distinct,
                    "numeric_count": numeric_count,
                    "nonfinite_count": nonfinite,
                    "non_numeric_count": row_count - nulls - numeric_count - nonfinite,
                    "leading_zero_count": leading_zero,
                }
                if numeric_count:
                    column["numeric_subset"] = {
                        "min": minimum,
                        "max": maximum,
                        "mean": mean,
                        "std": std,
                        "approx_quartiles": quantiles,
                    }
                if distinct <= 5000:
                    identifier = '"' + name.replace('"', '""') + '"'
                    grouped = f"SELECT CAST({identifier} AS VARCHAR) AS value, count(*) AS n FROM source WHERE {identifier} IS NOT NULL GROUP BY 1"
                    for key, order, limit in (
                        ("frequent_values", "n DESC, value", 3),
                        ("rare_values", "n ASC, value", 2),
                    ):
                        values = database.sql(
                            grouped + f" ORDER BY {order} LIMIT {limit}"
                        ).fetchall()
                        column[key] = [
                            {"value": value[:80], "length": len(value), "count": count}
                            for value, count in values
                        ]
                else:
                    column["value_counts"] = (
                        "High cardinality: request a targeted aggregate instead of listing values"
                    )
                columns.append(column)

        profile = {
            "source": str(resolved_file),
            "source_sha256": source_hash,
            "sheet_name": sheet_name,
            "row_count": row_count,
            "column_count": len(columns),
            "formula_cells": formula_cells,
            "coverage": "All rows scanned; counts/extrema/mean/std exact over finite numeric subset; distinct and quartiles approximate",
            "missing_definition": "Blank/NULL only; NA-like text preserved. CSV/XLSX values remain lexical. Formulas are text, not evaluated.",
            "columns": columns,
        }
        destination.write_text(
            json.dumps(profile, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "table_profile_computed",
            extra={"event_fields": {"row_count": row_count, "column_count": len(columns)}},
        )

    # 第五步：根据列偏移量截取当页列画像，并控制单页上下文 Token 上限
    if column_offset >= profile["column_count"] and profile["column_count"] > 0:
        raise ValueError("column_offset must be smaller than column_count")
    page = profile["columns"][column_offset : column_offset + PROFILE_PAGE_SIZE]
    response: dict[str, Any] = {key: value for key, value in profile.items() if key != "columns"}
    try:
        response["profile_file"] = destination.relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        response["profile_file"] = str(destination)
    response["column_offset"] = column_offset
    response["columns"] = [
        dict(column, name=column["name"][:120], name_truncated=len(column["name"]) > 120)
        for column in page
    ]
    response["next_column_offset"] = (
        column_offset + len(page) if column_offset + len(page) < profile["column_count"] else None
    )

    # 第六步：若当页输出超限，逐列回退并动态更新下一页偏移
    while (
        len(json.dumps(response, ensure_ascii=False)) > PROFILE_CONTEXT_LIMIT
        and len(response["columns"]) > 1
    ):
        response["columns"].pop()
        response["next_column_offset"] = column_offset + len(response["columns"])

    if len(json.dumps(response, ensure_ascii=False)) > PROFILE_CONTEXT_LIMIT:
        raise ValueError(
            "Profile metadata exceeds the context budget; inspect its saved JSON locally"
        )
    return response


__all__ = [
    "PROFILE_CONTEXT_LIMIT",
    "PROFILE_PAGE_SIZE",
    "profile_table",
]
