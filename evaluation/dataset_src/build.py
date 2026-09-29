"""Build the demo dataset from the prs_*.py modules.

    python evaluation/dataset_src/build.py            # all modules
    python evaluation/dataset_src/build.py prs_101_107  # one module
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import lib  # noqa: E402

modules = sys.argv[1:] or sorted(p.stem for p in Path(__file__).parent.glob("prs_*.py"))
for name in modules:
    importlib.import_module(name)
lib.write_all()
print(f"built {len(lib.REGISTRY)} PRs from {', '.join(modules)}")
