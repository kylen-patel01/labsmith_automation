import os
import subprocess
import time
import pandas as pd
import numpy as np
import uProcess
from sklearn.linear_model import LinearRegression
from matplotlib import pyplot as plt
from pathlib import Path
#USER SETTINGS
application_name = "" # Change to your own calibration experiment name 

# Pressure targets for calibration curve. Adjust as needed but should cover the expected operating range of your experiment. Could use np.linspace too. 
pressures = [2,4,6,8,10,12,14,16,18,20] #kPa

# Fixed parameters for uProcess. Adjust if needed but should be consistent across all trials for accurate calibration curve
dispense_time = 30 # Length of dispense time. Adjust as needed but should be long enough to allow for pressure stabilization and accurate flow measurement but not so long that it causes excessive reagent use or long calibration time. You may need to experiment with this value based on the performance of your system.
p_tol = 0.1 # Pressure tolerance in kPa. Adjust if needed but should be tight enough to ensure accurate pressure control but not so tight that it causes excessive trial failures or long wait times for pressure stabilization. You may need to experiment with this value based on the performance of your system.
trials_per_pressure = 4 # trials per pressure. Adjust as needed but more trials will give a more reliable calibration curve at the cost of longer calibration time and more reagent use.


# Path setup 
base_dir = Path(__file__).resolve().parent
config_path = base_dir / "config.json"
if not config_path.exists():
    raise FileNotFoundError(f"Config file not found at {config_path}")

with open(config_path, "r", encoding = "utf-8") as f:
    config = json.load(f)

uprocess_path = Path(config["uprocess_path"])

if not uprocess_path.exists():
    raise FileNotFoundError(f"uProcess path not found at {uprocess_path}")

# Calibation data folder
calib_data = base_dir / "CalibrationData"
calib_data.mkdir(parents = True, exist_ok = True)

# Base directory for the calibration uProcess script
base_usq = base_dir / "calibration.usq"
if not base_usq.exists():
    raise FileNotFoundError(f"Base calibration script not found at {base_usq}")

# Data directory for this calibration run/application
data_dir = calib_data / application_name
data_dir.mkdir(parents=True, exist_ok = True)

# File naming convention for trial CSVs. Adjust if needed but should be consistent with how build_script names the output files.
log_prefix = "Calib"





calibration_results = []
std_results = []
# Build script to send into uProcess
def build_script(target_pressure, trial_csv):
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

# Function to extract flow data from uProcess .csv output 
def extract_flow(csv_file):
    df = pd.read_csv(csv_file, sep=";", skiprows = [0])
    df.columns = df.columns.str.strip()
    flow_col = [c for c in df.columns if "SF06" in c][0]
    time_col= [c for c in df.columns if "Time (s)" in c][0]
    df[time_col] = pd.to_numeric(df[time_col], errors = 'coerce')
    df[flow_col] = pd.to_numeric(df[flow_col], errors = 'coerce')
    df.dropna(subset=[time_col, flow_col])
    sampling = df[(df[time_col] >= 5) & (df[time_col] <= 25)] # Sampling at 10 Hz and want mean flow from 5-25 seconds (steady-state flow)
    return np.mean(sampling[flow_col])

# Main calibration loop (averaging over 3 trials)
pipe = uProcess.OpenCommandPipe()
for p in pressures:
    print(f"\n=Running {p} kPa target=")

    trial_flows = []

    #Run 3 trials back to back before refilling reservoir

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


    # Compute average flow for this pressure target and store results. 
    mean_flow = np.mean(trial_flows)
    std_flow = np.std(trial_flows, ddof = 1)
    calibration_results.append((p,mean_flow,std_flow))
    print(f"\n Average flow for {p} kPa = {mean_flow:.2f} uL/min")
    print(f"\n STD flow for {p} kPa = {std_flow:.2f} uL/min")

    input("\n Refill reservoir before next pressure target. \n Press ENTER when done")
uProcess.CloseCommandPipe(pipe)

# Save curve
results_df = pd.DataFrame(calibration_results, columns = ["Pressure_kPa", "Flow_uLmin", "STD"])
summary_path = os.path.join(data_dir, "calibration_curve.csv")
results_df.to_csv(summary_path, index=False)
print(results_df)

# Fitting/regression and plotting
X = results_df["Pressure_kPa"].values.reshape(-1,1)
y = results_df["Flow_uLmin"].values

model = LinearRegression()
model.fit(X,y)

slope= model.coef_[0]
intercept = model.intercept_
r2 = model.score(X,y)


# Save coefficients for future experiment. Change the title of .txt file to match experiment application

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