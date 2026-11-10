'''Shared "Help & Notes" popup used by the calibration and experiment GUIs.

Instead of printing instructions and notes directly on the main window, each
GUI shows a single "Help & Notes" button (also bound to F1) that opens this
read-only, scrollable window.
'''

import tkinter as tk
from tkinter import ttk


def show_help_window(root, title, sections, existing=None):
    '''Open (or raise) a read-only help window.

    Parameters:
    - root: parent Tk window
    - title: window title
    - sections: list of (heading, items, numbered) tuples; items is a list of strings
    - existing: a previously returned window, raised instead of opening a duplicate

    Returns: the Toplevel window
    '''
    if existing is not None and existing.winfo_exists():
        existing.deiconify()
        existing.lift()
        existing.focus_set()
        return existing

    win = tk.Toplevel(root)
    win.title(title)
    win.geometry("720x560")
    win.minsize(480, 320)
    win.transient(root)

    frame = ttk.Frame(win, padding=12)
    frame.pack(fill="both", expand=True)

    text = tk.Text(frame, wrap="word", relief="flat", padx=10, pady=10,
                   font=("Segoe UI", 11), bg="white", cursor="arrow")
    scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    text.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    frame.grid_rowconfigure(0, weight=1)
    frame.grid_columnconfigure(0, weight=1)

    text.tag_configure("heading", font=("Segoe UI", 13, "bold"), foreground="#1a3d6d",
                       spacing1=10, spacing3=6)
    text.tag_configure("item", lmargin1=12, lmargin2=34, spacing3=6)

    for heading, items, numbered in sections:
        text.insert("end", heading + "\n", "heading")
        for i, item in enumerate(items, start=1):
            marker = f"{i}." if numbered else "•"
            text.insert("end", f"{marker}  {item}\n", "item")

    text.configure(state="disabled")

    ttk.Button(frame, text="Close", command=win.destroy).grid(
        row=1, column=0, columnspan=2, sticky="e", pady=(10, 0)
    )
    win.bind("<Escape>", lambda e: win.destroy())
    return win
