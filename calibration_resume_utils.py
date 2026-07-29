from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Any

import numpy as np
import pandas as pd

TRIAL_FILE_RE = re.compile(r"Calib_(?P<pressure>-?\d+(?:\.\d+)?)kPa_trial(?P<trial>\d+)\.csv", re.IGNORECASE)


def _extract_flow(csv_file: Path, dispense_time: Optional[float] = None) -> float:
    """Estimate flow from a calibration trial CSV using the same logic as the GUI."""
    lines = [line.strip() for line in csv_file.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        return float("nan")

    header_idx = None
    header_values = None
    for index, line in enumerate(lines):
        parts = [part.strip() for part in line.split(";")]
        lowered = [part.lower() for part in parts]
        if any("time" in part for part in lowered) or any("sf06" in part for part in lowered):
            header_idx = index
            header_values = parts
            break

    if header_idx is None or header_values is None:
        return float("nan")

    time_idx = None
    flow_idx = None
    for idx, name in enumerate(header_values):
        lowered = name.lower()
        if time_idx is None and ("time (s)" in lowered or "time" in lowered and "(s)" in lowered):
            time_idx = idx
        if flow_idx is None and ("sf06" in lowered or "flow" in lowered):
            flow_idx = idx

    if time_idx is None or flow_idx is None:
        return float("nan")

    rows = []
    for line in lines[header_idx + 1:]:
        parts = [part.strip() for part in line.split(";")]
        if len(parts) <= max(time_idx, flow_idx):
            continue
        try:
            time_value = float(parts[time_idx].replace(",", "."))
            flow_value = float(parts[flow_idx].replace(",", "."))
        except ValueError:
            continue
        rows.append((time_value, flow_value))

    if dispense_time is None or dispense_time <= 0:
        return float("nan")

    half_window = dispense_time / 2.0
    start_time = dispense_time / 4.0
    end_time = start_time + half_window

    sampling = [flow for time_value, flow in rows if start_time <= time_value <= end_time]
    if not sampling:
        sampling = [flow for _, flow in rows]
    if not sampling:
        return float("nan")
    return float(np.mean(sampling))


def load_removed_trial_numbers(data_dir: Path, pressure: float) -> set:
    """Return the trial numbers a previous run removed during review for one pressure target."""
    for name in (f"review_{pressure}kPa.csv", f"review_{pressure:g}kPa.csv"):
        review_path = data_dir / name
        if not review_path.exists():
            continue
        try:
            review_df = pd.read_csv(review_path)
        except Exception:
            return set()
        if "trial_number" not in review_df.columns:
            return set()
        removed = set()
        for value in review_df["trial_number"].tolist():
            try:
                removed.add(int(float(value)))
            except (TypeError, ValueError):
                continue
        return removed
    return set()


def collect_existing_pressure_results(data_dir: Path, dispense_time: Optional[float] = None) -> List[Dict[str, Any]]:
    """Summarize already-saved trial results for a calibration directory.

    Trials that the earlier run removed during review are excluded, so a resumed
    calibration reuses exactly the values the original session settled on.
    """
    pressure_groups: Dict[float, Dict[int, float]] = {}

    for csv_path in sorted(data_dir.glob("Calib_*kPa_trial*.csv")):
        match = TRIAL_FILE_RE.match(csv_path.name)
        if not match:
            continue

        pressure = float(match.group("pressure"))
        trial = int(match.group("trial"))
        flow = _extract_flow(csv_path, dispense_time)
        if not np.isnan(flow):
            pressure_groups.setdefault(pressure, {})[trial] = float(flow)

    results: List[Dict[str, Any]] = []
    for pressure in sorted(pressure_groups):
        trials = pressure_groups[pressure]
        removed = load_removed_trial_numbers(data_dir, pressure)
        kept_trials = [trial for trial in sorted(trials) if trial not in removed]
        ordered_flows = [trials[trial] for trial in kept_trials]
        if not ordered_flows:
            continue
        mean_flow = float(np.mean(ordered_flows))
        std_flow = float(np.std(ordered_flows, ddof=1)) if len(ordered_flows) > 1 else 0.0
        results.append(
            {
                "pressure": pressure,
                "trial_flows": ordered_flows,
                "mean": mean_flow,
                "std": std_flow,
                "removed_trials": sorted(removed),
            }
        )

    return results


def first_incomplete_pressure(
    results: List[Dict[str, Any]],
    trials_per_pressure: int,
    pressures: Optional[List[float]] = None,
) -> Optional[float]:
    """Find the lowest pressure target that does not yet have a full set of trials.

    When a planned pressure list is supplied, gaps in that list count as incomplete
    too, so a folder that stops partway through the sweep resumes at the next target.
    """
    counts = {float(result["pressure"]): len(result["trial_flows"]) for result in results}

    search_space = [float(p) for p in pressures] if pressures else sorted(counts)
    for pressure in search_space:
        if counts.get(pressure, 0) < trials_per_pressure:
            return pressure

    return None


def discover_incomplete_calibrations(calib_data_dir: Path, dispense_time: Optional[float] = None) -> List[Dict[str, Any]]:
    """Return calibration directories with trial data but no completed coeffs/plot yet."""
    if not calib_data_dir.exists():
        return []

    candidates: List[Dict[str, Any]] = []
    for child in sorted(calib_data_dir.iterdir()):
        if not child.is_dir():
            continue

        trial_files = sorted(child.glob("Calib_*kPa_trial*.csv"))
        if not trial_files:
            continue

        has_completed_artifacts = (child / "calibration_coeffs.txt").exists() or (child / "calibration_plot.png").exists()
        if has_completed_artifacts:
            continue

        results = collect_existing_pressure_results(child, dispense_time=dispense_time)
        if results:
            candidates.append(
                {
                    "name": child.name,
                    "path": child,
                    "results": results,
                }
            )

    return candidates
