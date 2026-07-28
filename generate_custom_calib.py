#!/usr/bin/env python3
"""
Generate a custom calibration using selected trials.
Specifically: use trial1 & trial2 from 12kPa, and trial2 & trial3 from 18kPa.
Outputs (saved to ReactionChamberChip folder):
 - calibration_curve_custom.csv  (Pressure_kPa, Flow_uLmin_avg, Flow_uLmin_std)
 - calibration_coeffs_custom.txt (slope, intercept, r2)
 - calibration_plot_custom.png   (scatter with errorbars + fit)

Prints per-pressure metrics and final slope/intercept/r2.
"""
import os
import sys
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), 'CalibrationData', 'ReactionChamberChip'))

# Build selection per your rule:
# - For 12 kPa: use trials 1 and 2
# - For 18 kPa: use trials 2 and 3
# - For all other pressures 2..20 (step 2): use trials 1..4
SELECTION = {}
for p in range(2, 22, 2):
    if p == 12:
        trials = [1, 2]
    elif p == 18:
        trials = [2, 3]
    else:
        trials = [1, 2, 3, 4]
    SELECTION[p] = [f'Calib_{p}kPa_trial{t}.csv' for t in trials]

OUT_CSV = os.path.join(BASE_DIR, 'calibration_curve.csv')
OUT_TXT = os.path.join(BASE_DIR, 'calibration_coeffs.txt')
OUT_PNG = os.path.join(BASE_DIR, 'calibration_plot.png')


def read_csv_guess(path):
    # handle files that include a leading 'sep=;'
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        first = f.readline()
        has_sep = first.startswith('sep=')
    if has_sep:
        df = pd.read_csv(path, sep=';', skiprows=[0])
    else:
        try:
            df = pd.read_csv(path, sep=';')
        except Exception:
            df = pd.read_csv(path)
    # strip column whitespace
    df.columns = [c.strip() for c in df.columns]
    return df


def detect_time_flow_cols(df):
    time_col = None
    flow_col = None
    # try exact matches like calibration.py
    cols = [c.strip() for c in df.columns]
    for c in cols:
        if 'Time (s)' in c and time_col is None:
            time_col = c
        if 'SF06' in c and flow_col is None:
            flow_col = c
    # fallback: looser matching
    if time_col is None or flow_col is None:
        for c in cols:
            lc = c.lower()
            if time_col is None and 'time' in lc and 'day' not in lc:
                time_col = c
            if flow_col is None and ('sf' in lc or 'flow' in lc):
                flow_col = c
    # final fallback: first numeric columns
    if time_col is None or flow_col is None:
        numeric = df.select_dtypes(include=[np.number])
        if numeric.shape[1] >= 2:
            if time_col is None:
                time_col = numeric.columns[0]
            if flow_col is None:
                flow_col = numeric.columns[1]
    return time_col, flow_col



def extract_flow(csv_file):
    # follow the exact logic from calibration.py: read with sep=';', skiprows=[0],
    # find SF06 flow column and 'Time (s)', sample between 5 and 25 s and return mean
    try:
        df = pd.read_csv(csv_file, sep=';', skiprows=[0])
    except Exception:
        # try without skiprows
        df = pd.read_csv(csv_file, sep=';')
    df.columns = df.columns.str.strip()
    try:
        flow_col = [c for c in df.columns if 'SF06' in c][0]
        time_col = [c for c in df.columns if 'Time (s)' in c][0]
    except Exception:
        # could not find expected columns
        return None
    df[time_col] = pd.to_numeric(df[time_col], errors='coerce')
    df[flow_col] = pd.to_numeric(df[flow_col], errors='coerce')
    df = df.dropna(subset=[time_col, flow_col])
    sampling = df[(df[time_col] >= 5) & (df[time_col] <= 25)]
    if sampling.empty:
        return None
    return float(np.mean(sampling[flow_col]))


def main():
    if not os.path.isdir(BASE_DIR):
        print('Calibration directory not found:', BASE_DIR)
        sys.exit(1)

    pressures = []
    averages = []
    stds = []

    print('Processing selected trials:')
    for p, files in sorted(SELECTION.items()):
        means = []
        print(f' Pressure {p} kPa:')
        for fn in files:
            path = os.path.join(BASE_DIR, fn)
            if not os.path.isfile(path):
                print('  Missing file:', fn)
                continue
            m = extract_flow(path)
            if m is None:
                print('  Could not detect time/flow columns or compute steady mean in', fn)
                continue
            means.append(m)
            print(f'  {fn}: mean flow = {m:.3f} uL/min')
        if len(means) == 0:
            print(' No usable trials for', p)
            continue
        avg = float(np.mean(means))
        std = float(np.std(means, ddof=1))
        pressures.append(float(p))
        averages.append(avg)
        stds.append(std)
        print(f'  -> Combined avg = {avg:.3f} uL/min, std = {std:.3f} (n={len(means)})')

    if len(pressures) < 2:
        print('Not enough pressure points to fit a line.')
        sys.exit(1)

    # build calibration curve dataframe
    df_curve = pd.DataFrame({'Pressure_kPa': pressures, 'Flow_uLmin_avg': averages, 'Flow_uLmin_std': stds})
    df_curve.to_csv(OUT_CSV, index=False)
    print('Saved calibration curve to', OUT_CSV)

    # fit linear model to averaged points
    x = np.array(pressures)
    y = np.array(averages)
    slope, intercept = np.polyfit(x, y, 1)
    # compute r2 using residuals on the averaged points
    yhat = slope * x + intercept
    ss_res = np.sum((y - yhat)**2)
    ss_tot = np.sum((y - np.mean(y))**2)
    r2 = 1.0 - ss_res / ss_tot if ss_tot != 0 else 1.0

    # write coeffs
    with open(OUT_TXT, 'w') as f:
        f.write(f'Slope = {slope}\n')
        f.write(f'Intercept = {intercept}\n')
        f.write(f'R2 = {r2}\n')
    print('Saved calibration coeffs to', OUT_TXT)

    # plot
    plt.figure(figsize=(8,6))
    plt.errorbar(pressures, averages, yerr=stds, fmt = 'o', capsize = 4, color = "blue", ecolor = 'gray', elinewidth = 1.5, label = "Data points +/- std")
    xs = np.linspace(min(pressures)-2, max(pressures)+2, 100)
    plt.plot(xs, slope*xs + intercept, color = 'red', linestyle = '--', linewidth = 2, label=f'Linear fit')
    plt.xlabel('Pressure (kPa)')
    plt.ylabel('Flow (uL/min)')
    plt.title('Calibration Curve')
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=150)
    print('Saved calibration plot to', OUT_PNG)
    print('\nFinal calibration:')
    for p, a, s in zip(pressures, averages, stds):
        print(f' Pressure {p} kPa: avg flow {a:.3f} uL/min, std {s:.3f}')
    print(f' Fit: slope = {slope:.6f}, intercept = {intercept:.6f}, r2 = {r2:.6f}')

if __name__ == '__main__':
    main()
