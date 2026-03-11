#experiment.py
#author: Kylen Patel
#Last updated: 2/23/2026
import os
import glob
import time
import tkinter as tk
from tkinter import ttk, messagebox
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from PIL import Image, ImageTk
import uProcess
from collections import deque
from matplotlib.animation import FuncAnimation
import threading
import sys
import subprocess
import json


#Paths (adjust if needed)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__)))
CALIB_DIR = os.path.join(BASE_DIR, "CalibrationData")
#base usq for experiment runs
BASE_USQ = os.path.join(BASE_DIR, "experiment.usq")


def find_calibration_curves(calib_root):
    #Return dict keyed by subfolder name. Each value contains slope/intercept/r2 and optional csv
    curves = {}
    if not os.path.isdir(calib_root):
        return curves
    for entry in sorted(os.listdir(calib_root)):
        full = os.path.join(calib_root, entry)
        if not os.path.isdir(full):
            continue
        vals = {'slope': None, 'intercept': None, 'r2': None, 'dir': full, 'path': None, 'curve_csv': None, 'curve_png': None}
        # look for coeffs file
        for f in os.listdir(full):
            if f.lower().startswith('calibration_coeffs') and f.lower().endswith('.txt'):
                vals.update(parse_coeffs_file(os.path.join(full, f)))
                vals['path'] = os.path.join(full, f)
            if f.lower().startswith('calibration_curve') and f.lower().endswith('.csv'):
                vals['curve_csv'] = os.path.join(full, f)
            if f.lower().endswith('.png') and ('plot' in f.lower() or 'calib' in f.lower() or 'calibration' in f.lower()):
                vals['curve_png'] = os.path.join(full, f)
        # if no coeffs, create dummy linear relation for UI demo
        if vals['slope'] is None or vals['intercept'] is None:
            # create simple dummy: flow = 10*pressure + 0
            vals['slope'] = 10.0
            vals['intercept'] = 0.0
            vals['r2'] = 1.0
        curves[entry] = vals
    return curves


def parse_coeffs_file(path):
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
        pass
    return vals


def build_script(target_pressure, dispense_time_s, trial_csv, p_tol=0.1, position=None):
    header = (
        f" ;Parameters \r\n"
        f"TargetPressure = {target_pressure} kPa\r\n"
        f"TolerancePressure = {p_tol} kPa \r\n"
        f"DispenseTime = {dispense_time_s} s\r\n"
        f"OutputFile = \"{trial_csv}\"\r\n"
    )
    # include reservoir position parameter if provided
    if position is not None:
        header += f"ReservoirPosition = {position}\r\n"
    header += "\r\n"
    if not os.path.exists(BASE_USQ):
        raise FileNotFoundError(f"Base USQ not found: {BASE_USQ}")
    with open(BASE_USQ, 'r', encoding='ascii', errors='replace') as f:
        base = f.read()
    script = header + base
    return script


def extract_flow(csv_file):
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


def compute_steady_mean_from_dispense(df, time_col, flow_col, dispense_time_s, threshold_flow=None, position=None):
    # find first time where flow exceeds threshold_flow
    if df is None or len(df) == 0:
        return None
    times = df[time_col].values
    flows = df[flow_col].values
    # try to detect start based on valve 2 state (position) if provided
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

    # if we didn't get a start from valve position, fall back to dynamic change detection
    if t_start is None:
        # sort by time to compute a baseline from the beginning of the file
        try:
            dsorted = df.sort_values(time_col)
            fvals = dsorted[flow_col].values
            tvals = dsorted[time_col].values
        except Exception:
            return None
        n = len(fvals)
        if n == 0:
            return None
        # baseline uses the first few samples (up to 5% of file or 10 samples)
        baseline_count = max(1, min(10, int(max(1, n * 0.05))))
        baseline = fvals[:baseline_count]
        med = float(np.median(baseline))
        std = float(np.std(baseline))
        # dynamic threshold: use 3*sigma or a small absolute floor
        dyn_thr = max(0.01, std * 3.0)
        idx = np.where(np.abs(fvals - med) > dyn_thr)[0]
        if idx.size == 0:
            return None
        t_start = float(tvals[int(idx[0])])
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

def downsample_by_time_nearest(df, time_col, target_hz):
    if df is None or df.empty or target_hz is None or target_hz <= 0:
        return df
    
    dfo = df.copy()
    dfo[time_col] = pd.to_numeric(dfo[time_col], errors = 'coerce')
    dfo = dfo.dropna(subset = [time_col])
    dfo = dfo.sort_values(time_col).reset_index(drop= True)

    if dfo.empty():
        return dfo
    
    target_hz = max(1,min(10,int(target_hz))) #change to max uprocess sample rate as well

    if target_hz == 10:
        return dfo.copy()
    
    dt = 1.0/float(target_hz) #time step
    tvals = dfo[time_col].to_numpy(dtype = float)

    tmin = float(tvals[0])
    tmax = float(tvals[-1])

    target_times = np.arange(tmin, tmax + 0.5 * dt, dt)
    
    chosen_idx = []
    used = set()

    for tt in target_times:
        idx = int(np.argmin(np.abs(tvals-tt)))
        if idx not in used:
            chosen_idx.append(idx)
            used.add(idx)
    
    chosen_idx = sorted(chosen_idx)
    return dfo.iloc[chosen_idx].reset_index(drop = True)

def plot_overlay(df, time_col, flow_col, pressure_col, out_png, title=None):
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

#Live plotting for flow sanity check. Live-plot flow from the current active uProcess .csv log. Call once to start; it reschedules itself with root.after()
class ExperimentGUI:
    def __init__(self, root):
        self.root = root
        root.title('Experiment Runner')
        self.curves = find_calibration_curves(CALIB_DIR)

        # Layout: left = plot, right = controls
        left = ttk.Frame(root)
        left.grid(row=0, column=0, rowspan=20, sticky='nswe', padx=6, pady=6)
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky='nswe', padx=6, pady=6)

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
        ttk.Label(right, text='Calibration Curve:').grid(row=0, column=0, sticky='w')
        self.curve_names = sorted(list(self.curves.keys()))
        self.curve_cb = ttk.Combobox(right, values=self.curve_names, state='readonly')
        self.curve_cb.grid(row=0, column=1, sticky='ew')
        self.curve_cb.bind('<<ComboboxSelected>>', self.on_curve_selected)

        # Universal settings
        ttk.Label(right, text='Threshold (%) :').grid(row=1, column=0, sticky='w')
        self.threshold_entry = tk.Entry(right)
        self.threshold_entry.insert(0, '10')
        self.threshold_entry.grid(row=1, column=1, sticky='ew')

        ttk.Label(right, text = 'Sample Rate (Hz): ').grid(row = 2, column = 0, sticky = 'w')
        self.sample_rate_entry = tk.Entry(right)
        self.sample_rate_entry.insert(0, '10')
        self.sample_rate_entry.grid(row = 2, column = 1, sticky = 'ew')

        ttk.Label(right, text='Experiment name:').grid(row=3, column=0, sticky='w')
        self.output_label = tk.Entry(right)
        self.output_label.insert(0, 'experiment_name')
        self.output_label.grid(row=3, column=1, sticky='ew')

        # Reservoir entries (3)
        self.reservoirs = []
        num_res = 3
        for i in range(num_res):
            row = 4 + i * 4
            ttk.Label(right, text=f'Reservoir{i+1} position (2-8):').grid(row=row, column=0, sticky='w')
            pos = tk.Entry(right)
            # default position clamped to valid selector range (2-8)
            pos_val = min(8, 2 + i)
            pos.insert(0, str(pos_val))
            pos.grid(row=row, column=1, sticky='ew')

            ttk.Label(right, text=f'Reservoir{i+1} desired flow rate (uL/min):').grid(row=row+1, column=0, sticky='w')
            flow = tk.Entry(right)
            flow.insert(0, '100')
            flow.grid(row=row+1, column=1, sticky='ew')

            ttk.Label(right, text=f'Reservoir{i+1} desired volume to be dispensed (uL):').grid(row=row+2, column=0, sticky='w')
            vol = tk.Entry(right)
            vol.insert(0, '100')
            vol.grid(row=row+2, column=1, sticky='ew')

            # labels to display computed pressure and time
            pressure_lbl = tk.StringVar(value='Pressure: N/A')
            time_lbl = tk.StringVar(value='DispenseTime (s): N/A')
            ttk.Label(right, textvariable=pressure_lbl).grid(row=row+3, column=0, sticky='w')
            ttk.Label(right, textvariable=time_lbl).grid(row=row+3, column=1, sticky='w')

            self.reservoirs.append({'pos': pos, 'flow': flow, 'vol': vol, 'pressure_lbl': pressure_lbl, 'time_lbl': time_lbl})

        # Result labels (populated after runs)
        self.result_vars = []
        start_row = 4 + num_res * 4
        for i in range(num_res):
            rv = tk.StringVar(value=f'Reservoir{i+1} Steady-State Avg: N/A (% difference from desired flow: N/A)')
            ttk.Label(right, textvariable=rv).grid(row=start_row + i, column=0, columnspan=2, sticky='w')
            self.result_vars.append(rv)

        # Status (above buttons)
        self.status_var = tk.StringVar(value='Ready')
        ttk.Label(right, textvariable=self.status_var).grid(row=start_row + num_res, column=0, columnspan=2, sticky='w')

        # Buttons at the bottom
        self.compute_btn = ttk.Button(right, text='Compute All', command=self.compute_all)
        self.compute_btn.grid(row=start_row + num_res + 1, column=0, pady=6)
        self.run_btn = ttk.Button(right, text='Run Experiment (3 reservoirs)', command=self.run_experiment)
        self.run_btn.grid(row=start_row + num_res + 1, column=1, pady=6)
        
        #for live plotting
        self.live_state_file = os.path.join(BASE_DIR, "live_state.json")
        self.plot_proc = None
    
    def write_live_state(self, csv_path = None, run_index = 0, done = False):
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
        try:
            if self.plot_proc is not None and self.plot_proc.poll() is None:
                return
            self.plot_proc = subprocess.Popen(
                [sys.executable, os.path.join(BASE_DIR, "live_plotter_multi.py"), self.live_state_file]
            )
        except Exception as e:
            print(f"could not start live plotter because: {e}")
    
    def on_close(self):
        try:
            self.write_live_state(csv_path = None, run_index = 0, done = True)
        except Exception:
            pass

        try:
            if self.plot_proc is not None and self.plot_proc.poll() is None:
                self.plot_proc.terminate()
        except Exception:
            pass

        self.root.destroy()
        
        

    def on_curve_selected(self, event=None):
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
                # hide canvas and show image
                try:
                    self.canvas.get_tk_widget().pack_forget()
                except Exception:
                    pass
                self.img_label.lift()
                return
            except Exception:
                pass

        # fallback: draw line and any CSV points
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

    def compute(self):
        # legacy single-reservoir compute not used
        return None

    def compute_all(self):
        name = self.curve_cb.get()
        if not name:
            messagebox.showerror('Error', 'Select a calibration curve first')
            return
        info = self.curves[name]
        slope = info.get('slope')
        intercept = info.get('intercept')
        # threshold exists but computeAll doesn't need it here
        for i, r in enumerate(self.reservoirs):
            try:
                desired_flow = float(r['flow'].get())
                vol = float(r['vol'].get())
            except Exception:
                messagebox.showerror('Error', f'Invalid inputs for reservoir {i+1}')
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
                r['pressure_lbl'].set('Pressure: N/A')
            else:
                r['pressure_lbl'].set(f'Pressure: {pressure:.3f} kPa')
            if disp_time is None:
                r['time_lbl'].set('DispenseTime(s): N/A')
            else:
                r['time_lbl'].set(f'DispenseTime(s): {disp_time:.2f}')

        self.status_var.set('Computed values for all reservoirs')
    
    def get_postprocess_sample_rate(self):
        try:
            rate = int(self.sample_rate_entry.gt())
        except Exception:
            messagebox.showwarning("Warning", "Invalid sample rate, using 10 Hz")
            return 10
        
        if rate < 1:
            messagebox.showwarning("Enter a valid sample rate greater than 1")
            return 10
        
        if rate > 10: #change this later to the max sample rate we put in uprocess
            messagebox.showwarning("Enter a valid sample rate less than the max rate")
            return 10
        
        return rate


    def run_experiment(self):
        # Run for three reservoirs sequentially, check threshold after each
        name = self.curve_cb.get()
        if not name:
            messagebox.showerror('Error', 'Select a calibration curve first')
            return
        info = self.curves[name]
        slope = info.get('slope')
        intercept = info.get('intercept')
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        # Save outputs under Automation/ExperimentData/<curve>/<experiment_name>/
        EXP_DATA_DIR = os.path.join(BASE_DIR, 'ExperimentData')
        exp_name = self.output_label.get().strip() or timestamp
        out_dir = os.path.join(EXP_DATA_DIR, name, exp_name)
        os.makedirs(out_dir, exist_ok=True)
        # live plot start
        self.write_live_state(csv_path = None, run_index = 0, done = False)
        self.start_live_plotter()


        threshold_pct = 10.0
        try:
            threshold_pct = float(self.threshold_entry.get())
        except Exception:
            messagebox.showwarning('Warning', 'Invalid threshold, using 10%')
        
        post_hz = self.get_postprocess_sample_rate()

        individual_csvs = []
        run_params = []

        # Open a single pipe and reuse it for runs
        try:
            pipe = uProcess.OpenCommandPipe()
        except Exception as e:
            messagebox.showerror("Error", f'could not open uProcess pipe: {e}')
            return

        for i, r in enumerate(self.reservoirs):
            try:
                desired_flow = float(r['flow'].get())
                vol = float(r['vol'].get())
                pos = int(r['pos'].get())
            except Exception:
                messagebox.showerror('Error', f'Invalid inputs for reservoir {i+1}')
                return

            # enforce valid reservoir positions 2-8
            if pos < 2 or pos > 8:
                messagebox.showerror('Error', f'Reservoir position for Res{i+1} must be between 2 and 8')
                return

            if slope == 0:
                pressure = 0.0
            else:
                pressure = (desired_flow - intercept) / slope

            if desired_flow <= 0:
                disp_time = 0
            else:
                disp_time = (vol / desired_flow) * 60.0

            # update labels for this reservoir before running
            if slope == 0:
                r['pressure_lbl'].set('Pressure: N/A')
            else:
                r['pressure_lbl'].set(f'Pressure: {pressure:.3f} kPa')
            r['time_lbl'].set(f'DispenseTime(s): {disp_time:.2f}')

            run_params.append({'desired_flow': desired_flow, 'disp_time': disp_time, 'pos': pos, 'out_csv': None})

            # file naming: Experiment_{calibfolder}_RES{pos}_{timestamp}.csv
            base_label = self.output_label.get().strip() or 'Experiment'
            # file inside ExperimentData/<curve> : <curve>_RES<pos>_<timestamp>.csv
            out_csv = os.path.join(out_dir, f"{name}_RES{pos}_R{i+1}_{timestamp}.csv")
            out_csv = out_csv.replace('\\', '/')

            self.write_live_state(csv_path = out_csv, run_index = i, done = False)

            # create script and run
            script_text = build_script(pressure, int(round(disp_time)), out_csv, p_tol=0.1, position=pos)
            script_text = script_text.replace('\x00', '')
            script_text = script_text.replace('\n', '\r\n')

            try:
                self.status_var.set(f'Running reservoir {i+1} at pos {pos}...')
                self.root.update()
                uProcess.SetScript(pipe, script_text)
                uProcess.Run(pipe)
                while uProcess.IsRunning(pipe):
                    time.sleep(0.5)
                    self.root.update()
            except Exception as e:
                messagebox.showerror('Error', f'Run failed for reservoir {i+1}: {e}')
                return

            # after run, record csv path for later processing
            individual_csvs.append(out_csv)
            # debug: log the saved csv path
            try:
                print(f"Run {i+1}: saved output CSV -> {out_csv}")
            except Exception:
                pass
            run_params[-1]['out_csv'] = out_csv
            # try to read basic columns for plotting later
            try:
                tcol, fcol, df = extract_flow(out_csv)
                df = downsample_by_time_nearest(df, tcol, post_hz)
                cols = (tcol, fcol)
            except Exception:
                cols = None
                df = None

            # no threshold check here; will perform after all runs

            # plot single-run overlay (flow + pressure)
            if isinstance(cols, tuple) and df is not None:
                tcol, fcol = cols
                # detect pressure column
                pressure_col = None
                for c in df.columns:
                    cl = c.lower()
                    if 'press' in cl or 'kpa' in cl:
                        pressure_col = c
                        break
                fn = out_csv.replace('.csv', '.png')
                plot_overlay(df, tcol, fcol, pressure_col, fn, title=f'Res{i+1} trace')

        try:
            uProcess.CloseCommandPipe(pipe)
        except Exception:
            pass

        self.write_live_state(csv_path = None, run_index = 0, done = True)

        # after all runs: compute steady-state means and percent differences
        exceeded = []
        for idx, p in enumerate(run_params):
            out_csv = p.get('out_csv')
            desired_flow = p.get('desired_flow')
            disp_time = p.get('disp_time')
            avg = None
            try:
                tcol, fcol, dfi = extract_flow(out_csv)
                dfi = downsample_by_time_nearest(dfi, tcol, post_hz)
                # use reservoir position to detect valve switch (V2 state) when available
                avg = compute_steady_mean_from_dispense(dfi, tcol, fcol, dispense_time_s=disp_time, position=p.get('pos'))
            except Exception:
                avg = None
            # debug: print computed avg info to console for investigation
            try:
                print(f"Compute avg for run #{idx+1}: file={out_csv}, disp_time={disp_time}, avg={avg}")
            except Exception:
                pass
            if avg is None:
                self.result_vars[idx].set(f'Res{idx+1} Avg: N/A')
            else:
                dev = abs(avg - desired_flow) / desired_flow * 100.0
                self.result_vars[idx].set(f'Res{idx+1} Avg: {avg:.2f} uL/min ({dev:.1f}%)')
                if dev > threshold_pct:
                    exceeded.append((idx+1, avg, dev))

        if exceeded:
            msgs = [f'Res{r} avg {a:.1f} uL/min deviates {d:.1f}%' for r, a, d in exceeded]
            messagebox.showwarning('Threshold Exceeded', 'Some reservoirs exceeded threshold:\n' + '\n'.join(msgs))

        # concatenate individual CSVs into one combined file
        if individual_csvs:
            combined_path = os.path.join(out_dir, f"{name}_{exp_name}_{timestamp}_combined.csv")
            # read and append using pandas (assume same columns)
            try:
                dfs = []
                # Read each individual CSV and offset its time column so the combined
                # file has a continuous time axis (e.g. 0-60 then 60-120 -> 0-120).
                dfs = []
                cumulative_offset = 0.0
                time_col_name = None
                for fpath in individual_csvs:
                    try:
                        dfi = pd.read_csv(fpath, sep=';', skiprows=[0])
                    except Exception:
                        dfi = pd.read_csv(fpath, sep=',')

                    # find a time-like column in this dataframe
                    tcols = [c for c in dfi.columns if 'time' in c.lower()]
                    if tcols:
                        tcol = tcols[0]
                        # keep original values for potential parsing
                        orig = dfi[tcol].astype(str)
                        num = pd.to_numeric(orig, errors='coerce')
                        if not num.isna().all():
                            dfi[tcol] = num
                        else:
                            # try to parse time-of-day strings e.g. HH:MM:SS[.ms]
                            parsed = pd.to_datetime(orig, errors='coerce')
                            if parsed.notna().any():
                                # convert to seconds relative to first timestamp in this file
                                base = parsed.iloc[0]
                                secs = (parsed - base).dt.total_seconds()
                                dfi[tcol] = secs
                        # ensure numeric and drop rows with NaN times
                        dfi[tcol] = pd.to_numeric(dfi[tcol], errors='coerce')
                        dfi = dfi.dropna(subset=[tcol])
                        # normalize to start at zero for this file, then add cumulative offset
                        if not dfi.empty:
                            min_t = float(dfi[tcol].min())
                            dfi[tcol] = dfi[tcol] - min_t + cumulative_offset
                            dfi["Total_Time"] = dfi[tcol]
                            cumulative_offset = float(dfi[tcol].max())
                        # remember a canonical time column name (use first file's name)
                        if time_col_name is None:
                            time_col_name = tcol
                        else:
                            # if this file uses a different time column name, rename it
                            if tcol != time_col_name:
                                dfi = dfi.rename(columns={tcol: time_col_name})
                    if time_col_name is not None and time_col_name in dfi.columns:
                        dfi = downsample_by_time_nearest(dfi, time_col_name, post_hz)
                    dfs.append(dfi)

                combined = pd.concat(dfs, ignore_index=True, sort=False)
                combined.to_csv(combined_path, index=False)
                # detect columns for overlay
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

                # create overlayed combined plot
                if time_col and flow_col:
                    try:
                        over_png = combined_path.replace('.csv', '_overlay.png')
                        plot_overlay(combined, time_col, flow_col, pressure_col, over_png, title='Combined Flow & Pressure')
                    except Exception:
                        pass
                messagebox.showinfo('Done', f'Experiment complete. Combined file: {combined_path}')
                self.status_var.set('Done')
            except Exception as e:
                messagebox.showwarning('Warning', f'Could not combine CSVs: {e}')
                self.status_var.set('Done with warnings')

def main():
    root = tk.Tk()
    # default window size expanded to show 3 reservoirs and controls
    try:
        root.geometry('1400x1100')
        root.minsize(1100, 800)
    except Exception:
        pass
    app = ExperimentGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
