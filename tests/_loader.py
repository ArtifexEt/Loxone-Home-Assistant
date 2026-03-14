"""Helpers for loading selected integration modules without Home Assistant."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS_ROOT = ROOT / "custom_components"
INTEGRATION_ROOT = COMPONENTS_ROOT / "loxone_home_assistant"


def load_integration_module(module_name: str):
    """Load one module from the integration without importing its HA entrypoint."""
    _ensure_package("custom_components", COMPONENTS_ROOT)
    _ensure_package("custom_components.loxone_home_assistant", INTEGRATION_ROOT)

    relative_name = module_name.rsplit(".", 1)[-1]
    module_path = INTEGRATION_ROOT / f"{relative_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot create module spec for {module_name}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _ensure_package(name: str, path: Path) -> None:
    if name in sys.modules:
        return
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package
