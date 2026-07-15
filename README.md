# LabSmith Automation via Python
A Python GUI toolkit for running pressure-driven fluid dispense experiments
using uProcess hardware. Built for lab use with LabSmith's pressure-driven microfluidics system, thermal components, and SF06 flow sensor.

uProcess remains the hardware control layer, while Python handles run setup, calibration selection, parameter generation, file naming, plotting, and post-processing.

## What it does
Three scripts work together:
- **calibration_gui.py** - runs a pressure-to-flow calibration. You enter a
  pressure range and increment in a GUI, and it steps through each pressure, runs
  trials, fits a linear curve (Flow = slope × Pressure + intercept), and saves
  the coefficients and plot to a CalibrationData folder.
- **experiment.py** - the main GUI for your project. You select a calibration
  curve based on your desired solution per stage/reservoir, enter desired flow
  rates and volumes per reservoir, and it computes the required pressures and
  dispense times, runs each reservoir in sequence via uProcess, and saves
  individual and combined CSV/plot outputs. Supports:
  - Optional thermal control, with temperature stabilization before dispensing
  - **Hold stages** - hold pressure/temperature for a set duration with no
    flow or dispense math, instead of using a calibration curve
  - Pause / Resume / Stop controls mid-run
  - A live, per-stage status label (e.g. "Pressurizing," "Injecting") read
    from each stage's state CSV
  - Per-stage deviation-threshold checks, with a warning if steady-state flow
    deviates from the desired value by more than the configured percentage
  - A "save output" toggle - turn it off to run in a temporary, auto-deleted
    location for dry runs/demos instead of writing to `ExperimentData/`
  - A `_summary.txt` file per project with all run parameters and results
- **live_plotter_multi.py** - launched automatically by experiment.py.
  Polls a `live_state.json` file and shows a scrolling real-time flow and
  temperature plot as each run executes.

## Requirements
- Python 3.10+
- uProcess (LabSmith's hardware control library, must be installed separately)
  - `import uProcess`
- uProcess must be open before running the Python scripts
- `pip install pandas numpy matplotlib`
  - `calibration_gui.py` may need additional packages (e.g. `pillow`,
    `scikit-learn`) depending on its curve-fitting/plotting implementation-
    check its imports directly

## Setup
1. Make sure `uProcess.py` (LabSmith's Python wrapper) is importable from your
   environment- either placed alongside these scripts or available on your
   `PYTHONPATH`.
2. Place your `calibration.usq` and `experiment.usq` scripts in the project
   root- these are the base uProcess scripts that Python prepends run
   parameters to before sending them to the hardware.

## Usage
Run calibration first (if you don't have a calibration curve for your solution(s) yet):
```bash
python calibration_gui.py
```
Then run experiments:
```bash
python experiment.py
```
The live plotter launches automatically during experiment execution.

## Output
Results are saved under `ExperimentData/<project_name>/` (skipped entirely if
"save output" is turned off). Each project produces:
- Per-stage CSV files, named `<project_name>_Stage<stage_number>_Res<reservoir_position>_<timestamp>.csv`
  (timestamp is optional, toggled in the GUI)
- A per-stage state CSV tracking live status codes
- Per-stage flow/pressure overlay plots, and optional temperature plots if
  thermal control was enabled
- A combined CSV with a continuous time axis across all stages, plus a
  combined overlay plot
- A `_summary.txt` file with run parameters and results for the whole project

## Author
Kylen Patel
