"""Skywatch: local weather-watching and analysis.

The physics models in the data do the forecasting. The LLM is an analyst reading
instruments: it synthesises, detects disagreement between models, and narrates.
It is never asked to do arithmetic over raw data, and never to predict.
"""

__version__ = "0.1.0"
