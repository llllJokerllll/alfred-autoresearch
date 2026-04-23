"""Core experiment engine — the heart of AutoResearch."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from autoresearch.core.confidence import compute_confidence
from autoresearch.core.metrics import parse_metric_lines


class RunStatus(str, Enum):
    KEEP = "keep"
    DISCARD = "discard"
    CRASH = "crash"
    PENDING = "pending"


class Direction(str, Enum):
    LOWER = "lower"
    HIGHER = "higher"


@dataclass(frozen=True)
class SecondaryMetricDef:
    name: str
    unit: str


@dataclass
class Run:
    """A single experiment run."""
    run: int
    commit: str
    metric: float
    metrics: dict[str, float]
    status: RunStatus
    baseline: bool
    description: str
    timestamp: float
    segment: int
    confidence: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "commit": self.commit,
            "metric": self.metric,
            "metrics": self.metrics,
            "status": self.status.value,
            "baseline": self.baseline,
            "description": self.description,
            "timestamp": self.timestamp,
            "segment": self.segment,
            "confidence": self.confidence,
        }


@dataclass
class ExperimentConfig:
    """Experiment configuration."""
    name: str | None = None
    metric_name: str = "metric"
    metric_unit: str = ""
    best_direction: Direction = Direction.LOWER
    secondary_metrics: list[SecondaryMetricDef] = field(default_factory=list)
    files_in_scope: list[str] = field(default_factory=list)
    off_limits: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "config",
            "name": self.name,
            "metricName": self.metric_name,
            "metricUnit": self.metric_unit,
            "bestDirection": self.best_direction.value,
        }


@dataclass
class ExecutionResult:
    """Result of running a command."""
    command: str
    exit_code: int | None
    duration_seconds: float
    passed: bool
    crashed: bool
    timed_out: bool
    stdout: str
    stderr: str

    @property
    def combined_output(self) -> str:
        parts = [self.stdout, self.stderr]
        return "\n".join(p for p in parts if p.strip())

    @property
    def tail_output(self, lines: int = 80) -> str:
        combined = self.combined_output
        if not combined:
            return ""
        return "\n".join(combined.split("\n")[-lines:])


class AutoResearch:
    """
    Autonomous experiment loop engine.

    Usage:
        ar = AutoResearch(name="optimize-latency", metric="latency_ms", direction="lower")
        ar.init()
        ar.run("./benchmark.sh")  # Baseline
        ar.log("Baseline measurement")
        # ... make changes ...
        ar.run("./benchmark.sh")
        ar.log("After caching optimization")
    """

    DEFAULT_TIMEOUT = 600  # seconds
    RESULTS_LOG = "autoresearch.results.jsonl"
    IDEAS_FILE = "autoresearch.ideas.md"
    SESSION_DOC = "autoresearch.md"
    CHECKPOINT_FILE = "autoresearch.checkpoint.json"

    def __init__(
        self,
        name: str = "",
        metric: str = "metric",
        direction: Literal["lower", "higher"] = "lower",
        unit: str = "",
        cwd: str | Path | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        secondary_metrics: list[SecondaryMetricDef] | None = None,
        files_in_scope: list[str] | None = None,
        off_limits: list[str] | None = None,
        constraints: list[str] | None = None,
    ):
        self._cwd = Path(cwd) if cwd else Path.cwd()
        self._config = ExperimentConfig(
            name=name or None,
            metric_name=metric,
            metric_unit=unit,
            best_direction=Direction(direction),
            secondary_metrics=secondary_metrics or [],
            files_in_scope=files_in_scope or [],
            off_limits=off_limits or [],
            constraints=constraints or [],
        )
        self._timeout = timeout
        self._runs: list[Run] = []
        self._pending: Run | None = None
        self._current_segment = 0
        self._current_run_index = 0
        self._total_runs = 0
        self._baseline_metric: float | None = None
        self._best_metric: float | None = None
        self._confidence: float | None = None
        self._initialized = False

        # Load existing state if any
        self._load_state()

    # --- Properties ---

    @property
    def cwd(self) -> Path:
        return self._cwd

    @property
    def config(self) -> ExperimentConfig:
        return self._config

    @property
    def runs(self) -> list[Run]:
        return list(self._runs)

    @property
    def last_run(self) -> Run | None:
        return self._runs[-1] if self._runs else None

    @property
    def baseline_metric(self) -> float | None:
        return self._baseline_metric

    @property
    def best_metric(self) -> float | None:
        return self._best_metric

    @property
    def confidence(self) -> float | None:
        return self._confidence

    @property
    def total_runs(self) -> int:
        return self._total_runs

    @property
    def current_segment(self) -> int:
        return self._current_segment

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    # --- Core Methods ---

    def init(self, reset: bool = False) -> None:
        """
        Initialize a new experiment segment.

        Args:
            reset: If True, start completely fresh. If False, continue existing.
        """
        if reset:
            self._runs.clear()
            self._current_segment = 0
            self._current_run_index = 0
            self._total_runs = 0
            self._baseline_metric = None
            self._best_metric = None
            self._confidence = None
            self._pending = None
            self._initialized = False

        if self._initialized and not reset:
            raise ValueError("Already initialized. Use reset=True to start fresh.")

        # Write config entry
        self._append_results(self._config.to_dict())
        self._initialized = True

    def run(
        self,
        command: str,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """
        Run a benchmark command and capture metrics.

        Outputs METRIC name=value lines from stdout/stderr.
        The primary metric must match config.metric_name.

        Args:
            command: Shell command to execute.
            timeout: Override default timeout in seconds.
            env: Additional environment variables.

        Returns:
            ExecutionResult with parsed metrics.

        Raises:
            RuntimeError: If experiment not initialized.
            RuntimeError: If a pending run exists (call log() first).
        """
        if not self._initialized:
            raise RuntimeError("Not initialized. Call init() first.")
        if self._pending is not None:
            raise RuntimeError("Pending run exists. Call log() first.")

        timeout = timeout or self._timeout
        cmd_env = os.environ.copy()
        if env:
            cmd_env.update(env)

        started = time.monotonic()
        try:
            proc = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self._cwd),
                env=cmd_env,
            )
            exit_code = proc.returncode
            timed_out = False
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as e:
            exit_code = None
            timed_out = True
            stdout = e.stdout or ""
            stderr = e.stderr or ""

        duration = time.monotonic() - started
        passed = exit_code == 0 and not timed_out
        crashed = not passed

        result = ExecutionResult(
            command=command,
            exit_code=exit_code,
            duration_seconds=duration,
            passed=passed,
            crashed=crashed,
            timed_out=timed_out,
            stdout=stdout,
            stderr=stderr,
        )

        # Parse metrics
        metrics = parse_metric_lines(result.combined_output)
        primary = metrics.get(self._config.metric_name, 0.0)

        if timed_out:
            status = RunStatus.CRASH
        elif not passed:
            status = RunStatus.CRASH
        else:
            status = RunStatus.PENDING

        # Get git commit if available
        commit = self._get_git_commit()

        run = Run(
            run=self._current_run_index + 1,
            commit=commit,
            metric=primary,
            metrics=metrics,
            status=status,
            baseline=self._current_run_index == 0,
            description="",
            timestamp=datetime.now(timezone.utc).timestamp(),
            segment=self._current_segment,
            confidence=None,
        )

        self._pending = run
        return result

    def log(
        self,
        description: str = "",
        status: Literal["keep", "discard", "crash"] | None = None,
        metric: float | None = None,
        commit: str | None = None,
    ) -> Run:
        """
        Log the pending run result.

        Args:
            description: What was changed in this run.
            status: "keep", "discard", or "crash". Defaults to "keep" if passed.
            metric: Override the primary metric value.
            commit: Override the git commit hash.

        Returns:
            The logged Run.

        Raises:
            RuntimeError: If no pending run exists.
        """
        if self._pending is None:
            raise RuntimeError("No pending run. Call run() first.")

        run = self._pending
        self._pending = None

        # Override fields if provided
        if description:
            run = Run(
                run=run.run,
                commit=commit or run.commit,
                metric=metric if metric is not None else run.metric,
                metrics=run.metrics,
                status=RunStatus(status) if status else (RunStatus.KEEP if run.status == RunStatus.PENDING else RunStatus.CRASH),
                baseline=run.baseline,
                description=description,
                timestamp=run.timestamp,
                segment=run.segment,
                confidence=run.confidence,
            )
        elif run.status == RunStatus.PENDING:
            # Default to keep if command passed
            run = Run(
                run=run.run,
                commit=commit or run.commit,
                metric=run.metric,
                metrics=run.metrics,
                status=RunStatus.KEEP,
                baseline=run.baseline,
                description=description or "No description",
                timestamp=run.timestamp,
                segment=run.segment,
                confidence=run.confidence,
            )

        # Update indices
        self._current_run_index += 1
        self._total_runs += 1

        # Track baseline and best
        if run.baseline or self._baseline_metric is None:
            self._baseline_metric = run.metric

        if run.status == RunStatus.KEEP and run.metric > 0:
            if self._best_metric is None or self._is_better(run.metric, self._best_metric):
                self._best_metric = run.metric

        # Compute confidence
        self._confidence = self._compute_confidence()

        # Store and persist
        self._runs.append(run)
        self._append_results(run.to_dict())
        self._update_checkpoint()

        return run

    def discard_with_idea(self, description: str, idea: str) -> Run:
        """
        Log a discarded run with an idea for future exploration.

        The failed path gets appended to autoresearch.ideas.md.

        Args:
            description: What was tried.
            idea: Idea for a different approach.

        Returns:
            The discarded Run.
        """
        self._append_idea(idea)
        return self.log(description=description, status="discard")

    def reset(self) -> None:
        """Reset to a fresh experiment."""
        self.init(reset=True)

    def get_best_run(self, segment: int | None = None) -> Run | None:
        """Get the best kept run, optionally filtered by segment."""
        runs = self._runs
        if segment is not None:
            runs = [r for r in runs if r.segment == segment]
        if not runs:
            return None

        kept = [r for r in runs if r.status == RunStatus.KEEP]
        candidates = kept if kept else runs

        best = candidates[0]
        for r in candidates[1:]:
            if self._is_better(r.metric, best.metric):
                best = r
        return best

    def get_status(self) -> dict[str, Any]:
        """Get a snapshot of the current experiment state."""
        return {
            "name": self._config.name,
            "metric_name": self._config.metric_name,
            "metric_unit": self._config.metric_unit,
            "best_direction": self._config.best_direction.value,
            "segment": self._current_segment,
            "runs_in_segment": self._current_run_index,
            "total_runs": self._total_runs,
            "baseline_metric": self._baseline_metric,
            "best_metric": self._best_metric,
            "confidence": self._confidence,
            "has_pending": self._pending is not None,
            "initialized": self._initialized,
            "last_run": self._runs[-1].to_dict() if self._runs else None,
        }

    # --- Internal ---

    def _is_better(self, current: float, best: float) -> bool:
        return (
            current < best
            if self._config.best_direction == Direction.LOWER
            else current > best
        )

    def _compute_confidence(self) -> float | None:
        confidence_runs = [
            {"metric": r.metric, "status": r.status.value}
            for r in self._runs
            if r.segment == self._current_segment
        ]
        return compute_confidence(confidence_runs, self._config.best_direction.value)

    def _get_git_commit(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(self._cwd),
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return ""

    def _append_results(self, entry: dict[str, Any]) -> None:
        path = self._cwd / self.RESULTS_LOG
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _append_idea(self, idea: str) -> None:
        path = self._cwd / self.IDEAS_FILE
        with open(path, "a") as f:
            f.write(f"- {idea}\n")

    def _update_checkpoint(self) -> None:
        path = self._cwd / self.CHECKPOINT_FILE
        checkpoint = {
            "name": self._config.name,
            "metric_name": self._config.metric_name,
            "metric_unit": self._config.metric_unit,
            "best_direction": self._config.best_direction.value,
            "segment": self._current_segment,
            "total_runs": self._total_runs,
            "baseline_metric": self._baseline_metric,
            "best_metric": self._best_metric,
            "confidence": self._confidence,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(path, "w") as f:
            json.dump(checkpoint, f, indent=2)

    def _load_state(self) -> None:
        """Load existing state from results log and checkpoint."""
        results_path = self._cwd / self.RESULTS_LOG
        if not results_path.exists():
            return

        with open(results_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") == "config":
                    if entry.get("name"):
                        self._config = ExperimentConfig(
                            name=entry.get("name"),
                            metric_name=entry.get("metricName", "metric"),
                            metric_unit=entry.get("metricUnit", ""),
                            best_direction=Direction(entry.get("bestDirection", "lower")),
                            secondary_metrics=self._config.secondary_metrics,
                            files_in_scope=self._config.files_in_scope,
                            off_limits=self._config.off_limits,
                            constraints=self._config.constraints,
                        )
                    if self._total_runs > 0:
                        self._current_segment += 1
                    self._current_run_index = 0
                    self._baseline_metric = None
                    self._best_metric = None
                    self._confidence = None
                    self._initialized = True
                    continue

                metric_val = entry.get("metric")
                if not isinstance(metric_val, (int, float)):
                    continue

                self._total_runs += 1
                self._current_run_index += 1
                is_baseline = entry.get("baseline", False) or self._current_run_index == 1

                run = Run(
                    run=entry.get("run", self._current_run_index),
                    commit=entry.get("commit", ""),
                    metric=float(metric_val),
                    metrics=entry.get("metrics", {}),
                    status=RunStatus(entry.get("status", "keep")),
                    baseline=is_baseline,
                    description=entry.get("description", ""),
                    timestamp=entry.get("timestamp", 0),
                    segment=entry.get("segment", self._current_segment),
                    confidence=entry.get("confidence"),
                )

                self._runs.append(run)

                if self._baseline_metric is None:
                    self._baseline_metric = run.metric

                if run.status == RunStatus.KEEP and run.metric > 0:
                    if self._best_metric is None or self._is_better(run.metric, self._best_metric):
                        self._best_metric = run.metric

            self._confidence = self._compute_confidence()
