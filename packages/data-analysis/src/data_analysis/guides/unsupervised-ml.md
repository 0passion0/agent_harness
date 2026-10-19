# Unsupervised machine learning

- Clarify whether the goal is grouping, visualization or anomaly ranking.
- Select meaningful features and handle missingness explicitly; fit scaling for
  distance-based methods. Keep preprocessing and estimator reproducible.
- Begin with sklearn PCA/KMeans or a simple anomaly baseline. Use PyOD only when
  its method fits the data and objective; bound cluster/search complexity.
- Examine cluster sizes, stability and applicable silhouette metrics. Do not
  treat a two-dimensional embedding as proof of distinct populations.
- Report anomaly scores as model evidence, not verified errors or wrongdoing.
- Assert label/score counts match inputs and scores are finite. Save feature
  selection, seed, preprocessing, assignments and diagnostic plots.
