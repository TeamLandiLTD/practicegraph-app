"""Allow `python -m practicegraph` to behave exactly like the console script."""

from practicegraph.cli import entry

if __name__ == "__main__":
    entry()
