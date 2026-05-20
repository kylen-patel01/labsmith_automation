
'''Calibration GUI runner for uProcess pressure-to-flow calibration.

This version wraps the existing calibration workflow in a small Tkinter GUI.
The calibration math and uProcess execution pattern stay the same, but the
user now enters settings through the GUI and can watch the results fill in as
each trial completes. Used Claude AI to help write the GUI code and debug any threading issues,
but all content is original and not copied from any source.

Main GUI features
-----------------
- Enter application name
- Enter pressure targets as a comma-separated list
- Enter dispense time, pressure tolerance, and trials per pressure
- Start calibration from a button instead of editing variables in the file
- Watch a live-updating table of trial results
- Use a "Refill and Continue" button instead of terminal input()
- View the final calibration plot inside the GUI

The core backend logic remains the same:
- Python prepends run-specific parameters to calibration.usq
- The combined script is sent to uProcess through the command pipe
- uProcess executes the real-time hardware logic
- Python reads the resulting CSV files and fits the calibration curve
'''

import os
import time
import json
import threading
from pathlib import Path
from queue import Queue, Empty

import numpy as np
import pandas as pd
import uProcess
from sklearn.linear_model import LinearRegression
from matplotlib import pyplot as plt

import tkinter as tk
from tkinter import ttk, messagebox
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"Config file not found at {CONFIG_PATH}")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

UPROCESS_PATH = Path(CONFIG["uprocess_path"])
if not UPROCESS_PATH.exists():
    raise FileNotFoundError(f"uProcess path not found at {UPROCESS_PATH}")

BASE_USQ = BASE_DIR / "calibration.usq"
if not BASE_USQ.exists():
    raise FileNotFoundError(f"Base calibration script not found at {BASE_USQ}")

CALIB_DATA_DIR = BASE_DIR / "CalibrationData"
CALIB_DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_PREFIX = "Calib"


def generate_pressure_list(lower: float, upper: float, increment: float) -> list[float]:
    '''Generate pressure targets from lower, upper, and increment values.'''
    if not all(v > 0 for v in [lower, upper, increment]):
        raise ValueError("Lower, upper, and increment must all be positive.")
    if lower > upper:
        raise ValueError("Lower pressure must be <= upper pressure.")
    values = np.arange(lower, upper + increment / 2, increment)
    return np.round(values, 10).tolist()


def build_script(target_pressure: float, trial_csv: str, p_tol: float, dispense_time: float) -> str:
    '''Build the full uProcess script for one calibration trial.'''
    header = (
        f" ;Parameters \r\n"
        f"TargetPressure = {target_pressure} kPa\r\n"
        f"TolerancePressure = {p_tol} kPa \r\n"
        f"DispenseTime = {dispense_time} s\r\n"
        f"OutputFile = \"{trial_csv}\"\r\n"
        "\r\n"
    )

    with open(BASE_USQ, "r", encoding="ascii", errors="replace") as f:
        base = f.read()

    return header + base


def extract_flow(csv_file: str) -> float:
    '''Read a uProcess CSV and estimate steady-state flow from 5-25 s.'''
    df = pd.read_csv(csv_file, sep=";", skiprows=[0])
    df.columns = df.columns.str.strip()

    flow_col = [c for c in df.columns if "SF06" in c][0]
    time_col = [c for c in df.columns if "Time (s)" in c][0]

    df[time_col] = pd.to_numeric(df[time_col], errors="coerce")
    df[flow_col] = pd.to_numeric(df[flow_col], errors="coerce")
    df = df.dropna(subset=[time_col, flow_col])

    sampling = df[(df[time_col] >= 5) & (df[time_col] <= 25)]
    return float(np.mean(sampling[flow_col]))


class CalibrationGUI:
    '''Small Tkinter interface for running calibration with live results.'''

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Calibration Runner")

        self.gui_queue = Queue()
        self.refill_event = threading.Event()
        self.worker_thread = None
        self.running = False
        self.current_data_dir = None

        self._build_layout()
        self._poll_queue()

    def _add_input_field(self, parent, row, label_text, entry_attr, default=""):
        '''Helper to create a labeled entry field.'''
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", pady=3)
        entry = tk.Entry(parent, width=28)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        if default:
            entry.insert(0, default)
        setattr(self, entry_attr, entry)
        return entry

    def _build_layout(self):
        '''Build the GUI layout.'''
        self.root.geometry("1450x950")
        self.root.minsize(1150, 800)

        left = ttk.Frame(self.root, padding=10)
        left.grid(row=0, column=0, sticky="nswe")

        right = ttk.Frame(self.root, padding=10)
        right.grid(row=0, column=1, sticky="nswe")

        self.root.grid_columnconfigure(0, weight=0)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        ttk.Label(left, text="Calibration Settings", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        # Default values are inputted here 
        self._add_input_field(left, 1, "Application name:", "application_entry")
        self._add_input_field(left, 2, "Lower pressure (kPa):", "lower_pressure_entry", "2")
        self._add_input_field(left, 3, "Upper pressure (kPa):", "upper_pressure_entry", "20")
        self._add_input_field(left, 4, "Pressure increment (kPa):", "pressure_increment_entry", "2")
        self._add_input_field(left, 5, "Dispense time (s):", "dispense_time_entry", "30")
        self._add_input_field(left, 6, "Tolerance pressure (kPa):", "ptol_entry", "0.1")
        self._add_input_field(left, 7, "Trials per pressure:", "trials_entry", "4")

        ttk.Separator(left, orient="horizontal").grid(row=8, column=0, columnspan=2, sticky="ew", pady=10)

        self.start_button = ttk.Button(left, text="Start Calibration", command=self.start_calibration)
        self.start_button.grid(row=9, column=0, sticky="ew", pady=4)

        self.refill_button = ttk.Button(
            left, text="Refill and Continue", command=self.refill_and_continue, state="disabled"
        )
        self.refill_button.grid(row=9, column=1, sticky="ew", pady=4)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(left, text="Status:", font=("TkDefaultFont", 10, "bold")).grid(
            row=10, column=0, sticky="w", pady=(10, 2)
        )
        ttk.Label(left, textvariable=self.status_var, wraplength=320, justify="left").grid(
            row=11, column=0, columnspan=2, sticky="w"
        )

        help_text = (
            "Notes:\n"
            "- Pressure targets are generated from a lower bound, upper bound, and increment value.\n"
            "  Example: lower=2, upper=20, increment=2, P = [2,4,6,...,20]\n"
            "- The GUI shows trial results as they complete.\n"
            "- After each pressure group completes, refill the reservoir before continuing to the next pressure.\n"
            "- Click refill and continue when you finish refilling.\n"
            "- The final calibration plot will appear on the right when done."
        )
        ttk.Label(left, text=help_text, wraplength=320, justify="left").grid(
            row=12, column=0, columnspan=2, sticky="w", pady=(14, 0)
        )

        left.grid_columnconfigure(1, weight=1)

        ttk.Label(right, text="Calibration Results", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        table_frame = ttk.Frame(right)
        table_frame.grid(row=1, column=0, sticky="nsew")

        self.tree = ttk.Treeview(
            table_frame,
            columns=("pressure", "trials", "mean", "std"),
            show="headings",
            height=14
        )
        self.tree.heading("pressure", text="Pressure (kPa)")
        self.tree.heading("trials", text="Flow Rate (uL/min)")
        self.tree.heading("mean", text="Mean")
        self.tree.heading("std", text="Standard Deviation")
        self.tree.column("pressure", width=110, anchor="center")
        self.tree.column("trials", width=500, anchor="w")
        self.tree.column("mean", width=100, anchor="center")
        self.tree.column("std", width=100, anchor="center")

        table_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=table_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        table_scroll.grid(row=0, column=1, sticky="ns")
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        ttk.Label(right, text="Final Calibration Plot", font=("TkDefaultFont", 12, "bold")).grid(
            row=2, column=0, sticky="w", pady=(12, 8)
        )

        self.fig = Figure(figsize=(7, 4.5))
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("Calibration plot will appear here")
        self.ax.set_xlabel("Pressure (kPa)")
        self.ax.set_ylabel("Flow (uL/min)")
        self.ax.grid(True)

        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().grid(row=3, column=0, sticky="nsew")

        right.grid_rowconfigure(1, weight=1)
        right.grid_rowconfigure(3, weight=1)
        right.grid_columnconfigure(0, weight=1)

    def collect_settings(self):
        '''Read and validate the current GUI settings.'''
        application_name = self.application_entry.get().strip()
        if not application_name:
            raise ValueError("Application name cannot be empty.")

        pressures = generate_pressure_list(
            float(self.lower_pressure_entry.get()),
            float(self.upper_pressure_entry.get()),
            float(self.pressure_increment_entry.get())
        )
        
        dispense_time = float(self.dispense_time_entry.get())
        p_tol = float(self.ptol_entry.get())
        trials = int(self.trials_entry.get())

        if not all(v > 0 for v in [dispense_time, p_tol, trials]):
            raise ValueError("Dispense time, tolerance, and trials must all be positive.")

        return {
            "application_name": application_name,
            "pressures": pressures,
            "dispense_time": dispense_time,
            "p_tol": p_tol,
            "trials_per_pressure": trials,
        }

    def start_calibration(self):
        '''Start the calibration in a background thread so the GUI stays responsive.'''
        if self.running:
            messagebox.showinfo("Calibration Running", "A calibration is already in progress.")
            return

        try:
            settings = self.collect_settings()
        except Exception as e:
            messagebox.showerror("Input Error", str(e))
            return

        self.running = True
        self.refill_event.clear()
        self.start_button.configure(state="disabled")
        self.refill_button.configure(state="disabled")
        self.status_var.set("Starting calibration...")
        self.tree.delete(*self.tree.get_children())
        
        self.ax.clear()
        self.ax.set_title("Calibration in progress...")
        self.ax.set_xlabel("Pressure (kPa)")
        self.ax.set_ylabel("Flow (uL/min)")
        self.ax.grid(True)
        self.canvas.draw()

        self.worker_thread = threading.Thread(
            target=self.run_calibration_worker,
            args=(settings,),
            daemon=True
        )
        self.worker_thread.start()

    def refill_and_continue(self):
        '''Resume after the user has refilled the reservoir.'''
        self.refill_button.configure(state="disabled")
        self.status_var.set("Continuing calibration...")
        self.refill_event.set()

    def run_calibration_worker(self, settings: dict):
        '''Background worker that performs the calibration loop.'''
        application_name = settings["application_name"]
        pressures = settings["pressures"]
        dispense_time = settings["dispense_time"]
        p_tol = settings["p_tol"]
        trials_per_pressure = settings["trials_per_pressure"]

        data_dir = CALIB_DATA_DIR / application_name
        data_dir.mkdir(parents=True, exist_ok=True)
        self.current_data_dir = data_dir

        calibration_results = []
        pipe = None

        try:
            pipe = uProcess.OpenCommandPipe()

            for p in pressures:
                trial_flows = []
                self.gui_queue.put(("status", f"Running pressure group: {p} kPa"))

                for trial in range(1, trials_per_pressure + 1):
                    output_csv = os.path.join(data_dir, f"{LOG_PREFIX}_{p}kPa_trial{trial}.csv")
                    output_csv = output_csv.replace("\\", "/")

                    script_text = build_script(
                        target_pressure=p,
                        trial_csv=output_csv,
                        p_tol=p_tol,
                        dispense_time=dispense_time
                    ).replace("\x00", "").replace("\n", "\r\n")

                    self.gui_queue.put(("status", f"Running trial {trial} at {p} kPa..."))
                    uProcess.SetScript(pipe, script_text)
                    uProcess.Run(pipe)

                    while uProcess.IsRunning(pipe):
                        time.sleep(0.5)

                    avg_flow = extract_flow(output_csv)
                    trial_flows.append(avg_flow)

                    self.gui_queue.put(("trial_result", {
                        "pressure": p,
                        "trial_flows": trial_flows.copy()
                    }))

                mean_flow = float(np.mean(trial_flows))
                std_flow = float(np.std(trial_flows, ddof=1)) if len(trial_flows) > 1 else 0.0
                calibration_results.append((p, mean_flow, std_flow))

                self.gui_queue.put(("pressure_complete", {
                    "pressure": p,
                    "trial_flows": trial_flows.copy(),
                    "mean": mean_flow,
                    "std": std_flow
                }))

                if p != pressures[-1]:
                    self.gui_queue.put(("wait_for_refill", f"Refill reservoir before continuing past {p} kPa."))
                    self.refill_event.clear()
                    self.refill_event.wait()

            if pipe is not None:
                uProcess.CloseCommandPipe(pipe)
                pipe = None

            results_df = pd.DataFrame(
                calibration_results,
                columns=["Pressure_kPa", "Flow_uLmin", "STD"]
            )

            summary_path = os.path.join(data_dir, "calibration_curve.csv")
            results_df.to_csv(summary_path, index=False)

            X = results_df["Pressure_kPa"].values.reshape(-1, 1)
            y = results_df["Flow_uLmin"].values

            model = LinearRegression()
            model.fit(X, y)  # Fit linear model: Flow = slope * Pressure + intercept

            slope = float(model.coef_[0])
            intercept = float(model.intercept_)
            r2 = float(model.score(X, y))

            coef_path = os.path.join(data_dir, "calibration_coeffs.txt")
            with open(coef_path, "w") as f:
                f.write(f"Slope = {slope}\n")
                f.write(f"Intercept = {intercept}\n")
                f.write(f"R2 = {r2}\n")

            plot_path = os.path.join(data_dir, "calibration_plot.png")
            self._save_plot(X.flatten(), y, results_df["STD"], model.predict(X), plot_path)

            self.gui_queue.put(("finished", {
                "summary_path": summary_path,
                "coef_path": coef_path,
                "plot_path": plot_path,
                "x": X.flatten().tolist(),
                "y": y.tolist(),
                "yerr": results_df["STD"].tolist(),
                "yfit": model.predict(X).tolist(),
                "slope": slope,
                "intercept": intercept,
                "r2": r2,
                "data_dir": str(data_dir),
            }))

        except Exception as e:
            if pipe is not None:
                try:
                    uProcess.CloseCommandPipe(pipe)
                except Exception:
                    pass
            self.gui_queue.put(("error", str(e)))

    def _find_or_create_row(self, pressure):
        '''Find or create a results-table row for a given pressure.'''
        key = f"{pressure:g}"
        for item in self.tree.get_children():
            vals = self.tree.item(item, "values")
            if vals and vals[0] == key:
                return item
        return self.tree.insert("", "end", values=(key, "", "", ""))

    def _format_flows(self, trial_flows):
        '''Format trial flows as a comma-separated string.'''
        return ", ".join(f"{v:.2f}" for v in trial_flows)

    def update_trial_row(self, pressure, trial_flows):
        '''Update the row while a pressure group is still in progress.'''
        item = self._find_or_create_row(pressure)
        self.tree.item(item, values=(f"{pressure:g}", self._format_flows(trial_flows), "", ""))

    def update_pressure_complete_row(self, pressure, trial_flows, mean, std):
        '''Update the row after all trials at one pressure are complete.'''
        item = self._find_or_create_row(pressure)
        self.tree.item(item, values=(
            f"{pressure:g}", self._format_flows(trial_flows), f"{mean:.2f}", f"{std:.2f}"
        ))

    def _plot_calibration(self, ax, x, y, yerr, yfit):
        '''Plot calibration data with error bars and linear fit.'''
        ax.clear()
        ax.errorbar(x, y, yerr=yerr, fmt="o", capsize=4, color="blue", 
                    ecolor="gray", elinewidth=1.5, label="Data points ± std")
        ax.plot(x, yfit, color="red", linestyle="--", linewidth=2, label="Linear fit")
        ax.set_xlabel("Pressure (kPa)")
        ax.set_ylabel("Flow (uL/min)")
        ax.set_title("Calibration Curve")
        ax.grid(True)
        ax.legend()

    def draw_final_plot(self, x, y, yerr, yfit):
        '''Render the final calibration plot inside the GUI.'''
        self._plot_calibration(self.ax, x, y, yerr, yfit)
        self.canvas.draw()

    def _save_plot(self, x, y, yerr, yfit, path):
        '''Save calibration plot to file.'''
        fig = plt.figure(figsize=(8, 6))
        ax = fig.add_subplot(111)
        self._plot_calibration(ax, x, y, yerr, yfit)
        plt.savefig(path, dpi=300)
        plt.close()

    def _poll_queue(self):
        '''Process updates from the background worker and apply them to the GUI.'''
        try:
            while True:
                msg_type, payload = self.gui_queue.get_nowait()

                if msg_type == "status":
                    self.status_var.set(payload)

                elif msg_type == "trial_result":
                    self.update_trial_row(
                        pressure=payload["pressure"],
                        trial_flows=payload["trial_flows"]
                    )

                elif msg_type == "pressure_complete":
                    self.update_pressure_complete_row(
                        pressure=payload["pressure"],
                        trial_flows=payload["trial_flows"],
                        mean=payload["mean"],
                        std=payload["std"]
                    )
                    self.status_var.set(
                        f"Completed {payload['pressure']} kPa "
                        f"(mean {payload['mean']:.2f} uL/min, std {payload['std']:.2f})"
                    )

                elif msg_type == "wait_for_refill":
                    self.status_var.set(payload)
                    self.refill_button.configure(state="normal")

                elif msg_type == "finished":
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.refill_button.configure(state="disabled")

                    self.draw_final_plot(
                        x=payload["x"],
                        y=payload["y"],
                        yerr=payload["yerr"],
                        yfit=payload["yfit"]
                    )

                    self.status_var.set(
                        f"Calibration complete. R² = {payload['r2']:.4f}. "
                        f"Saved outputs in {payload['data_dir']}"
                    )

                    messagebox.showinfo(
                        "Calibration Complete",
                        "Calibration finished successfully.\n\n"
                        f"Slope = {payload['slope']:.4f} uL/min per kPa\n"
                        f"Intercept = {payload['intercept']:.4f} uL/min\n"
                        f"R² = {payload['r2']:.4f}"
                    )

                elif msg_type == "error":
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.refill_button.configure(state="disabled")
                    self.status_var.set("Calibration failed.")
                    messagebox.showerror("Calibration Error", payload)

        except Empty:
            pass

        self.root.after(150, self._poll_queue)


def main():
    '''Start the calibration GUI.'''
    root = tk.Tk()
    CalibrationGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
