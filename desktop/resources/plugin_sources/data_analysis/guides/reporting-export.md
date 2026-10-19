# Reporting and export

The final deliverable is artifacts/report.md, published through finalize_report.
Use these exact level-two headings (subheadings are unrestricted):

```markdown
# <Research report title>
## 研究问题
## 数据说明
## 分析方法
## 结果
## 讨论与局限
## 结论
## 复现信息
```

- Answer the research question early; distinguish executed results, assumptions,
  exploratory findings and unverified limitations. Give effects, units, intervals,
  sample sizes and appropriate metrics. Never replace interpretation with raw logs.
- Reference local artifacts relative to artifacts/report.md: results.json,
  figure.png, ../scripts/analysis.py. Use ordinary Markdown links/images, no
  absolute or virtual /runs/ paths. Explain each figure's scientific implication.
- Pass evidence_files relative to the session directory: scripts/analysis.py,
  artifacts/results.json and any linked figures. Include at least executed Python
  and result JSON. All selected scripts/results must match successful run_python
  evidence; rerun edited files. Every evidence file must have a Markdown link.
- Call finalize_report only after reading actual results. It checks required
  sections, evidence hashes and links before writing the report; do not deliver
  the report through write_file or claim success before finalization succeeds.
- Do not fabricate citations. External literature claims require actual provided
  sources; distinguish them from conclusions computed from the user's dataset.

- Read saved metrics/tables and build conclusions only from executed results.
- Quantitative figure interpretations must use saved numeric aggregates (for
  calibration, save bin counts, predicted means and observed fractions in JSON).
  Do not guess coordinates from a filename or invent values absent from evidence.
- Describe actual fitted behavior, not assumed library defaults. Record relevant
  fitted attributes such as do_early_stopping_ when discussing internal validation.
- Fold composition can suggest explanations for metric variation; three folds
  cannot establish its cause or prove that one held-out split is representative.
- Use openpyxl for workbook formatting, Matplotlib/Seaborn for PNG plots, and
  Plotly for interactive HTML when useful. Label axes, units and sample sizes.
- Preserve original workbooks. Put exports in the current artifacts directory;
  use numeric Excel cells for numbers and text cells for identifiers.
- Reopen exported tables/workbooks and check sheets, columns and row counts.
  Check plot files exist and are nonempty before linking them.
- Include input paths, handling decisions, methods, metrics, uncertainty and
  limitations in report.md. Link scripts and artifacts for reproduction.
- Separate completed analysis from skipped/unverified work. Do not imply that
  successful script execution guarantees a statistically reliable conclusion.
