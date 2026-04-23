"""
AutoResearch SDK v1.1 — Standalone Python SDK for autonomous experiment loops.

Inspired by karpathy/autoresearch: try ideas, keep what works, discard what doesn't, never stop.

Core cycle: SCAN → HYPOTHESIS → EXPERIMENT → MEASURE → INTEGRATE

Usage:
    from autoresearch import AutoResearch

    ar = AutoResearch(
        name="optimize-latency",
        metric="latency_ms",
        direction="lower",
        unit="ms",
    )
    ar.init()  # Initialize experiment
    ar.run("./benchmark.sh")  # Run baseline
    ar.log("Baseline measurement")  # Log result
"""

__version__ = "1.1.0"
__author__ = "Jose Manuel Sabarís García"

from autoresearch.core.engine import AutoResearch
from autoresearch.core.confidence import compute_confidence, describe_confidence
from autoresearch.core.metrics import parse_metric_lines

__all__ = [
    "AutoResearch",
    "compute_confidence",
    "describe_confidence",
    "parse_metric_lines",
]
