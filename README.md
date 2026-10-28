# LabSmith Automation via Python
A Python GUI toolkit for running pressure-driven fluid dispense experiments
using uProcess hardware. Built for lab use with LabSmith's pressure-driven microfluidics system, thermal components, and SF06 flow sensor.

uProcess remains the hardware control layer, while Python handles run setup, calibration selection, parameter generation, file naming, plotting, and post-processing.

## What it does
Four scripts work together:
- **calibration_gui.py** - runs a pressure-to-flow calibration. You enter a
  solution/calibration name, a pressure range and increment, dispense time,
  tolerance, and trials per pressure, and it steps through each pressure,
  fits a linear curve (Flow = slope × Pressure + intercept), and saves the
  coefficients and plot to `CalibrationData/<name>/`. Also supports:
  - A live-updating results table showing each trial's flow value as it completes
  - A trial review step after each pressure group- keep all trials or remove
    specific bad ones before the mean/std is calculated (removed trials are
    logged to `review_<pressure>kPa.csv`)
  - A "Refill and Continue" button instead of a terminal prompt
  - Resuming an interrupted calibration- on startup it scans `CalibrationData/`
    for folders with trial CSVs but no `calibration_coeffs.txt`/plot, and can
    pick up from a chosen pressure, reusing saved trials below it
  - Uses `calibration_review_utils.py` and `calibration_resume_utils.py` as
    helper modules- keep both next to `calibration_gui.py`
- **experiment_new.py** - the main GUI for your project. You select a calibration
  curve based on your desired solution per stage/reservoir, enter desired flow
  rates and volumes per reservoir, and it computes the required pressures and
  dispense times, runs each reservoir in sequence via uProcess, and saves
  individual and combined CSV/plot outputs. Supports:
  - Optional thermal control, with temperature stabilization before dispensing
  - **Hold stages** - hold pressure/temperature for a set duration with no
    flow or dispense math, instead of using a calibration curve
  - **Clear stages** - each stage has a compact clear-stage control for its
    own tubing volume and desired flow rate; pressure is calculated from that
    stage's calibration curve before it runs ahead of Stage 1 and every later stage
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
- **calibration_review_utils.py / calibration_resume_utils.py** - helper
  modules imported by calibration_gui.py; not run directly.

## Requirements
- Python 3.10+
- uProcess (LabSmith's hardware control library, must be installed separately)
  - `import uProcess`
- uProcess must be open before running the Python scripts
- A `config.json` with a working `uprocess_path` (see Setup)- both
  `calibration_gui.py` and `experiment.py` read this at startup and will not
  launch without it
- `pip install pandas numpy matplotlib scikit-learn`
  - `scikit-learn` is required by `calibration_gui.py` for the linear fit
    (`sklearn.linear_model.LinearRegression`)

## Setup
1. Add a `config.json` in the project root pointing at your uProcess
   installation:
   ```json
   {
     "uprocess_path": "C:/path/to/uProcess"
   }
   ```
   Both scripts also fall back to a local folder named exactly
   `uProcess v2.072D BETA` next to the scripts if you'd rather keep
   `uProcess.py`/`uProcessTest.py` there instead of setting `uprocess_path`.
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

Calibration results are saved under `CalibrationData/<solution_name>/`:
- Per-trial CSVs, named `Calib_<pressure>kPa_trial<trial_number>.csv`
- `calibration_curve.csv`, `calibration_coeffs.txt`, `calibration_plot.png`
- An optional `review_<pressure>kPa.csv` per pressure target where trials
  were removed during review

## Author
Kylen Patel
