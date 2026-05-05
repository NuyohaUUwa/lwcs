"""
Convenience entrypoint: `python -m game_test`.

Delegates execution to `game_test.run`'s existing main block.
"""

import runpy

if __name__ == "__main__":
    runpy.run_module("game_test.run", run_name="__main__")
