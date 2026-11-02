"""Historical page admission for repair experiments, not JEV question replay."""

import json
from pathlib import Path

class PinnedPageSelection:
    """Use a recorded run and its own threshold to select fixed repair pages."""

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
        probabilities = [float(value) for value in probabilities]
        threshold = baseline.get('threshold')
        if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            raise ValueError('Historical JEV baseline needs its recorded threshold')
        self.page_indexes = [index for index, probability in enumerate(probabilities, 1)
                             if 1 - probability >= threshold]
