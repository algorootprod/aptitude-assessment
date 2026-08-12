"""Per-module version registry.

Each module declares its baseline `__version__` in its `__init__.py`. The registry seeds itself
from those at startup, but **the active version is mutable at runtime**: an operator (Nest or
another service) can POST to `/v1/versions/{module}` to override it. Modules consult
`version_of(name)` when deciding behavior, so flipping the active version lets us steer traffic
away from a failing module-version without redeploying. Ported from apex-assessment's
`app/core/versioning.py` — see CLAUDE.md for the convention this implements.

The registry is in-process state. For multi-replica deployments, persist overrides in DB or a
config store and reload on startup — interface stays the same.
"""

from __future__ import annotations

import threading
from importlib import import_module

MODULE_NAMES: tuple[str, ...] = (
    "user_topic_mapping",
    "user_test_mapping",
    "evaluation_report",
    "question_generation",
)


class _VersionRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, str] = {}
        self._baseline: dict[str, str] = {}
        self._seed()

    def _seed(self) -> None:
        for name in MODULE_NAMES:
            mod = import_module(f"app.modules.{name}")
            baseline = getattr(mod, "__version__", "unknown")
            self._baseline[name] = baseline
            self._active[name] = baseline

    def all(self) -> dict[str, str]:
        with self._lock:
            return dict(self._active)

    def baselines(self) -> dict[str, str]:
        with self._lock:
            return dict(self._baseline)

    def get(self, module: str) -> str:
        with self._lock:
            if module not in self._active:
                raise KeyError(module)
            return self._active[module]

    def set(self, module: str, version: str) -> str:
        with self._lock:
            if module not in self._baseline:
                raise KeyError(module)
            self._active[module] = version
            return version

    def reset(self, module: str) -> str:
        with self._lock:
            if module not in self._baseline:
                raise KeyError(module)
            self._active[module] = self._baseline[module]
            return self._active[module]


_registry = _VersionRegistry()


def module_versions() -> dict[str, str]:
    return _registry.all()


def baseline_versions() -> dict[str, str]:
    return _registry.baselines()


def version_of(module_name: str) -> str:
    try:
        return _registry.get(module_name)
    except KeyError:
        return "unknown"


def set_version(module_name: str, version: str) -> str:
    return _registry.set(module_name, version)


def reset_version(module_name: str) -> str:
    return _registry.reset(module_name)


def known_modules() -> tuple[str, ...]:
    return MODULE_NAMES
