"""
QUndoCommand subclasses wrapping AppState mutations.

Generic (not one subclass per action) on purpose: nearly every undoable
action in this app is "replace field X's value with Y" — a calibration
table edit, an Apply Channel Configuration click, an Apply auto-detected
window click, a row add/remove. One SetFieldCommand covers all of them via
old/new value capture; a table-specific alias (TableEditCommand) exists
only for a clearer undo-menu label ("Edit calibration table" vs "Set
amp_files").
"""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QUndoCommand

from macos_app.ui.app_state import AppState


class SetFieldCommand(QUndoCommand):
    """Undoable set of one SessionData field, with a signal re-emitted after
    both do() and undo() so the UI stays in sync either direction."""

    def __init__(
        self,
        app_state: AppState,
        field_name: str,
        new_value: Any,
        text: str | None = None,
    ) -> None:
        super().__init__(text or f"Set {field_name}")
        self._app_state = app_state
        self._field_name = field_name
        self._old_value = app_state.get_field(field_name)
        self._new_value = new_value

    def redo(self) -> None:
        self._app_state.set_field_silent(self._field_name, self._new_value)
        self._emit()

    def undo(self) -> None:
        self._app_state.set_field_silent(self._field_name, self._old_value)
        self._emit()

    def _emit(self) -> None:
        if self._field_name == "mode":
            self._app_state.mode_changed.emit(self._app_state.data.mode)
        else:
            self._app_state.setting_changed.emit(self._field_name)


class FilesListCommand(SetFieldCommand):
    """SetFieldCommand for replacing an entire per-file-list field
    (amp_files/solid_files/cv_runs) wholesale — Apply Channel
    Configuration, Load sample data, or a session Import — firing
    files_changed on both redo and undo instead of setting_changed, since
    every mode view's file-list UI (dataset selector, channel list,
    comparison panel) listens for files_changed specifically. Plain
    SetFieldCommand's setting_changed was a silent no-op for these fields:
    the underlying AppState.data reverted correctly on undo, but nothing
    told the file-list widgets to redraw.

    Deliberately NOT used for per-file cpdf edits — TableEditCommand's
    narrower notify_cpdf_changed is correct there; firing files_changed on
    every table cell edit would reset the active-file selection back to 0
    each time."""

    def redo(self) -> None:
        self._app_state.set_field_silent(self._field_name, self._new_value)
        self._app_state.notify_files_changed(self._field_name)

    def undo(self) -> None:
        self._app_state.set_field_silent(self._field_name, self._old_value)
        self._app_state.notify_files_changed(self._field_name)


class TableEditCommand(SetFieldCommand):
    """SetFieldCommand for a per-file calibration/standards/sample table edit
    — also fires cpdf_changed / files_changed so table widgets refresh."""

    def __init__(
        self,
        app_state: AppState,
        files_key: str,
        file_index: int | None,
        new_value: Any,
        text: str = "Edit table",
    ) -> None:
        self._files_key = files_key
        self._file_index = file_index
        if file_index is None:
            # A top-level table field (e.g. assay_std_df), not a per-file cpdf.
            super().__init__(app_state, files_key, new_value, text)
        else:
            # Per-file cpdf lives inside the files list entry, not a bare
            # SessionData field — swap the whole files list so undo/redo
            # is a single atomic list replacement.
            files = list(app_state.files_for(files_key))
            old_entry = files[file_index]
            new_entry = {**old_entry, "cpdf": new_value}
            new_files = list(files)
            new_files[file_index] = new_entry
            super().__init__(app_state, files_key, new_files, text)

    def redo(self) -> None:
        super().redo()
        if self._file_index is None:
            self._app_state.setting_changed.emit(self._files_key)
        else:
            self._app_state.notify_cpdf_changed(self._files_key, self._file_index)

    def undo(self) -> None:
        super().undo()
        if self._file_index is None:
            self._app_state.setting_changed.emit(self._files_key)
        else:
            self._app_state.notify_cpdf_changed(self._files_key, self._file_index)
