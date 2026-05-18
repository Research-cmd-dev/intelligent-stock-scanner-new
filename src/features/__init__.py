"""Feature engineering for the Stock Finder Agent.

Three submodules:

- :mod:`alpha158` — pandas implementation of a representative subset of
  Qlib's Alpha158 factors (K-line shape, returns, MAs, volatility,
  volume, position-of-extreme). Pass-through to Qlib's real
  ``Alpha158`` handler is exposed when ``pyqlib`` is importable and a
  Qlib data directory is configured.
- :mod:`custom` — features specific to this project: scanner-signal
  derivatives, narrative score (when historical news is available),
  and sector-relative context.
- :mod:`evaluator` — panel construction + cross-sectional information
  coefficient (Spearman IC) per feature.

The :func:`build_feature_evaluation` entry point in :mod:`pipeline`
runs the whole flow end-to-end and returns a :class:`FeatureEvaluation`
that the backtest report consumes directly.
"""

from __future__ import annotations

from .evaluator import FeatureEvaluation, FeatureStats, evaluate_features
from .pipeline import build_feature_evaluation

__all__ = [
    "FeatureEvaluation",
    "FeatureStats",
    "build_feature_evaluation",
    "evaluate_features",
]
