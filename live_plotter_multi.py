"""live_plotter_multi.py
Live flow (and optional temperature) plotter for uProcess experiment runs.

Reads live_state.json to know which CSV is currently active, then polls it
and updates the plot in real time. Flow is always shown. If a temperature
column is detected in the CSV, a second subplot is added automatically below.

Usage:
    python live_plotter_multi.py <live_state.json>

Author: Kylen Patel
Last modified: 4/30/26
"""

import sys
import os
import time
import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

COLORS = ['C0', 'C1', 'C2', 'C3']


def read_csv(csv_path):
    '''
    Read a uProcess CSV and return time, flow, and temperature arrays.

    Looks for columns containing 'Time (s)', 'SF06' (flow), and either
    'T00389' or a column with both 'temp' and '(c)' in the name (temperature).

    Parameters:
    - csv_path: path to the uProcess output CSV file

    Returns:
    A tuple of (time_array, flow_array, temp_array) where temp_array is None
    if no temperature column is found. Returns (None, None, None) on failure.
    '''
    try:
        df = pd.read_csv(csv_path, sep=';', skiprows=[0])
        df.columns = df.columns.str.strip()

        time_cols = [c for c in df.columns if 'Time (s)' in c]
        flow_cols = [c for c in df.columns if 'SF06' in c]
        temp_cols = [c for c in df.columns if 'T00389' in c]

        if not time_cols or not flow_cols:
            return None, None, None

        tcol = time_cols[0]
        fcol = flow_cols[0]

        df[tcol] = pd.to_numeric(df[tcol], errors='coerce')
        df[fcol] = pd.to_numeric(df[fcol], errors='coerce')
        df = df.dropna(subset=[tcol, fcol])

        if df.empty:
            return None, None, None

        df = df.sort_values(tcol)
        t = df[tcol].to_numpy(dtype=float)
        f = df[fcol].to_numpy(dtype=float)

        temp = None
        if temp_cols:
            df[temp_cols[0]] = pd.to_numeric(df[temp_cols[0]], errors='coerce')
            temp = df[temp_cols[0]].to_numpy(dtype=float)

        return t, f, temp

    except Exception as e:
        print(f'read_csv error: {e}')
        return None, None, None


def main():
    '''
    Main loop: poll live_state.json and update the live plot every 0.3 seconds.

    Starts with a single flow subplot. The first time a temperature column is
    detected, the figure is rebuilt with two stacked subplots and all existing
    line data is carried over. After that the layout stays fixed for the rest
    of the run.

    Returns: None
    '''
    if len(sys.argv) < 2:
        print('Usage: python live_plotter_multi.py <live_state.json>')
        return

    state_file = sys.argv[1]

    plt.ion()

    # Start with a single flow subplot
    fig, ax_flow = plt.subplots(1, 1, figsize=(10, 4))
    ax_flow.set_xlabel('Time (s)')
    ax_flow.set_ylabel('Flow (uL/min)')
    ax_flow.set_title('Live Flow')
    ax_flow.grid(True)
    ax_temp = None          # created on demand when temp data first appears

    last_path   = None
    time_offset = 0.0
    last_x      = 0.0
    flow_lines  = {}        # csv_path → line object for flow
    temp_lines  = {}        # csv_path → line object for temperature

    while plt.fignum_exists(fig.number):

        # ── Read state file ───────────────────────────────────────────────────
        try:
            if not os.path.exists(state_file):
                plt.pause(0.3)
                continue
            with open(state_file, 'r') as f:
                state = json.load(f)
        except Exception:
            plt.pause(0.3)
            continue

        if state.get('done', False):
            plt.pause(0.3)
            continue

        csv_path  = state.get('csv_path')
        run_index = int(state.get('run_index', 0))

        if not csv_path or not os.path.exists(csv_path):
            plt.pause(0.3)
            continue

        # ── New run detected — reset time offset and create line objects ──────
        if csv_path != last_path:
            last_path   = csv_path
            time_offset = last_x
            color       = COLORS[run_index % len(COLORS)]

            if csv_path not in flow_lines:
                line, = ax_flow.plot([], [], linewidth=2, color=color,
                                     label=f'Run {run_index + 1}')
                flow_lines[csv_path] = line
                ax_flow.legend()

        # ── Read latest data ──────────────────────────────────────────────────
        t, f, temp = read_csv(csv_path)

        if t is None or len(t) == 0:
            plt.pause(0.3)
            continue

        # Shift time so runs appear end-to-end
        x      = t - t.min() + time_offset
        last_x = float(x[-1])

        # 30-second sliding window
        window = 30.0
        xmin   = max(0.0, last_x - window)
        mask   = x >= xmin

        # ── Update flow plot ──────────────────────────────────────────────────
        flow_lines[csv_path].set_data(x[mask], f[mask])
        ax_flow.set_xlim(xmin, xmin + window)
        ax_flow.relim()
        ax_flow.autoscale_view(scalex=False, scaley=True)

        # ── Add temperature subplot the first time temp data appears ──────────
        if temp is not None and ax_temp is None:
            # Rebuild the figure with two stacked subplots
            fig.clear()
            gs      = gridspec.GridSpec(2, 1, figure=fig, hspace=0.45)
            ax_flow = fig.add_subplot(gs[0])
            ax_temp = fig.add_subplot(gs[1])

            ax_flow.set_xlabel('Time (s)')
            ax_flow.set_ylabel('Flow (uL/min)')
            ax_flow.set_title('Live Flow')
            ax_flow.grid(True)

            ax_temp.set_xlabel('Time (s)')
            ax_temp.set_ylabel('Temperature (C)')
            ax_temp.set_title('Live Temperature')
            ax_temp.grid(True)

            fig.set_size_inches(10, 7)

            # Re-create all existing line objects on the new axes
            flow_lines = {}
            temp_lines = {}
            for path, idx in [(csv_path, run_index)]:
                color = COLORS[idx % len(COLORS)]
                line, = ax_flow.plot([], [], linewidth=2, color=color,
                                     label=f'Run {idx + 1}')
                flow_lines[path] = line
                line, = ax_temp.plot([], [], linewidth=2, color=color,
                                     label=f'Run {idx + 1}')
                temp_lines[path] = line

            ax_flow.legend()
            ax_temp.legend()

        # ── Update temperature plot ───────────────────────────────────────────
        if temp is not None and ax_temp is not None:
            # Create a temp line for this run if not already present
            if csv_path not in temp_lines:
                color = COLORS[run_index % len(COLORS)]
                line, = ax_temp.plot([], [], linewidth=2, color=color,
                                     label=f'Run {run_index + 1}')
                temp_lines[csv_path] = line
                ax_temp.legend()

            temp_lines[csv_path].set_data(x[mask], temp[mask])
            ax_temp.set_xlim(xmin, xmin + window)
            ax_temp.relim()
            ax_temp.autoscale_view(scalex=False, scaley=True)

        fig.canvas.draw_idle()
        plt.pause(0.3)
        time.sleep(0.1)


if __name__ == '__main__':
    main()