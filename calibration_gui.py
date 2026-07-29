
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
import sys
import time
import json
import threading
from pathlib import Path
from queue import Queue, Empty

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from matplotlib import pyplot as plt

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from calibration_review_utils import format_trial_values_for_prompt, remove_trials_by_numbers
from calibration_resume_utils import (
    collect_existing_pressure_results,
    discover_incomplete_calibrations,
    first_incomplete_pressure,
)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"Config file not found at {CONFIG_PATH}")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

UPROCESS_PATH = Path(CONFIG["uprocess_path"])
if not UPROCESS_PATH.exists():
    raise FileNotFoundError(f"uProcess path not found at {UPROCESS_PATH}")

UPROCESS_LIB_DIR = BASE_DIR / "uProcess v2.072D BETA"
for candidate_dir in [UPROCESS_PATH, UPROCESS_LIB_DIR]:
    candidate_dir = str(candidate_dir)
    if candidate_dir not in sys.path:
        sys.path.insert(0, candidate_dir)

try:
    import uProcess
except ImportError as exc:
    raise ImportError(
        "Could not import the LabSmith uProcess wrapper. "
        "Expected the file 'uProcess v2.072D BETA/uProcess.py' next to this script."
    ) from exc

if not hasattr(uProcess, "OpenCommandPipe"):
    raise ImportError(
        "The imported module does not expose the expected uProcess command-pipe functions."
    )

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


def extract_flow(csv_file: str, dispense_time: float) -> float:
    '''Read a uProcess CSV and estimate steady-state flow from the middle half of the dispense window.'''
    df = pd.read_csv(csv_file, sep=";", skiprows=[0])
    df.columns = df.columns.str.strip()

    flow_col = [c for c in df.columns if "SF06" in c][0]
    time_col = [c for c in df.columns if "Time (s)" in c][0]

    df[time_col] = pd.to_numeric(df[time_col], errors="coerce")
    df[flow_col] = pd.to_numeric(df[flow_col], errors="coerce")
    df = df.dropna(subset=[time_col, flow_col])

    if dispense_time <= 0:
        raise ValueError("Dispense time must be positive.")

    half_window = dispense_time / 2.0
    start_time = dispense_time / 4.0
    end_time = start_time + half_window

    sampling = df[(df[time_col] >= start_time) & (df[time_col] <= end_time)]
    if sampling.empty:
        return float("nan")
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
        self.prompt_event = threading.Event()
        self.prompt_result = None
        self.resume_selection = None

        self._build_layout()
        self._poll_queue()
        self.root.after(300, self._check_for_resume_candidates)

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
        self._add_input_field(left, 1, "Solution/calibration name:", "application_entry")
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
            "- On startup the app asks to resume any unfinished calibration folder, meaning one that has\n"
            "  trial CSVs but no calibration_coeffs.txt and no calibration_plot.png.\n"
            "- Resuming reuses the saved trials below the chosen start pressure and re-runs everything\n"
            "  from it upward, then saves the coefficients and plot as usual.\n"
            "- After each pressure group completes, refill the reservoir before continuing to the next pressure.\n"
            "- Click refill and continue when you finish refilling.\n"
            "- Prime the liquid lines before injection to remove trapped air and ensure consistent flow.\n"
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

    def _get_resume_dispense_time(self) -> float:
        '''Best-effort parse of the current dispense time for resume scanning.'''
        try:
            return float(self.dispense_time_entry.get())
        except ValueError:
            return 30.0

    def _get_trials_per_pressure(self) -> int:
        '''Best-effort parse of the trials-per-pressure field for resume scanning.'''
        try:
            trials = int(self.trials_entry.get())
            return trials if trials > 0 else 4
        except ValueError:
            return 4

    def _get_planned_pressures(self):
        '''Best-effort pressure list from the current bounds, or None if they are not usable yet.'''
        try:
            return generate_pressure_list(
                float(self.lower_pressure_entry.get()),
                float(self.upper_pressure_entry.get()),
                float(self.pressure_increment_entry.get()),
            )
        except (ValueError, TypeError):
            return None

    def _describe_candidate(self, results, trials_per_pressure) -> str:
        '''Summarize how many trials each pressure target already has.'''
        lines = []
        for result in results:
            count = len(result["trial_flows"])
            state = "complete" if count >= trials_per_pressure else "incomplete"
            lines.append(
                f"  {result['pressure']:g} kPa: {count}/{trials_per_pressure} trials ({state}), "
                f"mean {result['mean']:.2f} uL/min"
            )
        return "\n".join(lines)

    def _populate_existing_results(self, results):
        '''Show previously completed pressure rows in the GUI table.'''
        self.tree.delete(*self.tree.get_children())
        for result in results:
            self.update_pressure_complete_row(
                pressure=result["pressure"],
                trial_flows=result["trial_flows"],
                mean=result["mean"],
                std=result["std"],
            )

    def _check_for_resume_candidates(self):
        '''Offer to resume an unfinished calibration folder if one is detected.

        A folder counts as unfinished when it holds trial CSVs but no coefficients
        file and no plot, which is what a calibration that stopped part way through
        leaves behind. Nothing is asked when every folder is already finished.
        '''
        candidates = discover_incomplete_calibrations(CALIB_DATA_DIR, dispense_time=self._get_resume_dispense_time())
        if not candidates:
            return

        trials_per_pressure = self._get_trials_per_pressure()

        if len(candidates) == 1:
            selected = candidates[0]
            question = (
                f"Found an unfinished calibration: {selected['name']}\n"
                "(it has trial data but no coefficients file and no plot)\n\n"
                f"{self._describe_candidate(selected['results'], trials_per_pressure)}\n\n"
                "Continue this calibration?"
            )
        else:
            selected = None
            question = (
                f"Found {len(candidates)} unfinished calibrations in {CALIB_DATA_DIR}:\n\n"
                + "\n".join(f"  {candidate['name']}" for candidate in candidates)
                + "\n\nContinue one of them?"
            )

        if not messagebox.askyesno("Resume calibration?", question):
            return

        if selected is None:
            names = [candidate["name"] for candidate in candidates]
            selection = simpledialog.askstring(
                "Resume calibration",
                "Enter the calibration folder name to resume:\n\n" + "\n".join(names),
            )
            if not selection:
                return
            selected = next(
                (candidate for candidate in candidates if candidate["name"].lower() == selection.strip().lower()),
                None,
            )
            if selected is None:
                messagebox.showwarning("Resume cancelled", "No matching calibration folder was selected.")
                return

        # The saved CSVs only make sense against the dispense time they were recorded
        # with, which may differ from the one used for the remaining pressures.
        existing_dispense_time = simpledialog.askfloat(
            "Existing dispense time",
            f"What dispense time (s) was used for the trials already in {selected['name']}?\n\n"
            "This only affects how the saved CSVs are re-read. You can still enter a\n"
            "different dispense time in the main window for the pressures you re-run.",
            initialvalue=self._get_resume_dispense_time(),
            minvalue=0.001,
        )
        if existing_dispense_time is None:
            self.status_var.set("Resume cancelled.")
            return

        results = collect_existing_pressure_results(Path(selected["path"]), dispense_time=existing_dispense_time)
        if not results:
            messagebox.showwarning(
                "Resume cancelled",
                f"No usable trial data could be read from {selected['name']} at a {existing_dispense_time} s dispense time.",
            )
            self.status_var.set("Resume cancelled.")
            return

        self.application_entry.delete(0, tk.END)
        self.application_entry.insert(0, selected["name"])
        self.resume_selection = {
            "name": selected["name"],
            "path": str(selected["path"]),
            "results": results,
            "existing_dispense_time": existing_dispense_time,
        }
        self._populate_existing_results(results)

        suggested = first_incomplete_pressure(
            results,
            trials_per_pressure,
            pressures=self._get_planned_pressures(),
        )
        prompt = (
            f"{self._describe_candidate(results, trials_per_pressure)}\n\n"
            "At which pressure should the calibration restart?\n"
            "Everything below it is reused from the saved trials, and everything\n"
            "from it upward is re-run and overwritten."
        )
        if suggested is not None:
            prompt += f"\n\n{suggested:g} kPa is the first target without a full set of trials."

        resume_from_pressure = simpledialog.askfloat(
            "Start pressure",
            prompt,
            initialvalue=suggested if suggested is not None else None,
        )
        if resume_from_pressure is None:
            self.resume_selection = None
            self.tree.delete(*self.tree.get_children())
            self.status_var.set("Resume cancelled.")
            return

        self.resume_selection["resume_from_pressure"] = resume_from_pressure
        reused = [result["pressure"] for result in results if result["pressure"] < resume_from_pressure]
        self.status_var.set(
            f"Resuming {selected['name']} from {resume_from_pressure:g} kPa "
            f"(reusing {', '.join(f'{p:g}' for p in reused) or 'no'} kPa). "
            "Enter the remaining settings, then press Start Calibration."
        )

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

        # Renaming the calibration means the loaded results no longer belong to it.
        if self.resume_selection and self.resume_selection["name"].lower() != application_name.lower():
            self.resume_selection = None
            self.status_var.set("Calibration name changed, so the resumed results were dropped.")

        resume_from_pressure = None
        if self.resume_selection:
            resume_from_pressure = self.resume_selection.get("resume_from_pressure")
            if resume_from_pressure is not None and not any(
                abs(p - resume_from_pressure) < 1e-9 for p in pressures
            ):
                raise ValueError(
                    f"The resume pressure {resume_from_pressure:g} kPa is not in the generated pressure list "
                    f"({', '.join(f'{p:g}' for p in pressures)}). Adjust the bounds or increment."
                )

        return {
            "application_name": application_name,
            "pressures": pressures,
            "dispense_time": dispense_time,
            "p_tol": p_tol,
            "trials_per_pressure": trials,
            "resume_from_pressure": resume_from_pressure,
            "resume_selection": self.resume_selection,
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
        if self.resume_selection and self.resume_selection.get("results"):
            self._populate_existing_results(self.resume_selection["results"])
        
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

    def _request_trial_review(self, pressure, trial_flows, data_dir):
        '''Ask the user whether to keep all trials or remove one or more bad trials for a pressure target.'''
        self.prompt_result = None
        self.prompt_event.clear()
        self.gui_queue.put(("prompt_review_pressure", {
            "pressure": pressure,
            "trial_flows": trial_flows.copy(),
            "data_dir": str(data_dir),
        }))
        self.prompt_event.wait()
        return self.prompt_result or {"action": "keep", "removed_trials": []}

    def run_calibration_worker(self, settings: dict):
        '''Background worker that performs the calibration loop.'''
        application_name = settings["application_name"]
        pressures = settings["pressures"]
        dispense_time = settings["dispense_time"]
        p_tol = settings["p_tol"]
        trials_per_pressure = settings["trials_per_pressure"]
        resume_from_pressure = settings.get("resume_from_pressure")
        resume_selection = settings.get("resume_selection") or {}
        existing_results = {
            float(result["pressure"]): result for result in resume_selection.get("results", [])
        }

        data_dir = CALIB_DATA_DIR / application_name
        data_dir.mkdir(parents=True, exist_ok=True)
        self.current_data_dir = data_dir

        calibration_results = []
        pipe = None

        try:
            pipe = uProcess.OpenCommandPipe()

            for p in pressures:
                if resume_from_pressure is not None and p < resume_from_pressure:
                    # Reuse the saved trials rather than dropping the pressure, so the
                    # fit, summary CSV, and plot cover the whole sweep.
                    existing = existing_results.get(float(p))
                    if existing is None:
                        self.gui_queue.put((
                            "status",
                            f"No saved trials found for {p} kPa, so it is left out of the calibration fit."
                        ))
                        continue

                    calibration_results.append((p, existing["mean"], existing["std"]))
                    self.gui_queue.put(("pressure_complete", {
                        "pressure": p,
                        "trial_flows": list(existing["trial_flows"]),
                        "mean": existing["mean"],
                        "std": existing["std"],
                    }))
                    continue

                if resume_from_pressure is not None and p >= resume_from_pressure:
                    for existing_csv in sorted(data_dir.glob(f"{LOG_PREFIX}_{p}kPa_trial*.csv")):
                        try:
                            existing_csv.unlink()
                        except FileNotFoundError:
                            pass
                    review_log_path = data_dir / f"review_{p}kPa.csv"
                    if review_log_path.exists():
                        review_log_path.unlink()

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

                    avg_flow = extract_flow(output_csv, dispense_time)
                    trial_flows.append(avg_flow)

                    self.gui_queue.put(("trial_result", {
                        "pressure": p,
                        "trial_flows": trial_flows.copy()
                    }))

                review_decision = self._request_trial_review(p, trial_flows, data_dir)
                if review_decision.get("action") == "remove":
                    trial_numbers = review_decision.get("trial_numbers", [])
                    if trial_numbers:
                        trial_flows, removed_entries = remove_trials_by_numbers(trial_flows, trial_numbers)
                        review_log_path = os.path.join(data_dir, f"review_{p}kPa.csv")
                        review_df = pd.DataFrame(removed_entries)
                        if review_df.empty:
                            review_df = pd.DataFrame(columns=["trial_number", "flow_value"])
                        review_df.to_csv(review_log_path, index=False)
                        self.gui_queue.put(("status", f"Removed trials {', '.join(map(str, trial_numbers))} at {p} kPa; recomputing mean/std."))

                mean_flow = float(np.mean(trial_flows)) if trial_flows else 0.0
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

                elif msg_type == "prompt_review_pressure":
                    pressure = payload["pressure"]
                    trial_flows = payload["trial_flows"]
                    data_dir = payload.get("data_dir")
                    prompt_text = format_trial_values_for_prompt(pressure, trial_flows)
                    keep_all = messagebox.askyesno(
                        "Review trials",
                        f"Keep all {len(trial_flows)} trial results for {pressure} kPa?\n\n{prompt_text}"
                    )

                    if keep_all:
                        self.prompt_result = {"action": "keep", "removed_trials": []}
                    elif len(trial_flows) <= 1:
                        messagebox.showinfo(
                            "No removal possible",
                            "Only one trial remains, so there is nothing to remove."
                        )
                        self.prompt_result = {"action": "keep", "removed_trials": []}
                    else:
                        entry = simpledialog.askstring(
                            "Remove one or more trials",
                            f"Which trial numbers should be removed for {pressure} kPa?\n"
                            f"Enter comma-separated numbers from 1 to {len(trial_flows)}\n\n{prompt_text}",
                        )
                        if entry is None or not entry.strip():
                            self.prompt_result = {"action": "keep", "removed_trials": []}
                        else:
                            try:
                                trial_numbers = [int(part.strip()) for part in entry.split(',') if part.strip()]
                                if any(number < 1 or number > len(trial_flows) for number in trial_numbers):
                                    raise ValueError("Out of range")
                                self.prompt_result = {"action": "remove", "trial_numbers": trial_numbers}
                            except Exception:
                                messagebox.showwarning(
                                    "Invalid input",
                                    "Please enter valid trial numbers separated by commas."
                                )
                                self.prompt_result = {"action": "keep", "removed_trials": []}

                    if data_dir:
                        review_log_path = os.path.join(data_dir, f"review_{pressure}kPa.csv")
                        if os.path.exists(review_log_path):
                            os.remove(review_log_path)

                    self.prompt_event.set()

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
