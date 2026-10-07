"""`python -m r7 …` — командная строка без окна, см. r7/cli.py и docs/cli.md."""
import sys

from r7.cli import main

if __name__ == "__main__":
    sys.exit(main())
