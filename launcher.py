import tkinter as tk
from tkinter import messagebox
import subprocess
import sys
from pathlib import Path

# Folder containing this launcher
BASE_DIR = Path(__file__).resolve().parent


def run_script(script_name):
    script_path = BASE_DIR / script_name

    if not script_path.exists():
        messagebox.showerror(
            "File Not Found",
            f"Could not find:\n{script_path}"
        )
        return

    # Launch using the same Python installation/environment
    subprocess.Popen(
        [sys.executable, str(script_path)],
        cwd=BASE_DIR
    )

    # Close launcher after selection
    root.destroy()


# ---------------- GUI ----------------

root = tk.Tk()
root.title("Automated Microfluidics Control")
root.geometry("420x240")
root.resizable(False, False)

title = tk.Label(
    root,
    text="LabSmith Automation",
    font=("Arial", 18, "bold")
)
title.pack(pady=(30, 10))

prompt = tk.Label(
    root,
    text="What would you like to do?",
    font=("Arial", 12)
)
prompt.pack(pady=(0, 20))

calibration_button = tk.Button(
    root,
    text="Run Calibration",
    width=25,
    height=2,
    command=lambda: run_script("calibration_gui.py")
)
calibration_button.pack(pady=5)

experiment_button = tk.Button(
    root,
    text="Run Experiment",
    width=25,
    height=2,
    command=lambda: run_script("experiment_new.py")
)
experiment_button.pack(pady=5)

root.mainloop()