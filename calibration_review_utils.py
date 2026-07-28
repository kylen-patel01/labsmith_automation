from __future__ import annotations

from typing import List, Dict, Tuple


def format_trial_values_for_prompt(pressure: float, trial_flows: List[float]) -> str:
    """Create a readable prompt body showing each trial value for a pressure target."""
    lines = [f"Pressure {pressure} kPa trials:"]
    for index, value in enumerate(trial_flows, start=1):
        lines.append(f"Trial {index}: {value:.3f} uL/min")
    return "\n".join(lines)


def remove_trials_by_numbers(trial_flows: List[float], trial_numbers: List[int]) -> Tuple[List[float], List[Dict[str, float]]]:
    """Remove one or more 1-based trial numbers from the flow list.

    Returns the remaining flow values and a list of removed entries with their trial numbers and values.
    """
    remaining = list(trial_flows)
    removed_entries: List[Dict[str, float]] = []

    for trial_number in sorted(set(trial_numbers), reverse=True):
        index = trial_number - 1
        if 0 <= index < len(remaining):
            removed_entries.append({"trial_number": float(trial_number), "flow_value": float(remaining[index])})
            del remaining[index]

    removed_entries.sort(key=lambda item: item["trial_number"])
    return remaining, removed_entries
