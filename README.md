# labsmith
# LabSmith Automation

A Python GUI toolkit for running pressure-driven fluid dispense experiments 
using uProcess hardware. Built for lab use with LabSmith's pressure driven microfluidics system, thermal components, and SF06 flow sensor.

## What it does

Three scripts work together:

- **calibration_gui.py** — runs a pressure-to-flow calibration. You enter a 
  pressure range and increment, and it steps through each pressure, runs 
  trials, fits a linear curve (Flow = slope × Pressure + intercept), and saves 
  the coefficients and plot to a CalibrationData folder.

- **experiment_new.py** — the main experiment GUI. You select a calibration 
  curve, enter desired flow rates and volumes per reservoir, and it computes 
  the required pressures and dispense times, runs each reservoir in sequence 
  via uProcess, and saves individual and combined CSV/plot outputs. Optionally 
  supports thermal control with temperature stabilization before dispensing.

- **live_plotter_multi.py** — launched automatically by experiment_new.py. 
  Polls a live_state.json file and shows a scrolling real-time flow plot as 
  each run executes.

## Requirements

- Python 3.10+
- uProcess (hardware control library, must be installed separately)
- `pip install pandas numpy matplotlib pillow scikit-learn`

## Setup

1. Add a `config.json` in the project root with the path to your uProcess installation:
```json
   {
     "uprocess_path": "C:/path/to/uProcess"
   }
```
2. Place your `calibration.usq` and `experiment.usq` scripts in the project root.

## Usage

Run calibration first (if you don't have a calibration curve yet):
```bash
python calibration_gui.py
```

Then run experiments:
```bash
python experiment_new.py
```

The live plotter launches automatically — no need to run it manually.

## Output

Results are saved in timestamped folders. Each experiment produces:
- Per-reservoir CSV files and flow/pressure overlay plots
- A combined CSV with a continuous time axis
- Optional temperature plots if thermal control was enabled

## Author

Kylen Patel
