"""Local JSON state store.

Single-writer model -- enough for cron-driven pipelines. Writes are atomic
(temp file + ``os.replace``). A corrupt file is backed up and the store starts
fresh instead of crashing.

Namespaces: ``personas``, ``persona_configs``, ``assets``, ``posts``, ``cycles``.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

NAMESPACES = ("personas", "persona_configs", "assets", "posts", "cycles")

_DEFAULT_PATH = os.environ.get("HF_SOCIAL_STATE", "hf_social_state.json")


class StateStore:
    """A namespaced JSON document with atomic writes."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path or _DEFAULT_PATH)
        self._lock = threading.Lock()

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _empty() -> dict[str, dict[str, Any]]:
        return {ns: {} for ns in NAMESPACES}

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state root is not a JSON object")
        except (json.JSONDecodeError, ValueError, OSError):
            # Corrupt or unreadable: preserve it for forensics, start clean.
            try:
                backup = self.path.with_suffix(self.path.suffix + ".corrupt")
                self.path.replace(backup)
            except OSError:
                pass
            return self._empty()
        for ns in NAMESPACES:
            data.setdefault(ns, {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent or "."), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # -- public API --------------------------------------------------------
    def put(self, namespace: str, key: str, value: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            data[namespace][key] = value
            self._save(data)
        return value

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        return self._load().get(namespace, {}).get(key)

    def all(self, namespace: str) -> dict[str, dict[str, Any]]:
        return self._load().get(namespace, {})

    def update(self, namespace: str, key: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            current = dict(data[namespace].get(key, {}))
            current.update(fields)
            data[namespace][key] = current
            self._save(data)
        return current

    def list_values(self, namespace: str) -> list[dict[str, Any]]:
        return list(self._load().get(namespace, {}).values())


# -- module-level singleton (overridable, e.g. by tests) -------------------
_store: StateStore | None = None


def get_store() -> StateStore:
    global _store
    if _store is None:
        _store = StateStore()
    return _store


def configure_store(path: str | os.PathLike[str]) -> StateStore:
    """Point the global store at a specific file (used by tests / multi-env)."""
    global _store
    _store = StateStore(path)
    return _store
