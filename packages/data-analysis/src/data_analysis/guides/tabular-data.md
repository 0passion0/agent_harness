# Tabular data

- Start with inspect_table: it scans every row locally and returns compact column
  profiles, not a head sample. XLSX without sheet_name lists sheets first.
- Inspect count, missingness, numeric/non-numeric mixtures, finite extrema,
  approximate quartiles/cardinality, exact common/rare value frequencies and
  leading-zero identifiers. Do not reinterpret numeric-looking identifiers.
- For wide tables, use next_column_offset to page columns. Full profiles remain
  in artifacts/table_profiles/; inspect only relevant fields, not the entire JSON.
- Treat approximate distributions as exploration, not inferential results.
  Generate precise question-specific group/time/relationship aggregates next;
  inspect bounded exceptional rows only when their actual values matter.
- The reader supports UTF-8 comma-separated CSV, Parquet and explicit XLSX sheets.
  Blank/NULL counts are exact; literal NA text remains data. Excel formulas are
  reported as formulas, not evaluated values; ask for evaluated data if required.
- For large data, use DuckDB/Polars lazy column selection and predicates; never
  load a whole large CSV merely to print it. Heavy all-pairs correlations,
  interactions and row duplication checks run only when the question needs them.
- For counts, means, quantiles, conditional counts and grouped summaries, prefer
  one DuckDB/Polars aggregation over bespoke row loops, full value-frequency
  dictionaries or accumulating every numeric value in arrays. Materialize only
  bounded aggregated data needed for plotting; full data remains on disk.

- Inspect Excel sheet names before selecting a sheet; check headers and units.
- Use pandas/openpyxl for ordinary Excel, Polars lazy scans for large CSV/Parquet,
  and DuckDB for SQL aggregation and joins. Preserve formulas/styles with openpyxl.
- Summarize shape, dtypes, missing counts, duplicate keys and bounded samples.
  Keep identifiers as identifiers; do not turn leading-zero codes into numbers.
- Confirm join cardinality and compare row counts before/after each join.
- Handle missing values and duplicates according to the task's meaning. Record
  affected columns and counts; do not apply whole-table generic cleaning.
- Write intermediate tables to the current artifacts directory. Do not overwrite
  input files. Validate output schema and row counts before reporting success.
