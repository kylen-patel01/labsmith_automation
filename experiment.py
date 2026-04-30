"""experiment.py
A small application for configuring and running pressure-driven dispense experiments.

This script provides a Tkinter GUI that lets the user select a calibration calibration curve,
enter desired flow and volume for multiple reservoirs, compute pressures and dispense times,
run uProcess scripts for each reservoir, collect output CSV files, compute steady-state flow
values, and generate individual and combined plots.

Author: Kylen Patel
Last modified: 4/14/26
"""

# Standard library imports
import os
import glob
import time
import tkinter as tk
from tkinter import ttk, messagebox
from collections import deque
import threading
import sys
import subprocess
import json

# Third-party imports
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from PIL import Image, ImageTk

# uProcess hardware control wrapper
import uProcess
'''
Path setup. BASE_DIR is the folder where this script lives
All other paths are defined relative to this folder so the project remains portable
'''
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__)))

# Folder that contains saved calibration subfolders
CALIB_DIR = os.path.join(BASE_DIR, "CalibrationData")

# Base uProcess script for experiments
# Recall: Python prepends the run-specific header to this file before sending it to uProcess 
BASE_USQ = os.path.join(BASE_DIR, "experiment.usq")

def find_calibration_curves(calib_root):
    '''
    Scan the calibration folder and build a dictionary of available calibrations 
    Each calibration is stored in its own subfolder. The function looks for: 
    - calibration_coeffs.txt file containing slope/intercept/r2 values
    - optional calibration_curve.csv with raw data points
    - optional calibration plot PNG for display in the GUI

    Returns: 
    A dict keyed by subfolder name (the names the user sees in the GUI)
    Each value is a dict with keys: slope, intercept, r2, dir (full path to subfolder), path (full path to coeffs file), curve_csv (full path to csv), curve_png (full path to png)
    '''
    # Initialize empty dictionary for curves
    curves = {}
    # Check if the calibration root directory exists
    if not os.path.isdir(calib_root):
        return curves
    # Iterate through each entry in the directory
    for entry in sorted(os.listdir(calib_root)):
        full = os.path.join(calib_root, entry)
        # Skip if not a directory
        if not os.path.isdir(full):
            continue
        # Initialize dictionary for this calibration
        vals = {'slope': None, 'intercept': None, 'r2': None, 'dir': full, 'path': None, 'curve_csv': None, 'curve_png': None}
        # Search the folder for the expected calibration files
        for f in os.listdir(full):
            # Parse coefficients if coeffs file found
            if f.lower().startswith('calibration_coeffs') and f.lower().endswith('.txt'):
                vals.update(parse_coeffs_file(os.path.join(full, f)))
                vals['path'] = os.path.join(full, f)
            # Set CSV path if curve file found
            if f.lower().startswith('calibration_curve') and f.lower().endswith('.csv'):
                vals['curve_csv'] = os.path.join(full, f)
            # Set PNG path if plot image found
            if f.lower().endswith('.png') and ('plot' in f.lower() or 'calib' in f.lower() or 'calibration' in f.lower()):
                vals['curve_png'] = os.path.join(full, f)
        
        # Add this calibration to the curves dictionary
        curves[entry] = vals
    # Return the curves dictionary
    return curves

def parse_coeffs_file(path):
    '''
    Read a calibration_coeffs.txt file and extract slope, intercept, and r2 values. The file is expected to have lines like:
    Expectd file format: 
        Slope = m
        Intercept = y
        R2 = #
    '''
    vals = {'slope': None, 'intercept': None, 'r2': None}
    try:
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith('slope'):
                    vals['slope'] = float(line.split('=')[1])
                elif line.lower().startswith('intercept'):
                    vals['intercept'] = float(line.split('=')[1])
                elif line.lower().startswith('r2'):
                    try:
                        vals['r2'] = float(line.split('=')[1])
                    except Exception:
                        vals['r2'] = None
    except Exception:
        # If parsing fails, leave values as None
        pass
    return vals


# uProcess script preparation
def build_script(target_pressure, dispense_time_s, trial_csv, p_tol=0.1, position=None, 
                 use_thermal=False, temp_lower=None, temp_upper=None, temp_dwell=None, temp_ramp=None):
    '''
    Build the full script text that will be sent to uProcess
    
    The script sent to uProcess is made of two pieces:
    1. A short parameter header generated by Python for this specific run. 
    2. The validated base .usq script containing the hardware logic 

    Parameters:
    - target_pressure: the pressure to apply for this run (kPa)
    - dispense_time_s: the time to keep the pressure on (seconds)
    - trial_csv: the path where the run's output CSV should be saved (this is passed to the script so it can log data there)
    - p_tol: pressure tolerance passed into the script
    - position: reservoir position used by the AV801 selector value 
    - use_thermal: whether to enable thermal control (boolean or 1/0)
    - temp_lower: temperature lower bound (C)
    - temp_upper: temperature upper bound (C)
    - temp_dwell: thermal dwell time (s)
    - temp_ramp: temperature ramp rate (V/s)
    '''
    header = (
        f" ;Parameters \r\n"
        f"p_target = {target_pressure} kPa\r\n"
        f"p_tol = {p_tol} kPa \r\n"
        f"dispense_time = {dispense_time_s} s\r\n"
        f"output_file = \"{trial_csv}\"\r\n"
    )
    # Include thermal control parameters if enabled
    if use_thermal:
        header += (
            f"use_thermal = 1\r\n"
            f"temp_lower = {temp_lower} C\r\n"
            f"temp_upper = {temp_upper} C \r\n"
            f"temp_dwell = {temp_dwell} s\r\n"
            f"ramp_rate = {temp_ramp} V/s\r\n"
        )
    else:
        header += f"UseThermalControl = 0\r\n"
    
    # Include reservoir position parameter if provided
    if position is not None:
        header += f"ReservoirPosition = {position}\r\n"
    header += "\r\n"
    if not os.path.exists(BASE_USQ):
        raise FileNotFoundError(f"Base USQ not found: {BASE_USQ}")
    with open(BASE_USQ, 'r', encoding='ascii', errors='replace') as f:
        base = f.read()
    script = header + base
    return script


# CSV parsing/flow analysis

def extract_flow(csv_file):
    '''
    Read a uProcess .csv file and identify the time and flow columns.

    The function expects the output format produced by the uProcess experiment script.
    It first looks for named columns containing 'Time (s)' and 'SF06'. If those are absent,
    it falls back to the first two numeric columns in the file.

    Returns:
    (time_col_name, flow_col_name, dataframe)
    '''
    df = pd.read_csv(csv_file, sep=';', skiprows=[0])
    df.columns = df.columns.str.strip()
    flow_col = [c for c in df.columns if 'SF06' in c]
    time_col = [c for c in df.columns if 'Time (s)' in c]
    if not flow_col or not time_col:
        # fallback: try first numeric columns
        numeric = df.select_dtypes(include=[np.number])
        if numeric.shape[1] < 2:
            return None, None, df
        time_col = numeric.columns[0]
        flow_col = numeric.columns[1]
    else:
        time_col = time_col[0]
        flow_col = flow_col[0]
    df[time_col] = pd.to_numeric(df[time_col], errors='coerce')
    df[flow_col] = pd.to_numeric(df[flow_col], errors='coerce')
    df = df.dropna(subset=[time_col, flow_col])
    return (time_col, flow_col, df)


def compute_steady_mean_from_dispense(df, time_col, flow_col, dispense_time_s, position=None):
    '''
    Compute the steady state mean flow from the dispense portion of the data file.

    The function identifies the dispense start using the valve selector position change
    (if available), then computes the average flow over the middle half of the expected
    dispense interval.

    Parameters:
    - df: the DataFrame containing the data
    - time_col: the name of the time column
    - flow_col: the name of the flow column
    - dispense_time_s: the time to keep the pressure on (seconds)
    - position: the reservoir position used by the AV801 selector value

    Returns:
    The steady-state mean flow or None if it cannot be computed
    '''

    # If the data are missing, we cannot compute a steady-state average
    if df is None or len(df) == 0:
        return None
    times = df[time_col].values
    flows = df[flow_col].values
    # Try to detect start based on valve 2 state (position) if provided
    t_start = None
    if position is not None:
        valve_col = None
        for c in df.columns:
            cl = c.lower()
            if 'v2' in cl and 'state' in cl:
                valve_col = c
                break
        if valve_col is not None:
            # attempt numeric match first, then string
            try:
                vals_num = pd.to_numeric(df[valve_col], errors='coerce')
                match_idx = np.where(vals_num == float(position))[0]
            except Exception:
                match_idx = np.array([], dtype=int)
            if match_idx.size == 0:
                # fallback to string comparison
                try:
                    match_idx = np.where(df[valve_col].astype(str).str.strip() == str(position))[0]
                except Exception:
                    match_idx = np.array([], dtype=int)
            if match_idx.size > 0:
                try:
                    t_start = float(df.iloc[int(match_idx[0])][time_col])
                except Exception:
                    t_start = None

    # if we didn't get a start from valve position, we cannot determine dispense start reliably
    if t_start is None:
        return None

    # define middle-half window within dispense time
    if dispense_time_s is None or dispense_time_s <= 0:
        # fallback: average middle half of entire recording
        df_sorted = df.sort_values(time_col)
        n = len(df_sorted)
        if n < 4:
            sample = df_sorted
        else:
            start = n // 4
            end = n - start
            sample = df_sorted.iloc[start:end]
        return float(np.mean(sample[flow_col]))

    mid_start = t_start + dispense_time_s / 4.0
    mid_end = t_start + 3.0 * dispense_time_s / 4.0
    sample = df[(df[time_col] >= mid_start) & (df[time_col] <= mid_end)]
    if sample.empty:
        # try the full dispense window
        sample = df[(df[time_col] >= t_start) & (df[time_col] <= t_start + dispense_time_s)]
        if sample.empty:
            return None
    return float(np.mean(sample[flow_col]))


def plot_overlay(df, time_col, flow_col, pressure_col, out_png, title=None):
    '''
    Create an overlay plot of flow and pressure vs. time for a single run or combined run 
    Flow is plotted on the left y-axis and pressure on the right y-axis.
    '''
    try:
        fig = Figure(figsize=(8, 4))
        ax1 = fig.add_subplot(111)
        ax1.plot(df[time_col], df[flow_col], color='tab:blue', label='Flow (uL/min)')
        # use a friendly x-label when the column represents seconds
        if isinstance(time_col, str) and 'time' in time_col.lower():
            ax1.set_xlabel('Time (s)')
        else:
            ax1.set_xlabel(time_col)
        ax1.set_ylabel('Flow (uL/min)', color='tab:blue')
        ax1.tick_params(axis='y', labelcolor='tab:blue')
        if pressure_col and pressure_col in df.columns:
            ax2 = ax1.twinx()
            ax2.plot(df[time_col], df[pressure_col], color='tab:red', label='Pressure (kPa)')
            ax2.set_ylabel('Pressure (kPa)', color='tab:red')
            ax2.tick_params(axis='y', labelcolor='tab:red')
        if title:
            ax1.set_title(title)
        fig.tight_layout()
        fig.savefig(out_png, dpi=150)
        return True
    except Exception:
        return False

def plot_temperature(df, time_col, temp_col, out_png, title=None):
    '''
    Create a temperature vs. time plot for a single run or combined run.
    '''
    try:
        fig = Figure(figsize=(8, 4))
        ax = fig.add_subplot(111)
        ax.plot(df[time_col], df[temp_col], color='tab:orange', linewidth=2, label='Temperature')
        if isinstance(time_col, str) and 'time' in time_col.lower():
            ax.set_xlabel('Time (s)')
        else:
            ax.set_xlabel(time_col)
        ax.set_ylabel('Temperature (C)', color='tab:orange')
        ax.tick_params(axis='y', labelcolor='tab:orange')
        ax.grid(True, alpha=0.3)
        if title:
            ax.set_title(title)
        fig.tight_layout()
        fig.savefig(out_png, dpi=150)
        return True
    except Exception:
        return False

# GUI CLASS

class ExperimentGUI:
    '''
    Tkinter-based GUI for selecting a calibration and running one or more experiments.

    The GUI lets the user:
    - choose a calibration curve
    - input desired flow rates and volumes for up to 12 reservoirs
    - compute the required pressures and dispense times based on the selected calibration
    - run the experiments sequentially, saving output CSVs for each reservoir
    - launch the full experiment sequence
    - view results and flow/pressure overlays after each run
    - monitor live state for integration with a live plotting script
    - handle errors and provide feedback through the interface
    '''
    def __init__(self, root):
        '''
        Initialize the ExperimentGUI with the root Tkinter window.
        
        Sets up the main layout with left plot area and right controls,
        initializes calibration curves, creates input fields for reservoirs,
        and prepares for live plotting integration.
        
        Parameters:
        - root: The Tkinter root window
        
        Returns: None
        '''
        # Set the root window reference
        self.root = root
        # Set the window title
        root.title('uProcess Experiment Runner')
        # Load available calibration curves from the directory
        self.curves = find_calibration_curves(CALIB_DIR)

        # Layout: left = plot, right = controls
        # Create left frame for plot area
        left = ttk.Frame(root)
        left.grid(row=0, column=0, rowspan=20, sticky='nswe', padx=6, pady=6)
        # Create right frame for controls
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky='nswe', padx=6, pady=6)
        # Configure right frame columns for better layout
        right.grid_columnconfigure(0, weight=0)
        right.grid_columnconfigure(1, weight=1)
        right.grid_columnconfigure(2, weight=0)
        right.grid_columnconfigure(3, weight=0)

        # Plot area
        # Image label for displaying calibration png (preferred)
        self.img_label = tk.Label(left)
        self.img_label.pack(fill='both', expand=True)
        # fallback: matplotlib canvas if no png
        self.fig = Figure(figsize=(5, 4))
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=left)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)

        # Controls
        # Label for calibration curve selection
        ttk.Label(right, text='Calibration Curve:').grid(row=0, column=0, sticky='w')
        # Get sorted list of curve names
        self.curve_names = sorted(list(self.curves.keys()))
        # Create combobox for selecting calibration curve
        self.curve_cb = ttk.Combobox(right, values=self.curve_names, state='readonly')
        self.curve_cb.grid(row=0, column=1, sticky='ew')
        # Bind event for curve selection
        self.curve_cb.bind('<<ComboboxSelected>>', self.on_curve_selected)

        # Universal settings
        # Label for threshold percentage
        ttk.Label(right, text='Threshold (%) :').grid(row=1, column=0, sticky='w')
        # Create entry for threshold
        self.threshold_entry = tk.Entry(right)
        self.threshold_entry.insert(0, '10')
        self.threshold_entry.grid(row=1, column=1, sticky='ew')

        # Label for experiment name
        ttk.Label(right, text='Experiment name:').grid(row=2, column=0, sticky='w')
        # Create entry for experiment name
        self.output_label = tk.Entry(right)
        self.output_label.insert(0, 'exp1')
        self.output_label.grid(row=2, column=1, sticky='ew')

        # Label for number of experiments
        ttk.Label(right, text='Number of experiments:').grid(row=3, column=0, sticky='w')
        # Create variable for number of experiments
        self.num_experiments_var = tk.IntVar(value=3)
        # Create combobox for number of experiments
        self.num_ex_cb = ttk.Combobox(right, values=list(range(1, 13)), textvariable=self.num_experiments_var, state='readonly', width=5)
        self.num_ex_cb.grid(row=3, column=1, sticky='w')
        # Bind event for experiment count change
        self.num_ex_cb.bind('<<ComboboxSelected>>', self.on_experiment_count_changed)

        # Thermal control section
        ttk.Separator(right, orient='horizontal').grid(row=4, column=0, columnspan=4, sticky='ew', pady=5)
        
        # Checkbox for enabling thermal control (per-experiment basis)
        self.thermal_enabled_var = tk.BooleanVar(value=False)
        self.thermal_check = ttk.Checkbutton(right, text='Enable Temperature Control', variable=self.thermal_enabled_var, command=self.on_thermal_control_toggled)
        self.thermal_check.grid(row=5, column=0, columnspan=2, sticky='w', pady=2)

        # Scrollable frame for reservoir controls
        # Create container frame for scrollable area
        self.res_container = ttk.Frame(right)
        self.res_container.grid(row=6, column=0, columnspan=3, sticky='nsew')
        # Create canvas for scrolling
        self.res_canvas = tk.Canvas(self.res_container, borderwidth=0, highlightthickness=0, height=520)
        # Create scrollbar
        self.res_scrollbar = ttk.Scrollbar(self.res_container, orient='vertical', command=self.res_canvas.yview)
        # Create inner frame for inputs
        self.res_inner = ttk.Frame(self.res_canvas)
        # Bind configure event to update scroll region
        self.res_inner.bind('<Configure>', lambda e: self.res_canvas.configure(scrollregion=self.res_canvas.bbox('all')))
        # Create window in canvas
        self.res_canvas.create_window((0, 0), window=self.res_inner, anchor='nw')
        # Configure canvas yscroll
        self.res_canvas.configure(yscrollcommand=self.res_scrollbar.set)
        # Grid canvas and scrollbar
        self.res_canvas.grid(row=0, column=0, sticky='nsew')
        self.res_scrollbar.grid(row=0, column=2, sticky='ns')
        # Configure grid weights
        self.res_container.grid_rowconfigure(0, weight=1)
        self.res_container.grid_columnconfigure(0, weight=1)
        self.res_container.grid_columnconfigure(2, weight=0)  # Added for scrollbar column

        # Create result frame
        self.result_frame = ttk.Frame(right)
        self.result_frame.grid(row=7, column=0, columnspan=3, sticky='w')

        # Create status variable and label
        self.status_var = tk.StringVar(value='Ready')
        self.status_label = ttk.Label(right, textvariable=self.status_var)
        self.status_label.grid(row=8, column=0, columnspan=3, sticky='w')

        # Create notes frame for thermal control info
        self.notes_frame = ttk.Frame(right)
        self.notes_frame.grid(row=9, column=0, columnspan=3, sticky='w')
        ttk.Label(self.notes_frame, text='Note: Temperature is stabilized before pressurization begins', font=('TkDefaultFont', 9, 'italic')).grid(row=0, column=0, columnspan=3, sticky='w', padx=0, pady=1)
        ttk.Label(self.notes_frame, text='Note: Interconnect volume must be added manually to dispense volume. If dispense volume is large, consider splitting into multiple experiment runs', font=('TkDefaultFont', 9, 'italic')).grid(row=1, column=0, columnspan=3, sticky='w', padx=0, pady=1)

        # Create compute and run buttons
        self.compute_btn = ttk.Button(right, text='Compute All', command=self.compute_all)
        self.compute_btn.grid(row=10, column=0, pady=6)
        self.run_btn = ttk.Button(right, text='Run Experiment', command=self.run_experiment)
        self.run_btn.grid(row=10, column=2, pady=6)

        # Initialize lists for reservoirs and result variables
        self.reservoirs = []
        self.result_vars = []
        # Build the initial reservoir input fields
        self.build_reservoir_inputs()
        
        # For live plotting
        # Set path for live state JSON file
        self.live_state_file = os.path.join(BASE_DIR, "live_state.json")
        # Initialize plot process variable
        self.plot_proc = None

    def build_reservoir_inputs(self):
        '''
        Build the input fields for each reservoir based on the selected number of experiments.
        Arranged in a 2-column grid: experiments 1-2 in first row, 3-4 in second, etc.
        '''
        # Clear existing widgets
        for child in self.res_inner.winfo_children():
            child.destroy()
        for child in self.result_frame.winfo_children():
            child.destroy()

        # Configure column weights for 2-column layout (labels: weight 0, entries: weight 1)
        self.res_inner.grid_columnconfigure(0, weight=0)  # Labels for column 1
        self.res_inner.grid_columnconfigure(1, weight=1)  # Entries for column 1
        self.res_inner.grid_columnconfigure(2, weight=0)  # Labels for column 2
        self.res_inner.grid_columnconfigure(3, weight=1)  # Entries for column 2
        
        # Configure result frame for 2 columns
        self.result_frame.grid_columnconfigure(0, weight=1)
        self.result_frame.grid_columnconfigure(1, weight=1)

        # Initialize lists
        self.reservoirs = []
        self.result_vars = []
        num_res = self.num_experiments_var.get()
        is_thermal_enabled = self.thermal_enabled_var.get()
        
        # Rows per experiment: 6 base + 4 thermal if enabled
        rows_per_experiment = 10 if is_thermal_enabled else 6
        spacing_rows = 2  # Extra rows between experiment pairs
        spacing_cols = 1  # Extra columns between left/right experiments

        # Build input fields for each experiment in 2-column grid
        for i in range(num_res):
            # Calculate position in grid with spacing
            col_offset = (i % 2) * (2 + spacing_cols)  # Add spacing between left/right columns
            row_base = (i // 2) * (rows_per_experiment + spacing_rows)  # Add spacing between vertical pairs
            
            # Create a frame for this experiment with a border
            exp_frame = tk.Frame(self.res_inner, relief='solid', borderwidth=2, bg='white')
            exp_frame.grid(row=row_base, column=col_offset, columnspan=2, sticky='nsew', padx=3, pady=3)
            
            # Configure frame grid
            exp_frame.grid_columnconfigure(0, weight=1)
            exp_frame.grid_columnconfigure(1, weight=1)
            
            # Bold header for each experiment
            header_label = ttk.Label(exp_frame, text=f'Experiment {i+1}', font=('TkDefaultFont', 10, 'bold'), background='white')
            header_label.grid(row=0, column=0, columnspan=2, sticky='w', pady=2, padx=5)
            
            # Position input
            ttk.Label(exp_frame, text='Valve Position (2-8):', background='white').grid(row=1, column=0, sticky='w', padx=5, pady=3)
            pos = tk.Entry(exp_frame)
            pos_val = min(8, 2 + i)
            pos.insert(0, str(pos_val))
            pos.grid(row=1, column=1, sticky='ew', padx=5, pady=3)
            
            # Flow input
            ttk.Label(exp_frame, text='Flow (uL/min):', background='white').grid(row=2, column=0, sticky='w', padx=5, pady=3)
            flow = tk.Entry(exp_frame)
            flow.insert(0, '100')
            flow.grid(row=2, column=1, sticky='ew', padx=5, pady=3)
            
            # Volume input
            ttk.Label(exp_frame, text='Dispense Volume (uL):', background='white').grid(row=3, column=0, sticky='w', padx=5, pady=3)
            vol = tk.Entry(exp_frame)
            vol.insert(0, '100')
            vol.grid(row=3, column=1, sticky='ew', padx=5, pady=3)
            
            # Pressure tolerance input
            ttk.Label(exp_frame, text='Pressure Tolerance (kPa):', background='white').grid(row=4, column=0, sticky='w', padx=5, pady=3)
            p_tol = tk.Entry(exp_frame, width=10)
            p_tol.insert(0, '0.1')
            p_tol.grid(row=4, column=1, sticky='ew', padx=5, pady=3)
            
            # Pressure and time labels
            pressure_lbl = tk.StringVar(value='Target Pressure: N/A')
            time_lbl = tk.StringVar(value='Dispense Time: N/A')
            ttk.Label(exp_frame, textvariable=pressure_lbl, font=('TkDefaultFont', 9, 'bold'), background='white').grid(row=5, column=0, sticky='w', padx=5, pady=3)
            ttk.Label(exp_frame, textvariable=time_lbl, font=('TkDefaultFont', 9, 'bold'), background='white').grid(row=5, column=1, sticky='w', padx=5, pady=3)
            
            # Thermal control fields if enabled
            temp_lower = None
            temp_upper = None
            temp_dwell = None
            temp_ramp = None
            
            if is_thermal_enabled:
                # Temperature Control Parameters header
                ttk.Label(exp_frame, text='Temperature Control Parameters', font=('TkDefaultFont', 10, 'bold'), background='white').grid(row=6, column=0, columnspan=2, sticky='w', padx=5, pady=(8, 3))
                
                # Temperature lower bound
                ttk.Label(exp_frame, text='Temp Lower (C):', background='white').grid(row=7, column=0, sticky='w', padx=5, pady=3)
                temp_lower = tk.Entry(exp_frame, width=10)
                temp_lower.insert(0, '24')
                temp_lower.grid(row=7, column=1, sticky='ew', padx=5, pady=3)
                
                # Temperature upper bound
                ttk.Label(exp_frame, text='Temp Upper (C):', background='white').grid(row=8, column=0, sticky='w', padx=5, pady=3)
                temp_upper = tk.Entry(exp_frame, width=10)
                temp_upper.insert(0, '26')
                temp_upper.grid(row=8, column=1, sticky='ew', padx=5, pady=3)
                
                # Thermal dwell time
                ttk.Label(exp_frame, text='Dwell Time (s):', background='white').grid(row=9, column=0, sticky='w', padx=5, pady=3)
                temp_dwell = tk.Entry(exp_frame, width=10)
                temp_dwell.insert(0, '60')
                temp_dwell.grid(row=9, column=1, sticky='ew', padx=5, pady=3)
                
                # Temperature ramp rate
                ttk.Label(exp_frame, text='Ramp Rate (V/s):', background='white').grid(row=10, column=0, sticky='w', padx=5, pady=3)
                temp_ramp = tk.Entry(exp_frame, width=10)
                temp_ramp.insert(0, '0.001')
                temp_ramp.grid(row=10, column=1, sticky='ew', padx=5, pady=3)
            
            # Store reservoir data
            self.reservoirs.append({
                'pos': pos, 'flow': flow, 'vol': vol, 'p_tol': p_tol, 
                'pressure_lbl': pressure_lbl, 'time_lbl': time_lbl,
                'temp_lower': temp_lower, 'temp_upper': temp_upper, 
                'temp_dwell': temp_dwell, 'temp_ramp': temp_ramp
            })

        # Build result labels in 2 columns
        for i in range(num_res):
            result_row = i // 2
            result_col = i % 2
            rv = tk.StringVar(value=f'Experiment {i+1} Steady-State Avg: N/A (% difference from desired flow: N/A)')
            ttk.Label(self.result_frame, textvariable=rv).grid(row=result_row, column=result_col, sticky='w', padx=2, pady=1)
            self.result_vars.append(rv)

        # Update button texts
        self.compute_btn.configure(text=f'Compute All ({num_res} experiments)')
        self.run_btn.configure(text=f'Run Experiment ({num_res} experiments)')


    def on_experiment_count_changed(self, event=None):
        '''
        Update the number of reservoir input fields when the experiment count is changed.
        Validates the count to ensure it's between 1 and 12, then rebuilds the reservoir inputs.
        
        Parameters:
        - event: Tkinter event (optional, for binding)
        
        Returns: None
        '''
        # Try to get the count from the variable
        try:
            count = int(self.num_experiments_var.get())
        except Exception:
            count = 3
            self.num_experiments_var.set(count)
        # Rebuild the reservoir input fields
        self.build_reservoir_inputs()

    def on_thermal_control_toggled(self):
        '''
        Rebuild the reservoir inputs when thermal control checkbox is toggled.
        This shows/hides the thermal fields for each experiment.
        
        Returns: None
        '''
        self.build_reservoir_inputs()


    def write_live_state(self, csv_path = None, run_index = 0, done = False):
        '''
        Write a small JSON file that tells the external live plotter which CSV file 
        is currently active. The live plotter can read this file and update its display accordingly.
        '''
        state = {
            "csv_path": csv_path,
            "run_index": run_index,
            "done": done
        }
        try:
            with open(self.live_state_file, "w") as f:
                json.dump(state, f)
        except Exception as e:
            print(f"write_live_state_error: {e}")
    
    def start_live_plotter(self):
        '''
        Launch the separate live_plotter_multi.py script as a subprocess. This script will read the live_state.json file 
        to know which CSV to plot and will update in real time as the experiment runs.
        '''
        try:
            if self.plot_proc is not None and self.plot_proc.poll() is None:
                return
            self.plot_proc = subprocess.Popen(
                [sys.executable, os.path.join(BASE_DIR, "live_plotter_multi.py"), self.live_state_file]
            )
        except Exception as e:
            print(f"could not start live plotter because: {e}")
    
    # Cleanly stop the live plotter before closing the GUI 
    def on_close(self):
        '''
        Cleanly stop the live plotter before closing the GUI.
        
        Writes the done state to the live state file and terminates the plot process.
        
        Returns: None
        '''
        # Write done state to live state JSON file
        try:
            self.write_live_state(csv_path = None, run_index = 0, done = True)
        except Exception:
            pass

        # Terminate the live plotter process if it's running
        try:
            if self.plot_proc is not None and self.plot_proc.poll() is None:
                self.plot_proc.terminate()
        except Exception:
            pass

        # Destroy the root window
        self.root.destroy()  
        

    def on_curve_selected(self, event=None):
        '''
        Handle the selection of a calibration curve from the dropdown.

        If a .png exists, show the image directly. 
        Otherwise, draw the line fit and any raw data points from the .csv on the matplotlib canvas.
        '''
        name = self.curve_cb.get()
        if not name:
            return
        info = self.curves.get(name, {})
        slope = info.get('slope')
        intercept = info.get('intercept')
        # Prefer showing a calibration PNG if available
        png = info.get('curve_png')
        if png and os.path.exists(png):
            try:
                img = Image.open(png)
                img = img.resize((600, 400), Image.LANCZOS)
                self._calib_photo = ImageTk.PhotoImage(img)
                self.img_label.config(image=self._calib_photo)
                self.img_label.image = self._calib_photo
                # hide canvas and show image instead of recomputing the plot
                self.canvas.get_tk_widget().pack_forget()
                self.img_label.lift()
                return
            except Exception:
                pass

        # fallback: draw the calibration fit and any raw CSV points
        # hide image label and ensure canvas is visible
        try:
            self.img_label.config(image='')
            self.img_label.pack_forget()
        except Exception:
            pass
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
        self.ax.clear()
        pressures = np.linspace(0, 10, 100)
        flows = slope * pressures + intercept
        self.ax.plot(pressures, flows, label='Linear fit')
        csv = info.get('curve_csv')
        if csv and os.path.exists(csv):
            try:
                dfc = pd.read_csv(csv)
                if 'Pressure_kPa' in dfc.columns and 'Flow_uLmin' in dfc.columns:
                    self.ax.scatter(dfc['Pressure_kPa'], dfc['Flow_uLmin'], color='C1', label='data')
            except Exception:
                pass
        self.ax.set_xlabel('Pressure (kPa)')
        self.ax.set_ylabel('Flow (uL/min)')
        self.ax.set_title(f'Calibration: {name}')
        self.ax.grid(True)
        self.ax.legend()
        self.canvas.draw()

    def compute_all(self):
        '''
        Compute the required pressure and dispense time for all reservoirs using the selected calibration curve.
        '''
        # Read the selected calibration and calculate each reservoir's target pressure and dispense duration
        name = self.curve_cb.get()
        if not name:
            messagebox.showerror('Error', 'Select a calibration curve first')
            return
        info = self.curves[name]
        slope = info.get('slope')
        intercept = info.get('intercept')
        for i, r in enumerate(self.reservoirs):
            try:
                desired_flow = float(r['flow'].get())
                vol = float(r['vol'].get())
            except Exception:
                messagebox.showerror('Error', f'Invalid inputs for experiment {i+1}')
                return
            # compute required pressure
            if slope == 0:
                pressure = None
            else:
                pressure = (desired_flow - intercept) / slope
            # compute dispense time
            if desired_flow <= 0:
                disp_time = None
            else:
                disp_time = (vol / desired_flow) * 60.0
            # update labels
            if pressure is None:
                r['pressure_lbl'].set('Pressure Target: N/A')
            else:
                r['pressure_lbl'].set(f'Pressure Target: {pressure:.3f} kPa')
            if disp_time is None:
                r['time_lbl'].set('Time (s): N/A')
            else:
                r['time_lbl'].set(f'Dispense Time: {disp_time:.2f} s')

        # Build status message including thermal control info if enabled
        status_msg = 'Computed values for all reservoirs'
        if self.thermal_enabled_var.get():
            status_msg += ' | Thermal control enabled'
        
        self.status_var.set(status_msg)

    def run_experiment(self):
        '''
        Run all experiment rows sequentially.

        For each configured reservoir row:
        - compute the required pressure and dispense time from the selected calibration
        - build a run-specific uProcess script and send it through the hardware pipe
        - wait for the run to finish and collect the generated CSV file
        - plot and save a single-run overlay of flow and pressure
        - compute steady-state flow metrics after the run sequence completes

        Once all runs are finished, the function also concatenates the individual CSV files
        into a single combined file with a continuous time axis.
        '''
        # Get the selected calibration curve name
        name = self.curve_cb.get()
        # Check if a calibration is selected
        if not name:
            messagebox.showerror('Error', 'Select a calibration curve first')
            return
        # Retrieve calibration info
        info = self.curves[name]
        # Get slope and intercept from calibration
        slope = info.get('slope')
        intercept = info.get('intercept')
        # Generate timestamp for output files
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        # Define experiment data directory
        EXP_DATA_DIR = os.path.join(BASE_DIR, 'ExperimentData')
        # Get experiment name or use timestamp
        exp_name = self.output_label.get().strip() or timestamp
        # Create output directory for this experiment
        out_dir = os.path.join(EXP_DATA_DIR, name, exp_name)
        os.makedirs(out_dir, exist_ok=True)
        # Initialize live plotting state
        self.write_live_state(csv_path = None, run_index = 0, done = False)
        # Start the live plotter subprocess
        self.start_live_plotter()

        # Set default threshold percentage
        threshold_pct = 10.0
        # Try to read threshold from entry
        try:
            threshold_pct = float(self.threshold_entry.get())
        except Exception:
            messagebox.showwarning('Warning', 'Invalid threshold, using 10%')

        # Check if thermal control is enabled globally
        use_thermal = self.thermal_enabled_var.get()

        # Initialize lists for CSV paths and run parameters
        individual_csvs = []
        run_params = []

        # Open a single pipe for all runs
        try:
            pipe = uProcess.OpenCommandPipe()
        except Exception as e:
            messagebox.showerror("Error", f'could not open uProcess pipe: {e}')
            return

        # Loop through each reservoir
        for i, r in enumerate(self.reservoirs):
            # Get desired flow, volume, and position
            try:
                desired_flow = float(r['flow'].get())
                vol = float(r['vol'].get())
                pos = int(r['pos'].get())
                p_tol = float(r['p_tol'].get())
            except Exception:
                messagebox.showerror('Error', f'Invalid inputs for experiment {i+1}')
                return

            # Extract thermal parameters if enabled
            temp_lower = None
            temp_upper = None
            temp_dwell = None
            temp_ramp = None
            if use_thermal:
                try:
                    temp_lower = float(r['temp_lower'].get())
                    temp_upper = float(r['temp_upper'].get())
                    temp_dwell = float(r['temp_dwell'].get())
                    temp_ramp = float(r['temp_ramp'].get())
                except Exception:
                    messagebox.showerror('Error', f'Invalid thermal parameters for experiment {i+1}')
                    return

            # Validate reservoir position
            if pos < 2 or pos > 8:
                messagebox.showerror('Error', f'Reservoir position for experiment {i+1} must be between 2 and 8')
                return

            # Calculate pressure
            if slope == 0:
                pressure = 0.0
            else:
                pressure = (desired_flow - intercept) / slope

            # Calculate dispense time
            if desired_flow <= 0:
                disp_time = 0
            else:
                disp_time = (vol / desired_flow) * 60.0

            # Update UI labels
            if slope == 0:
                r['pressure_lbl'].set('Pressure: N/A')
            else:
                r['pressure_lbl'].set(f'Pressure: {pressure:.3f} kPa')
            r['time_lbl'].set(f'Time (s): {disp_time:.2f}')

            # Store run parameters
            run_params.append({'desired_flow': desired_flow, 'disp_time': disp_time, 'pos': pos, 'out_csv': None})


            # Generate output CSV path
            out_csv = os.path.join(out_dir, f"{name}_RES{pos}_R{i+1}_{timestamp}.csv")
            out_csv = out_csv.replace('\\', '/')

            # Update live state for this run
            self.write_live_state(csv_path = out_csv, run_index = i, done = False)

            # Build and prepare the uProcess script with thermal parameters
            script_text = build_script(pressure, int(round(disp_time)), out_csv, p_tol=p_tol, position=pos,
                                     use_thermal=use_thermal, temp_lower=temp_lower, temp_upper=temp_upper,
                                     temp_dwell=temp_dwell, temp_ramp=temp_ramp)
            script_text = script_text.replace('\x00', '')
            script_text = script_text.replace('\n', '\r\n')

            # Run the experiment
            try:
                self.status_var.set(f'Running reservoir {i+1} at pos {pos}...')
                self.root.update()
                uProcess.SetScript(pipe, script_text)
                uProcess.Run(pipe)
                # Wait for run to complete
                while uProcess.IsRunning(pipe):
                    time.sleep(0.5)
                    self.root.update()
            except Exception as e:
                messagebox.showerror('Error', f'Run failed for reservoir {i+1}: {e}')
                return

            # Record the CSV path
            individual_csvs.append(out_csv)
            run_params[-1]['out_csv'] = out_csv
            # Extract flow data for plotting
            try:
                tcol, fcol, df = extract_flow(out_csv)
                cols = (tcol, fcol)
            except Exception:
                cols = None
                df = None

            # Plot single-run overlay
            if isinstance(cols, tuple) and df is not None:
                tcol, fcol = cols
                # Find pressure column
                pressure_col = None
                for c in df.columns:
                    cl = c.lower()
                    if 'press' in cl or 'kpa' in cl:
                        pressure_col = c
                        break
                # Generate plot file name
                fn = out_csv.replace('.csv', '.png')
                plot_overlay(df, tcol, fcol, pressure_col, fn, title=f'Res{i+1} trace')
            
            # Plot temperature if thermal control is enabled and temperature column exists
            if use_thermal and df is not None:
                try:
                    temp_col = None
                    for c in df.columns:
                        if 'T00389' in c or ('temp' in c.lower() and '(c)' in c.lower()):
                            temp_col = c
                            break
                    if temp_col:
                        temp_fn = out_csv.replace('.csv', '_temperature.png')
                        plot_temperature(df, tcol, temp_col, temp_fn, title=f'Res{i+1} temperature')
                except Exception:
                    pass


        # Close the pipe
        try:
            uProcess.CloseCommandPipe(pipe)
        except Exception:
            pass

        # Mark live plotting as done
        self.write_live_state(csv_path = None, run_index = 0, done = True)

        # Compute steady-state averages
        exceeded = []
        for idx, p in enumerate(run_params):
            out_csv = p.get('out_csv')
            desired_flow = p.get('desired_flow')
            disp_time = p.get('disp_time')
            avg = None
            try:
                tcol, fcol, dfi = extract_flow(out_csv)
                # Compute average using dispense detection
                avg = compute_steady_mean_from_dispense(dfi, tcol, fcol, dispense_time_s=disp_time, position=p.get('pos'))
            except Exception:
                avg = None
            if avg is None:
                self.result_vars[idx].set(f'Res{idx+1} Avg: N/A')
            else:
                # Calculate deviation percentage
                dev = abs(avg - desired_flow) / desired_flow * 100.0
                self.result_vars[idx].set(f'Res{idx+1} Avg: {avg:.2f} uL/min ({dev:.1f}%)')
                # Check if exceeds threshold
                if dev > threshold_pct:
                    exceeded.append((idx+1, avg, dev))

        # Show warning if any exceeded threshold
        if exceeded:
            msgs = [f'Res{r} avg {a:.1f} uL/min deviates {d:.1f}%' for r, a, d in exceeded]
            messagebox.showwarning('Threshold Exceeded', 'Some reservoirs exceeded input threshold:\n' + '\n'.join(msgs))

        # Concatenate CSVs 
        if individual_csvs:
            combined_path = os.path.join(out_dir, f"{name}_{exp_name}_{timestamp}_combined.csv")
            try:
                dfs = []
                cumulative_offset = 0.0
                time_col_name = None
                # Process each CSV
                for fpath in individual_csvs:
                    try:
                        dfi = pd.read_csv(fpath, sep=';', skiprows=[0])
                    except Exception:
                        dfi = pd.read_csv(fpath, sep=',')

                    # Handle time columns
                    tcols = [c for c in dfi.columns if 'time' in c.lower()]
                    if tcols:
                        tcol = tcols[0]
                        orig = dfi[tcol].astype(str)
                        num = pd.to_numeric(orig, errors='coerce')
                        if not num.isna().all():
                            dfi[tcol] = num
                        else:
                            # Parse time strings
                            parsed = pd.to_datetime(orig, errors='coerce')
                            if parsed.notna().any():
                                base = parsed.iloc[0]
                                secs = (parsed - base).dt.total_seconds()
                                dfi[tcol] = secs
                        # Clean and offset time
                        dfi[tcol] = pd.to_numeric(dfi[tcol], errors='coerce')
                        dfi = dfi.dropna(subset=[tcol])
                        if not dfi.empty:
                            min_t = float(dfi[tcol].min())
                            dfi[tcol] = dfi[tcol] - min_t + cumulative_offset
                            dfi["Total_Time"] = dfi[tcol]
                            cumulative_offset = float(dfi[tcol].max())
                        # Set canonical time column name
                        if time_col_name is None:
                            time_col_name = tcol
                        else:
                            if tcol != time_col_name:
                                dfi = dfi.rename(columns={tcol: time_col_name})
                    dfs.append(dfi)

                # Combine DataFrames
                combined = pd.concat(dfs, ignore_index=True, sort=False)
                combined.to_csv(combined_path, index=False)
                # Detect columns for plotting
                time_col = None
                flow_col = None
                pressure_col = None
                for c in combined.columns:
                    cl = c.lower()
                    if time_col is None and 'time' in cl:
                        time_col = c
                    if flow_col is None and ('sf' in cl or 'flow' in cl):
                        flow_col = c
                    if pressure_col is None and ('press' in cl or 'pressure' in cl or 'kpa' in cl):
                        pressure_col = c

                # Create combined plot
                if time_col and flow_col:
                    try:
                        over_png = combined_path.replace('.csv', '_overlay.png')
                        plot_overlay(combined, time_col, flow_col, pressure_col, over_png, title='Combined Flow & Pressure')
                    except Exception:
                        pass
                
                # Create combined temperature plot if thermal control was enabled
                if use_thermal:
                    try:
                        temp_col = None
                        for c in combined.columns:
                            if 'T00389' in c or ('temp' in c.lower() and '(c)' in c.lower()):
                                temp_col = c
                                break
                        if temp_col and time_col:
                            temp_png = combined_path.replace('.csv', '_temperature.png')
                            plot_temperature(combined, time_col, temp_col, temp_png, title='Combined Temperature')
                    except Exception:
                        pass
                
                messagebox.showinfo('Done', f'Experiment complete. Combined file: {combined_path}')

                self.status_var.set('Done')
            except Exception as e:
                messagebox.showwarning('Warning', f'Could not combine CSVs: {e}')
                self.status_var.set('Done with warnings')

# Script entry point
def main():
    '''
    Main entry point for the application.
    Initializes the Tkinter root window and launches the main GUI application.
    
    Returns: None
    '''
    # Create the Tkinter root window
    root = tk.Tk()
    # Set window geometry if supported
    root.geometry('1400x1100')
    root.minsize(1100, 800)
    # Create an instance of ExperimentGUI
    app = ExperimentGUI(root)
    # Start the Tkinter main event loop
    root.mainloop()

if __name__ == '__main__':
    main()