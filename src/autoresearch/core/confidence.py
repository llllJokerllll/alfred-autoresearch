"""Confidence computation — statistical significance of improvements."""

from __future__ import annotations

from typing import Any, Literal


def compute_confidence(
    runs: list[dict[str, Any]],
    direction: Literal["lower", "higher"] = "lower",
) -> float | None:
    """
    Compute confidence that the best improvement is real vs noise.

    Uses Median Absolute Deviation (MAD) as noise floor.
    Confidence = |best - baseline| / MAD.

    Returns:
        Confidence multiplier (e.g. 2.5 means improvement is 2.5x the noise floor).
        None if not enough runs (< 3) or MAD is zero.
    """
    usable = [
        r for r in runs
        if isinstance(r.get("metric"), (int, float))
        and float(r["metric"]) > 0
    ]
    if len(usable) < 3:
        return None

    baseline = next((r for r in usable if isinstance(r.get("metric"), (int, float))), None)
    if baseline is None:
        return None

    values = [r["metric"] for r in usable]
    median = _sorted_median(values)
    deviations = [abs(v - median) for v in values]
    mad = _sorted_median(deviations)
    if mad == 0:
        return None

    best_kept: float | None = None
    for r in usable:
        if r.get("status") != "keep":
            continue
        val = r["metric"]
        if best_kept is None or _is_better(val, best_kept, direction):
            best_kept = val

    if best_kept is None or best_kept == baseline["metric"]:
        return None

    return abs(best_kept - baseline["metric"]) / mad


def describe_confidence(confidence: float | None, label: str = "Confidence") -> str:
    """Human-readable confidence description."""
    if confidence is None:
        return f"{label}: n/a (need ≥ 3 runs)"
    rendered = f"{confidence:.1f}"
    if confidence >= 2.0:
        return f"{label}: {rendered}x noise floor — improvement is likely real ✅"
    if confidence >= 1.0:
        return f"{label}: {rendered}x noise floor — above noise but marginal ⚠️"
    return f"{label}: {rendered}x noise floor — within noise, re-run to confirm ❓"


def _sorted_median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    mid = len(sorted_vals) // 2
    if len(sorted_vals) % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    return sorted_vals[mid]


def _is_better(current: float, best: float, direction: str) -> bool:
    return current < best if direction == "lower" else current > best
