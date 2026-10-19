# Model interpretation

- Load the saved preprocessing/model and the exact training feature schema.
- Examine validation performance first; describe explanations of weak models
  with their predictive limits.
- Start with coefficients or held-out permutation importance. Use SHAP when
  local or nonlinear explanations add value; bound background/sample sizes.
- Explain the output scale, reference population and transformed feature mapping.
  Account for correlated features when interpreting importance.
- Keep explanation data separate from model fitting. Do not claim causality.
- Check explanation dimensions against the actual rows/features and save plots,
  numeric importance tables and sampling details in the task artifacts directory.
