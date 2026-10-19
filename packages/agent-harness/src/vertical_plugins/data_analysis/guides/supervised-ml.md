# Supervised machine learning

- Confirm target meaning, feature availability at prediction time and task type.
- Split before fitting preprocessing. Use chronological splits for temporal data,
  group splits for related entities, and stratification when classification permits.
- Keep imputation, encoding, scaling and feature selection in sklearn pipelines.
  Resample only training folds with imbalanced-learn; never resample the test set.
- Start with DummyClassifier/DummyRegressor plus LogisticRegression or Ridge.
  Compute baseline metrics from its actual held-out predictions. Do not hard-code
  F1 or substitute training prevalence for held-out average precision.
  Compare tree boosting only when justified: HistGradientBoosting, LightGBM,
  CatBoost or XGBoost. Use CPU execution and bounded thread counts.
- Choose metrics based on class balance and task cost. Report classification
  confusion matrix and suitable F1/ROC-AUC/PR-AUC; regression MAE, RMSE and R².
- Keep the final test set isolated. Report cross-validation variation; put any
  Optuna trial budget in the script and never tune against the final test set.
- Record actual thread environment settings, not an assumed thread count.
- Assert disjoint validation groups/time order where relevant, finite metrics and
  prediction row counts. Save split definitions, seed, features, parameters,
  package versions, metrics JSON and joblib model in the task artifacts directory.
