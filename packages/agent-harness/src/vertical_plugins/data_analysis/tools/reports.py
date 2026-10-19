"""数据分析报告文件的生成、产物一致性校验及原子发布模块。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from vertical_plugins.data_analysis.prompts.report import CORE_REPORT_SECTIONS

logger = logging.getLogger(__name__)

# 兼容新老报告标题的章节等价组：(主推荐标题, 兼容旧标题)
REQUIRED_SECTION_ALIASES: tuple[tuple[str, ...], ...] = (
    ("研究目标", "研究问题"),
    ("数据概况", "数据说明"),
    ("分析方法",),
    ("分析结果", "结果"),
    ("讨论与局限",),
    ("结论",),
)

# 保持向后兼容导出的章节名称元组
REPORT_SECTIONS: tuple[str, ...] = CORE_REPORT_SECTIONS


def validate_report(
    markdown: str,
    evidence_files: list[str],
    run_dir: Path,
    executions: list[dict[str, Any]],
) -> dict[str, Any]:
    """核查报告核心章节结构、本地证据文件引用、产物一致性与真实执行哈希凭证。

    :param markdown: 包含核心二级标题的完整中文 Markdown 报告。
    :type markdown: str
    :param evidence_files: 相对任务目录的凭证路径列表（含代码与 JSON 结果）。
    :type evidence_files: list[str]
    :param run_dir: 当前分析会话的绝对输出根目录。
    :type run_dir: pathlib.Path
    :param executions: 记录的脚本执行凭证与产物哈希列表。
    :type executions: list[dict[str, typing.Any]]
    :return: 包含报告 SHA256 和各凭证 SHA256 的校验记录字典。
    :rtype: dict[str, typing.Any]
    :raises ValueError: 报告缺失核心二级标题、缺少实质内容、凭证越界或与真实执行产物不一致时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info(
        "report_validation_started", extra={"event_fields": {"evidence_count": len(evidence_files)}}
    )
    resolved_run_dir = run_dir.resolve()

    # 第一步：提取所有二级标题与对应正文，核查核心段落存在且均有实质内容
    sections = {
        title.strip(): body.strip()
        for title, body in re.findall(
            r"^##\s+(.+?)\s*\n(.*?)(?=^##\s|\Z)", markdown, re.MULTILINE | re.DOTALL
        )
    }
    headings = set(re.findall(r"^##\s+(.+?)\s*$", markdown, re.MULTILINE))
    missing_groups: list[str] = []
    matched_titles: list[str] = []
    for alias_group in REQUIRED_SECTION_ALIASES:
        matched = next((name for name in alias_group if name in headings), None)
        if matched is None:
            missing_groups.append(alias_group[0])
        else:
            matched_titles.append(matched)

    if missing_groups:
        raise ValueError(f"Missing report sections: {', '.join(missing_groups)}")
    if any(not sections.get(name, "").strip() for name in matched_titles):
        raise ValueError("Every required report section must contain substantive content")

    # 第二步：核查所有证据文件，限制在任务目录内，确认非空并计算 SHA256 摘要
    hashes: dict[str, str] = {}
    paths: dict[str, Path] = {}
    json_payloads: dict[str, Any] = {}
    for name in evidence_files:
        path = (resolved_run_dir / name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(resolved_run_dir):
            raise ValueError(f"Evidence must stay inside the task directory: {name}")
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing or empty evidence file: {name}")
        if path.suffix == ".json":
            loaded_json = json.loads(path.read_text(encoding="utf-8"))
            # JSON 解码仅接受有限实数；禁止 NaN 或 Infinity
            json.dumps(loaded_json, allow_nan=False)
            json_payloads[name] = loaded_json
        paths[name] = path
        with path.open("rb") as source:
            hashes[name] = hashlib.file_digest(source, "sha256").hexdigest()

    # 第三步：确认证据中必须同时包含 Python 脚本与 JSON 结果文件
    scripts = {name for name, p in paths.items() if p.suffix == ".py"}
    results = {name for name, p in paths.items() if p.suffix == ".json"}
    if not scripts or not results:
        raise ValueError("Report evidence must include a Python script and JSON results")

    # 第四步：与真实成功执行的运行记录匹配脚本哈希与产物哈希
    verified_results: set[str] = set()
    verified_scripts: set[str] = set()
    for execution in executions:
        script = execution.get("script", "")
        exit_code = execution.get("exit_code")
        script_sha = execution.get("script_sha256")
        if exit_code == 0 and script in scripts and script_sha == hashes.get(script):
            verified_scripts.add(script)
            artifact_map = execution.get("artifact_sha256", {})
            verified_results.update(
                name for name in results if artifact_map.get(name) == hashes.get(name)
            )
    if verified_scripts != scripts or verified_results != results:
        raise ValueError(
            "Evidence must match successful analysis executions; rerun edited scripts/results"
        )

    # 第五步：核对 Markdown 中的本地引用，确保所有证据文件均在报告中以相对链接引用
    linked_paths: set[Path] = set()
    for target in re.findall(r"!?\[[^\]\n]*\]\(([^)\n]+)\)", markdown):
        url = urlsplit(target)
        if url.scheme in ("http", "https") or target.startswith("#"):
            continue
        path = (resolved_run_dir / "artifacts" / unquote(url.path)).resolve()
        if url.scheme or not path.is_relative_to(resolved_run_dir):
            raise ValueError(f"Report link must stay inside the task directory: {target}")
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing or empty report link: {target}")
        linked_paths.add(path)
    if not set(paths.values()).issubset(linked_paths):
        raise ValueError(
            "Link every evidence file from the report using paths relative to artifacts/report.md"
        )

    # 第六步：计算报告整体 SHA256 哈希并返回校验字典
    report_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    logger.info(
        "report_validation_completed", extra={"event_fields": {"report_sha": report_sha[:16]}}
    )
    return {"report_sha256": report_sha, "evidence_sha256": hashes}


def save_report(
    markdown: str,
    evidence_files: list[str],
    run_dir: Path,
    executions: list[dict[str, Any]],
) -> Path:
    """校验并原子发布 Markdown 报告及其关联执行证据记录。

    :param markdown: 完整报告正文 Markdown。
    :type markdown: str
    :param evidence_files: 报告引用的相对任务目录的代码与结果路径。
    :type evidence_files: list[str]
    :param run_dir: 当前会话输出根目录。
    :type run_dir: pathlib.Path
    :param executions: 记录的脚本执行凭证。
    :type executions: list[dict[str, typing.Any]]
    :return: 已发布的 Markdown 报告绝对路径。
    :rtype: pathlib.Path
    :raises ValueError: 报告校验失败或目标输出路径非法越界。
    :author: tongliga
    :date: 2026-10-19
    """
    logger.info("report_save_started")
    resolved_run_dir = run_dir.resolve()

    # 第一步：调用 validate_report 校验报告结构、内容和执行凭证
    validation = validate_report(markdown, evidence_files, resolved_run_dir, executions)
    artifacts_dir = resolved_run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report = artifacts_dir / "report.md"
    validation_file = artifacts_dir / "report_validation.json"

    # 第二步：校验目标文件路径是否严格位于会话输出目录内
    if not report.resolve().is_relative_to(
        resolved_run_dir
    ) or not validation_file.resolve().is_relative_to(resolved_run_dir):
        raise ValueError("Report destinations must stay inside the task directory")

    # 第三步：使用临时文件并原子替换，确保写入过程的安全与一致性
    temporary = report.with_suffix(".md.tmp")
    if temporary.is_symlink():
        raise ValueError("Report temporary path must not be a symlink")
    temporary.write_bytes(markdown.encode("utf-8"))
    validation_file.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(report)
    logger.info("report_save_completed", extra={"event_fields": {"report_path": str(report)}})
    return report


__all__ = [
    "CORE_REPORT_SECTIONS",
    "REPORT_SECTIONS",
    "REQUIRED_SECTION_ALIASES",
    "save_report",
    "validate_report",
]
