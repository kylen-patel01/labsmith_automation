#!/usr/bin/env python3
"""
Regenerate combined standardized CSV and overlay PNG from individual per-run CSVs in a folder.
Usage: run without args to operate on the example exp2 folder.
"""
import os
import sys
import glob
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

FOLDER = os.path.join(os.path.dirname(__file__), 'ExperimentData', '1m_PEEK', 'exp2')
OUT_COMBINED = os.path.join(FOLDER, 'regenerated_combined.csv')
OUT_PNG = os.path.join(FOLDER, 'regenerated_combined_overlay.png')

# find per-run CSVs
files = sorted(glob.glob(os.path.join(FOLDER, '*_RES*.csv')))
if not files:
    print('No per-run CSVs found in', FOLDER)
    sys.exit(1)

dfs = []
offset = 0.0
for fpath in files:
    print('Reading', fpath)
    # try sep=';' with skiprows
    try:
        dfi = pd.read_csv(fpath, sep=';', skiprows=[0])
    except Exception:
        try:
            dfi = pd.read_csv(fpath)
        except Exception as e:
            print('Failed to read', fpath, e)
            continue
    dfi.columns = [c.strip() for c in dfi.columns]
    # detect columns
    time_col = None
    flow_col = None
    pressure_col = None
    for c in dfi.columns:
        cl = c.lower()
        if time_col is None and 'time' in cl:
            time_col = c
        if flow_col is None and ('sf' in cl or 'flow' in cl):
            flow_col = c
        if pressure_col is None and ('press' in cl or 'pressure' in cl or 'kpa' in cl):
            pressure_col = c
    if time_col is None or flow_col is None:
        numeric = dfi.select_dtypes(include=[np.number])
        if numeric.shape[1] >= 2:
            time_col = numeric.columns[0]
            flow_col = numeric.columns[1]
    # compute elapsed seconds
    times = dfi[time_col]
    try:
        if np.issubdtype(times.dtype, np.number):
            sec = times.astype(float) - float(times.iloc[0])
        else:
            tvals = pd.to_datetime(times)
            sec = (tvals - tvals.iloc[0]).dt.total_seconds()
    except Exception:
        sec = pd.to_numeric(times, errors='coerce')
        sec = sec - sec.iloc[0]
    duration = float(sec.iloc[-1]) if len(sec) > 0 else 0.0
    proc = pd.DataFrame()
    proc['ElapsedTime_s'] = sec + offset
    proc['SourceFile'] = os.path.basename(fpath)
    proc['SourceDuration_s'] = duration
    proc['Flow'] = pd.to_numeric(dfi[flow_col], errors='coerce')
    if pressure_col and pressure_col in dfi.columns:
        proc['Pressure'] = pd.to_numeric(dfi[pressure_col], errors='coerce')
    else:
        proc['Pressure'] = np.nan
    dfs.append(proc)
    offset += duration

if not dfs:
    print('No valid dataframes created')
    sys.exit(1)

combined = pd.concat(dfs, ignore_index=True)
combined.to_csv(OUT_COMBINED, index=False)
print('Wrote', OUT_COMBINED)

# plot overlay with flow clipped 0-200
plt.figure(figsize=(10,4))
plt.plot(combined['ElapsedTime_s'], np.clip(combined['Flow'], 0, 200), label='Flow (uL/min)', color='tab:blue')
plt.ylabel('Flow (uL/min)')
plt.ylim(0,200)
if 'Pressure' in combined.columns and combined['Pressure'].notna().any():
    ax2 = plt.twinx()
    ax2.plot(combined['ElapsedTime_s'], combined['Pressure'], color='tab:red', label='Pressure (kPa)')
    ax2.set_ylabel('Pressure (kPa)')
plt.xlabel('Time (s)')
plt.title('Regenerated Combined Flow & Pressure')
plt.grid(True)
plt.tight_layout()
plt.savefig(OUT_PNG, dpi=150)
print('Wrote', OUT_PNG)
print('Done')
