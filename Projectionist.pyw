"""Double-click this file to open Projectionist."""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from projectionist.gui import main

    sys.exit(main())
except Exception as exc:
    from projectionist import needs

    if needs.is_tkinter_missing(exc):
        # (a Linux Python without its tkinter package: no box can be shown - say what to install, in the terminal)
        print(needs.tkinter_missing(), file=sys.stderr)
        sys.exit(1)
    # .pyw files have no console, so show startup failures in a dialog instead of vanishing.
    details = traceback.format_exc()
    try:
        import tkinter
        from tkinter import messagebox

        try:
            from projectionist import appicon     # (the box in the taskbar as Projectionist, if that much works)
        except Exception:
            appicon = None
        if appicon is not None:
            appicon.set_app_id()
        root = tkinter.Tk()
        root.withdraw()
        if appicon is not None:
            appicon.apply(root)
        messagebox.showerror("Projectionist", f"The app couldn't start:\n\n{details[-1500:]}")
    except Exception:
        pass
    raise
