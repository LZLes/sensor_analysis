"""
Undo/redo for the web app, generic over modes.

A mutating endpoint is decorated with `@tracked(mode_id, fields)` (or wraps
its change in `with undoable(session, mode_id, fields):`). The fields are
snapshotted on entry; if the endpoint raises, nothing is recorded, and if it finishes with the fields changed the old snapshot is
pushed onto the mode's undo stack (and the redo stack is cleared). Previews
and exports are not wrapped, so they never create history entries.

Snapshots copy DataFrames, lists and dicts, except entries named "df" inside
file/run records: those are the raw imported traces, which are never edited
in place, so sharing them keeps snapshots cheap.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import inspect
import pickle
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import pandas as pd

from web_app.session import SessionData

LIMIT = 50


def _copy(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, list):
        return [_copy(v) for v in value]
    if isinstance(value, dict):
        return {k: (v if k == "df" else _copy(v)) for k, v in value.items()}
    return copy.deepcopy(value)


def _digest_parts(value: Any) -> Any:
    """A picklable stand-in for `value` with raw trace frames replaced by id."""
    if isinstance(value, pd.DataFrame):
        return ("df", value.shape, value.to_csv())
    if isinstance(value, list):
        return [_digest_parts(v) for v in value]
    if isinstance(value, dict):
        return {k: (("raw", id(v)) if k == "df" else _digest_parts(v)) for k, v in value.items()}
    return value


def _digest(snap: dict) -> str:
    return hashlib.sha1(pickle.dumps(_digest_parts(snap))).hexdigest()


def snapshot(session: SessionData, fields: tuple[str, ...]) -> dict:
    return {f: _copy(getattr(session, f)) for f in fields}


def _restore(session: SessionData, snap: dict) -> None:
    for f, v in snap.items():
        setattr(session, f, _copy(v))


def _stacks(session: SessionData, mode_id: str) -> dict:
    return session.history.setdefault(mode_id, {"undo": [], "redo": []})


@contextmanager
def undoable(session: SessionData, mode_id: str, fields: tuple[str, ...]) -> Iterator[None]:
    before = snapshot(session, fields)
    yield
    if _digest(before) != _digest(snapshot(session, fields)):
        st = _stacks(session, mode_id)
        st["undo"].append(before)
        del st["undo"][:-LIMIT]
        st["redo"].clear()


def tracked(mode_id: str, fields: tuple[str, ...]) -> Callable:
    """Endpoint decorator: run the endpoint inside undoable(). Goes under
    the @router.post(...) line; the endpoint must take `session`."""
    def deco(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapper_async(*args, **kwargs):
                with undoable(kwargs["session"], mode_id, fields):
                    return await fn(*args, **kwargs)
            return wrapper_async

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with undoable(kwargs["session"], mode_id, fields):
                return fn(*args, **kwargs)
        return wrapper
    return deco


def step(session: SessionData, mode_id: str, fields: tuple[str, ...], direction: str) -> bool:
    """Undo (direction "undo") or redo one change. False if there was none."""
    st = _stacks(session, mode_id)
    src, dst = (st["undo"], st["redo"]) if direction == "undo" else (st["redo"], st["undo"])
    if not src:
        return False
    dst.append(snapshot(session, fields))
    _restore(session, src.pop())
    return True


def counts(session: SessionData, mode_id: str) -> tuple[int, int]:
    st = session.history.get(mode_id)
    return (len(st["undo"]), len(st["redo"])) if st else (0, 0)
