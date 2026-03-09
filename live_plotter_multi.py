import sys
import os
import time
import json
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

COLORS = ['C0', 'C1', 'C2']


def read_flow(csv_path):
    try:
        df = pd.read_csv(csv_path, sep=';', skiprows=[0])
        df.columns = df.columns.str.strip()

        flow_cols = [c for c in df.columns if 'SF06' in c]
        time_cols = [c for c in df.columns if 'Time (s)' in c]

        if not flow_cols or not time_cols:
            return None, None

        tcol = time_cols[0]
        fcol = flow_cols[0]

        df[tcol] = pd.to_numeric(df[tcol], errors='coerce')
        df[fcol] = pd.to_numeric(df[fcol], errors='coerce')
        df = df.dropna(subset=[tcol, fcol])

        if df.empty:
            return None, None

        t = df[tcol].to_numpy(dtype=float)
        f = df[fcol].to_numpy(dtype=float)

        order = np.argsort(t)
        return t[order], f[order]

    except Exception as e:
        print(f"read_flow error: {e}")
        return None, None


def main():
    if len(sys.argv) < 2:
        print("Usage: python live_plotter_multi.py <live_state.json>")
        return

    state_file = sys.argv[1]

    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Flow (uL/min)")
    ax.set_title("Live Flow")
    ax.grid(True)

    last_path = None
    time_offset = 0.0
    last_x = 0.0
    lines = {}

    while plt.fignum_exists(fig.number):
        try:
            if not os.path.exists(state_file):
                plt.pause(0.3)
                continue

            with open(state_file, "r") as f:
                state = json.load(f)
        except Exception:
            plt.pause(0.3)
            continue

        if state.get("done", False):
            plt.pause(0.3)
            continue

        csv_path = state.get("csv_path")
        run_index = int(state.get("run_index", 0))

        if not csv_path or not os.path.exists(csv_path):
            plt.pause(0.3)
            continue

        if csv_path != last_path:
            last_path = csv_path
            time_offset = last_x

            if csv_path not in lines:
                color = COLORS[run_index % len(COLORS)]
                line, = ax.plot([], [], linewidth=2, color=color, label=f"Run {run_index + 1}")
                lines[csv_path] = line
                ax.legend()

        t, f = read_flow(csv_path)
        if t is not None and len(t) > 0:
            x = t - t.min() + time_offset
            last_x = float(x[-1])
            #sliding window of 30s
            window = 30.0
            xmin = max(0.0, last_x - window)
            mask = x >= xmin



            lines[csv_path].set_data(x[mask], f[mask])
            ax.set_xlim(xmin, xmin+window)
            ax.relim()
            ax.autoscale_view(scalex = False, scaley = True)
            fig.canvas.draw_idle()

        plt.pause(0.3)
        time.sleep(0.1)


if __name__ == "__main__":
    main()