"""Calibration runner for uProcess pressure-to-flow calibration.

This script runs a series of calibration trials at preset pressures, collects
uProcess CSV outputs, computes steady-state flow from each trial, and then
fits a linear model relating pressure to flow. Results are saved in a dedicated
CalibrationData subfolder named after the application.

Author: Kylen Patel
Last modified: 4/14/26
"""

import os
import subprocess
import time
import pandas as pd
import numpy as np
import uProcess
from sklearn.linear_model import LinearRegression
from matplotlib import pyplot as plt
from pathlib import Path
import json

# USER SETTINGS
# Set a descriptive name for this calibration run / application.
# This name determines the folder under CalibrationData where results are saved.
application_name = ""  # Change to your own calibration experiment name 

# Pressure targets for calibration curve. Adjust as needed but should cover the expected operating range of your experiment. Could use np.linspace too. 
pressures = [2,4,6,8,10,12,14,16,18,20] #kPa

# Fixed parameters for uProcess. Adjust if needed but should be consistent across all trials for accurate calibration curve
dispense_time = 30 # Length of dispense time. Adjust as needed but should be long enough to allow for pressure stabilization and accurate flow measurement but not so long that it causes excessive reagent use or long calibration time. You may need to experiment with this value based on the performance of your system.
p_tol = 0.1 # Pressure tolerance in kPa. Adjust if needed but should be tight enough to ensure accurate pressure control but not so tight that it causes excessive trial failures or long wait times for pressure stabilization. You may need to experiment with this value based on the performance of your system.
trials_per_pressure = 4 # trials per pressure. Adjust as needed but more trials will give a more reliable calibration curve at the cost of longer calibration time and more reagent use.


# Path setup
# base_dir is the folder containing this Python script. All data and script paths are built from this location.
base_dir = Path(__file__).resolve().parent
config_path = base_dir / "config.json"
if not config_path.exists():
    raise FileNotFoundError(f"Config file not found at {config_path}")

with open(config_path, "r", encoding = "utf-8") as f:
    config = json.load(f)

# The config file must provide the path to the uProcess executable/library.
uprocess_path = Path(config["uprocess_path"])
if not uprocess_path.exists():
    raise FileNotFoundError(f"uProcess path not found at {uprocess_path}")

# Calibration data folder and per-application results subfolder.
calib_data = base_dir / "CalibrationData"
calib_data.mkdir(parents = True, exist_ok = True)

# Base uProcess script template used for every calibration trial.
base_usq = base_dir / "calibration.usq"
if not base_usq.exists():
    raise FileNotFoundError(f"Base calibration script not found at {base_usq}")

# Data directory for this calibration run/application. The application_name must be set by the user.
data_dir = calib_data / application_name
data_dir.mkdir(parents=True, exist_ok = True)

# Prefix used for individual trial CSV files.
log_prefix = "Calib"





calibration_results = []
std_results = []
def build_script(target_pressure, trial_csv):
    '''Build a run-specific uProcess script with the requested pressure and output file.'''
    header = (
        f" ;Parameters \r\n"
        f"TargetPressure = {target_pressure} kPa\r\n"
        f"TolerancePressure = {p_tol} kPa \r\n"
        f"DispenseTime = {dispense_time} s\r\n"
        f"OutputFile = \"{trial_csv}\"\r\n"
        "\r\n"
    )

    with open(base_usq, "r", encoding = "ascii", errors = "replace") as f:
        base = f.read()

    script = header + base                    
    return script

# Function to extract steady-state flow from uProcess .csv output.
def extract_flow(csv_file):
    # Read the uProcess CSV output and identify the time and flow columns.
    df = pd.read_csv(csv_file, sep=";", skiprows = [0])
    df.columns = df.columns.str.strip()
    flow_col = [c for c in df.columns if "SF06" in c][0]
    time_col= [c for c in df.columns if "Time (s)" in c][0]
    df[time_col] = pd.to_numeric(df[time_col], errors = 'coerce')
    df[flow_col] = pd.to_numeric(df[flow_col], errors = 'coerce')
    df.dropna(subset=[time_col, flow_col])

    # Use the middle portion of the run (5-25 seconds) to measure steady-state flow,
    # avoiding the startup transient at the beginning and any end-of-run artifacts.
    sampling = df[(df[time_col] >= 5) & (df[time_col] <= 25)]
    return np.mean(sampling[flow_col])

# Main calibration loop: iterate pressure targets and collect multiple trials for each.
# The uProcess command pipe is opened once and reused throughout the session.
pipe = uProcess.OpenCommandPipe()
for p in pressures:
    print(f"\n=Running {p} kPa target=")

    trial_flows = []

    # Run trials back to back before refilling the reservoir.
    # This keeps the calibration consistent while minimizing changes between pressure steps.
    for trial in range(1, trials_per_pressure + 1):
        print(f"\n=Trial {trial} at {p} kPa=")
        output_csv = os.path.join(data_dir, f"{log_prefix}_{p}kPa_trial{trial}.csv")
        output_csv = output_csv.replace("\\","/")
        script_text = build_script(p,output_csv)
        script_text = script_text.replace("\x00", "")
        script_text = script_text.replace("\n", "\r\n")
        resp = uProcess.SetScript(pipe, script_text)
        uProcess.Run(pipe)

        while uProcess.IsRunning(pipe):
            time.sleep(0.5)

        avg_flow = extract_flow(output_csv)
        print(f"Trial {trial}: {avg_flow:.2f} uL/min)")
        trial_flows.append(avg_flow)

    # Store the per-pressure average and standard deviation for later fitting.
    mean_flow = np.mean(trial_flows)
    std_flow = np.std(trial_flows, ddof = 1)
    calibration_results.append((p,mean_flow,std_flow))
    print(f"\n Average flow for {p} kPa = {mean_flow:.2f} uL/min")
    print(f"\n STD flow for {p} kPa = {std_flow:.2f} uL/min")

    input("\n Refill reservoir before next pressure target. \n Press ENTER when done")
uProcess.CloseCommandPipe(pipe)

# Save the calibration curve data as a CSV file for later reference.
results_df = pd.DataFrame(calibration_results, columns = ["Pressure_kPa", "Flow_uLmin", "STD"])
summary_path = os.path.join(data_dir, "calibration_curve.csv")
results_df.to_csv(summary_path, index=False)
print(results_df)

# Fit a linear regression model to the measured pressure-flow points.
# The resulting slope and intercept are the calibration coefficients used by experiment.py.
X = results_df["Pressure_kPa"].values.reshape(-1,1)
y = results_df["Flow_uLmin"].values

model = LinearRegression()
model.fit(X,y)

slope = model.coef_[0]
intercept = model.intercept_
r2 = model.score(X,y)

# Save the calibrated coefficients for future experiment scripts.
coef_path = os.path.join(data_dir, "calibration_coeffs.txt")
with open(coef_path, "w") as f:
    f.write(f"Slope = {slope}\n")
    f.write(f"Intercept = {intercept}\n")
    f.write(f"R2 = {r2}\n")

print(f"Calibration slope: {slope:.4f} uL/min per kPa")
print(f"Calibration intercept: {intercept:.4f} uL/min")
print(f"Calibration R2: {r2:.4f}")
plt.figure(figsize = (8,6))
plt.errorbar(X,y, yerr= results_df["STD"], fmt = 'o', capsize = 4, color = "blue", ecolor = 'gray', elinewidth = 1.5, label = "Data points +/- std")
plt.plot(X, model.predict(X), color = "red", linestyle = '--',linewidth = 2, label = "Linear fit")
plt.xlabel("Pressure (kPa)")
plt.ylabel("Flow (uL/min)")
plt.title("Calibration Curve")
plt.grid(True)
plt.legend()

plot_path = os.path.join(data_dir, "calibration_plot.png")
plt.savefig(plot_path, dpi= 300)
plt.close()