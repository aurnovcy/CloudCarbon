# CloudCarbon API package
# Set up sys.path so bare module imports work when run as `python -m uvicorn src.main:app`
import sys
import os

_src_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.abspath(os.path.join(_src_dir, "..", "..", ".."))

# Add src/ itself so bare imports like `from config import` work
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

# Add local package paths
_carbon_models_path = os.path.join(_project_root, "packages", "carbon-models", "src")
_focus_schema_path = os.path.join(_project_root, "packages", "focus-schema", "src")
_agents_src_path = os.path.join(_project_root, "apps", "agents", "src")

for _p in (_carbon_models_path, _focus_schema_path, _agents_src_path):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
