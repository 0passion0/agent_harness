# Statistical analysis

- State the hypothesis, observational unit, units and paired/group structure.
- Report sample sizes and missing-data handling. Inspect distributions and
  assumptions before selecting SciPy, Statsmodels or Pingouin methods.
- Match independent, paired and repeated-measure methods to the study design.
- Prefer the installed library's tested statistical interfaces: for a Welch test,
  scipy.stats.ttest_ind(equal_var=False).confidence_interval() gives its matching
  interval; use statsmodels formula models for declared covariates/categories.
  Do not replace these with bespoke partial formulas or arbitrary degrees of freedom.
- For a pre/post controlled study, keep observations paired within each subject;
  compare changes and consider baseline-adjusted follow-up analysis when suitable.
  Respect batch/site effects and the number of independent experimental units.
- Diagnose residuals, variance assumptions, collinearity and model specification.
  Use a justified robust method when needed and disclose its small-sample limits.
- Compare the primary analysis with explicitly defined missing/outlier sensitivity
  analyses; retain all eligible observations in the declared primary analysis.
- Choose a small set of sensitivities that address the actual threat. Unknown
  missingness does not justify automatically adding multiple imputation: report
  the unidentified mechanism and a clearly conditional sensitivity when useful.
  Avoid an unrequested battery of similar tests or interaction models.
- A bootstrap distribution around the observed estimate is not a null distribution;
  do not derive a hypothesis-test p-value merely by counting bootstrap signs.
- Report effect sizes and confidence intervals alongside p-values. Correct for
  multiple comparisons when making a family of tests.
- Separate association from causation; disclose confounding and selection limits.
- Save test statistics, parameters and assumptions as structured results.
  Check sample sizes and finite outputs in the script before writing the report.
- Record fit.use_t and the actual interval distribution for robust regressions;
  HC3 does not automatically imply a residual-df t interval. Distinguish an
  influential point from high leverage; Cook's distance alone does not prove both.
