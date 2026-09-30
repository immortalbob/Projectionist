"""The script tools/build_exe.py freezes into Projectionist.exe. It only hands over to projectionist.launch, where
the .exe's command line (the window, a database to open, --version, --self-test) is."""

import sys

from projectionist.launch import main

if __name__ == "__main__":
    sys.exit(main())
