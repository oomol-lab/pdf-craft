"""Deterministic JEV baseline replay for repository experiments."""

import json
from pathlib import Path
from typing import Any

class PinnedJevEvaluator:
    """Replay one named probability run from a committed JEV baseline."""

    def __init__(self, baseline_path: Path, run_name: str | None = None) -> None:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        runs = baseline.get("runs")
        if not isinstance(runs, list) or not runs:
            raise ValueError(f"JEV baseline has no runs: {baseline_path}")
        selected = runs[0] if run_name is None else next(
            (
                run for run in runs
                if isinstance(run, dict) and run.get("name") == run_name
            ),
            None,
        )
        if not isinstance(selected, dict):
            raise ValueError(
                f"JEV baseline has no run named {run_name!r}: {baseline_path}"
            )
        probabilities = selected.get("pass_probabilities")
        if (
            not isinstance(probabilities, list)
            or not probabilities
            or not all(
                isinstance(value, (int, float)) and 0 <= value <= 1
                for value in probabilities
            )
        ):
            raise ValueError(
                f"JEV baseline run has invalid pass probabilities: {baseline_path}"
            )
        self.baseline_path = baseline_path
        self.run_name = str(selected.get("name", "unnamed"))
        self._probabilities = [float(value) for value in probabilities]

    async def __call__(self, page_index: int, _request: dict[str, Any]) -> float:
        if page_index < 1 or page_index > len(self._probabilities):
            raise ValueError(
                f"JEV baseline {self.run_name!r} has no page {page_index}"
            )
        return self._probabilities[page_index - 1]
