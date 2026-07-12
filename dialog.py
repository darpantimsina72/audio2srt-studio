"""Cross-platform dialog helper called by audio_to_srt.lua on Windows.

Usage:
  python dialog.py alert       <title> <message>
  python dialog.py alert_error <title> <message>
  python dialog.py pick        <title> <prompt> <item1> [item2 ...]
  python dialog.py input       <title> <prompt> <default>
  python dialog.py notify      <title> <message>

Exit code: 0 always (errors are printed to stderr).
For 'pick' and 'input', the chosen/entered value is printed to stdout.
"""

import sys

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError as exc:
    # Without tkinter no dialog can be shown — the caller only sees an empty
    # reply, so leave an unmissable hint in the dialog log (stderr).
    print("dialog.py error: tkinter is not available (%s).\n"
          "Fix: reinstall Python with tcl/tk support.\n"
          "  Mac:     brew install python-tk\n"
          "  Windows: re-run the Python installer and keep the "
          "'tcl/tk and IDLE' option checked." % exc, file=sys.stderr)
    sys.exit(2)


def create_root(title):
    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    root.after(50, root.lift)
    root.after(100, root.focus_force)
    return root


def center(win):
    win.update_idletasks()
    w = win.winfo_width()
    h = win.winfo_height()
    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()
    win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")


def alert(title, message):
    root = create_root(title)
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=message, wraplength=380, justify="left").pack(pady=(0, 16))
    ttk.Button(frm, text="OK", width=10, command=root.destroy).pack()
    center(root)
    root.mainloop()


def alert_error(title, message):
    alert("⚠ " + title, message)


def pick(title, prompt, items):
    result = {"value": None}

    root = create_root(title)
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=prompt).pack(anchor="w", pady=(0, 6))

    combo_var = tk.StringVar(value=items[0] if items else "")
    combo = ttk.Combobox(frm, textvariable=combo_var, values=items,
                         state="readonly", width=45)
    combo.pack(pady=(0, 16))

    btn_frm = ttk.Frame(frm)
    btn_frm.pack(anchor="e")

    def on_ok():
        result["value"] = combo_var.get()
        root.destroy()

    ttk.Button(btn_frm, text="Cancel", width=8, command=root.destroy).pack(side="left", padx=(0, 6))
    ttk.Button(btn_frm, text="OK",     width=8, command=on_ok).pack(side="left")

    center(root)
    root.mainloop()

    if result["value"]:
        print(result["value"], flush=True)


def input_dialog(title, prompt, default=""):
    result = {"value": None}

    root = create_root(title)
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=prompt, wraplength=380, justify="left").pack(anchor="w", pady=(0, 6))

    entry_var = tk.StringVar(value=default)
    entry = ttk.Entry(frm, textvariable=entry_var, width=40)
    entry.pack(pady=(0, 16))
    entry.focus()

    btn_frm = ttk.Frame(frm)
    btn_frm.pack(anchor="e")

    def on_ok():
        result["value"] = entry_var.get()
        root.destroy()

    root.bind("<Return>", lambda e: on_ok())

    ttk.Button(btn_frm, text="Cancel",             width=8,  command=root.destroy).pack(side="left", padx=(0, 6))
    ttk.Button(btn_frm, text="Generate Subtitles", width=18, command=on_ok).pack(side="left")

    center(root)
    root.mainloop()

    if result["value"] is not None:
        print(result["value"], flush=True)


def notify(title, message):
    # Lightweight toast — just print; a real Windows toast needs extra libs
    print(f"[{title}] {message}", file=sys.stderr)


def main():
    # The Resolve bridge parses our stdout. On Windows the default stream
    # encoding is cp1252 — printing a picked track name that contains
    # Devanagari would crash and look like the user pressed Cancel.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace",
                                line_buffering=True)
        except (AttributeError, OSError):
            pass
    try:
        if len(sys.argv) < 2:
            print(__doc__)
            sys.exit(0)

        cmd = sys.argv[1]

        if cmd == "alert" and len(sys.argv) >= 4:
            alert(sys.argv[2], sys.argv[3])

        elif cmd == "alert_error" and len(sys.argv) >= 4:
            alert_error(sys.argv[2], sys.argv[3])

        elif cmd == "pick" and len(sys.argv) >= 5:
            title  = sys.argv[2]
            prompt = sys.argv[3]
            items  = sys.argv[4:]
            pick(title, prompt, items)

        elif cmd == "input" and len(sys.argv) >= 5:
            title   = sys.argv[2]
            prompt  = sys.argv[3]
            default = sys.argv[4] if len(sys.argv) > 4 else ""
            input_dialog(title, prompt, default)

        elif cmd == "notify" and len(sys.argv) >= 4:
            notify(sys.argv[2], sys.argv[3])

        else:
            print(f"Unknown command or missing args: {sys.argv[1:]}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        print(f"dialog.py error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
