"""Metric parsing — extract METRIC name=value lines from command output."""

from __future__ import annotations

import re

# Pattern: METRIC name=value (supports integers, floats, scientific notation, unicode µ)
_METRIC_LINE_RE = re.compile(
    r"^METRIC\s+([A-Za-z0-9_.\-\u00b5]+)\s*=\s*(-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*$"
)


def parse_metric_lines(output: str) -> dict[str, float]:
    """
    Parse METRIC name=value lines from command output.

    Example output:
        Running benchmark...
        METRIC latency_ms=142.5
        METRIC throughput_qps=1024
        METRIC memory_mb=256

    Returns:
        Dict mapping metric names to float values.
    """
    metrics: dict[str, float] = {}
    for line in output.splitlines():
        line = line.strip()
        match = _METRIC_LINE_RE.match(line)
        if match:
            name = match.group(1)
            value = float(match.group(2))
            if name and value == value:  # not NaN
                metrics[name] = value
    return metrics


def format_metric(name: str, value: float) -> str:
    """Format a metric line for output."""
    return f"METRIC {name}={value}"
