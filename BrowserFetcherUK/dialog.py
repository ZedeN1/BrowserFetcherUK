import os

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
                                 QGroupBox, QLabel, QPushButton, QCheckBox, QComboBox, QLineEdit,
                                 QTreeWidget, QTreeWidgetItem, QHeaderView, QProgressBar,
                                 QTextBrowser, QMessageBox, QFileDialog)
from qgis.core import QgsApplication
from qgis.gui import QgsCollapsibleGroupBox, QgsFileWidget

from . import changes, connections, sources, store
from .editors import CustomConnectionsDialog, SourcesDialog
from .fetch import FetchTask, StatusTask

TITLE = "Browser Fetcher UK"
EVY_TOOLTIP = ("For Edenvale Young staff: share one copy of the connection list through the\n"
               "shared folder on the O: drive, and add the Ordnance Survey connections that go\n"
               "through the Niagara proxy (these only work on the EVY VPN).")
CHANGE_COLOURS = {"New": "#2e7d32", "Removed": "#c62828", "Updated": "#b36b00",
                  "Retired": "#b36b00", "Reinstated": "#2e7d32"}
STATUS_REFRESH_MS = 60 * 1000
# Changes list choices besides the history files.
PENDING, LAST_APPLIED = "pending", "applied"


class BrowserFetcherDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.task = None          # running FetchTask
        self.status_task = None   # running StatusTask
        self.status = None        # last finished StatusTask
        self.rows = []
        self.setWindowTitle(TITLE)
        self.resize(860, 780)

        layout = QVBoxLayout(self)

        # --- Status ---
        box = QGroupBox("Status")
        grid = QGridLayout(box)
        self.copy_label = QLabel("Reading the data folder...")
        self.sources_label = QLabel()
        self.sources_label.setStyleSheet("color: gray;")
        self.browser_label = QLabel()
        self.lock_label = QLabel()
        self.lock_label.setStyleSheet("color: #b36b00;")
        for row, label in enumerate((self.copy_label, self.sources_label, self.browser_label,
                                     self.lock_label)):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, row, 0)
        layout.addWidget(box)

        btns = QHBoxLayout()
        self.apply_btn = QPushButton("Update QGIS Browser (fast)")
        self.apply_btn.setToolTip("Replace the connections this plugin added with the latest copy of the list.\n"
                                  "Connections you added yourself are never touched.")
        self.apply_btn.clicked.connect(self.apply)
        self.fetch_btn = QPushButton()
        self.fetch_btn.setToolTip(
            "Fetch the catalogues and save a new copy of the connection list (about a minute).\n"
            "England comes from data.gov.uk, which lacks some DEFRA datasets (mostly Marine\n"
            "Management Organisation) and lists some retired services. Datasets found by the last\n"
            "full fetch are kept.")
        self.fetch_btn.clicked.connect(lambda: self.fetch(False))
        self.full_btn = QPushButton()
        self.full_btn.setToolTip(
            "As Fetch latest data, but England comes from the complete DEFRA catalogue.\n"
            "Very slow: an hour or more (each page of 200 datasets takes over a minute).\n"
            "Progress is saved after every page, so it can be cancelled and resumed later.")
        self.full_btn.clicked.connect(lambda: self.fetch(True))
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setToolTip("Stop the update. Pages fetched so far are kept; "
                                   "Resume update carries on from there.")
        self.cancel_btn.clicked.connect(self.cancel)
        btns.addWidget(self.apply_btn)
        btns.addWidget(self.fetch_btn)
        btns.addWidget(self.full_btn)
        btns.addWidget(self.cancel_btn)
        btns.addStretch()
        layout.addLayout(btns)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        # --- Changes ---
        chg = QGroupBox("Changes")
        cl = QVBoxLayout(chg)
        top = QHBoxLayout()
        top.addWidget(QLabel("Show"))
        self.view_combo = QComboBox()
        self.view_combo.currentIndexChanged.connect(self._show_changes)
        top.addWidget(self.view_combo, 1)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter...")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._filter_rows)
        top.addWidget(self.filter_edit, 1)
        cl.addLayout(top)
        self.table = QTreeWidget()
        self.table.setHeaderLabels(changes.COLUMNS)
        self.table.setRootIsDecorated(False)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        header = self.table.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.setMinimumHeight(200)
        cl.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.counts_label = QLabel()
        bottom.addWidget(self.counts_label, 1)
        self.save_csv_btn = QPushButton("Save CSV...")
        self.save_csv_btn.clicked.connect(self.save_csv)
        bottom.addWidget(self.save_csv_btn)
        cl.addLayout(bottom)
        layout.addWidget(chg, 1)

        # --- Settings ---
        sett = QgsCollapsibleGroupBox("Settings")
        sett.setCollapsed(True)
        sg = QGridLayout(sett)
        self.evy_cb = QCheckBox("EVY staff")
        self.evy_cb.setToolTip(EVY_TOOLTIP)
        sg.addWidget(self.evy_cb, 0, 0, 1, 2)
        sg.addWidget(QLabel("Shared folder"), 1, 0)
        self.shared_widget = QgsFileWidget()
        self.shared_widget.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.shared_widget.setToolTip("Holds the copy of the connection list shared by everyone "
                                      "(used when EVY staff is ticked)")
        sg.addWidget(self.shared_widget, 1, 1)
        sg.addWidget(QLabel("Local folder"), 2, 0)
        self.local_widget = QgsFileWidget()
        self.local_widget.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.local_widget.setToolTip("Holds your own copy of the connection list (used when EVY staff "
                                     "is not ticked, or the shared folder cannot be reached)")
        sg.addWidget(self.local_widget, 2, 1)
        row = QHBoxLayout()
        first_row = True
        for text, slot, tip in (
                ("Custom connections...", self.edit_custom,
                 "Your own connections (and the built-in BGS, UKCEH, OSM ones), added on every update"),
                ("Catalogue sources...", self.edit_sources,
                 "Which catalogues are searched: URLs, naming, import / export"),
                ("Reset folders", self._reset_folders, "Back to the default folders"),
                ("Remove old script connections...", self.remove_legacy,
                 "Remove connections added by the old AddConnectionsToQGIS.py script\n"
                 "(names like 'WMS UK EA: ...' that this plugin did not add)"),
                ("Remove plugin connections...", self.remove_owned,
                 "Remove every connection this plugin added"),
                ("Export XML...", self.export_xml,
                 "Save the connections as QGIS XML files, for Settings > Options > Import connections")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
            if row.count() == 2 and first_row:
                first_row = False
                row.addStretch()
                sg.addLayout(row, 3, 0, 1, 2)
                row = QHBoxLayout()
        row.addStretch()
        sg.addLayout(row, 4, 0, 1, 2)
        sg.setColumnStretch(1, 1)
        layout.addWidget(sett)

        # --- Log ---
        log_box = QgsCollapsibleGroupBox("Log")
        ll = QVBoxLayout(log_box)
        self.log_box = QTextBrowser()
        self.log_box.setMaximumHeight(140)
        ll.addWidget(self.log_box)
        layout.addWidget(log_box)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self._load_settings()
        self.evy_cb.toggled.connect(self._settings_changed)
        self.shared_widget.fileChanged.connect(self._settings_changed)
        self.local_widget.fileChanged.connect(self._settings_changed)
        self._update_buttons()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._timed_refresh)
        self.timer.start(STATUS_REFRESH_MS)

    # ------------------------------------------------------------------ settings
    def _load_settings(self):
        s = store.settings()
        self.evy_cb.setChecked(bool(s["evy"]))
        self.shared_widget.setFilePath(s["shared"])
        self.local_widget.setFilePath(s["local"])

    def _settings_changed(self, *args):
        store.save_settings(self.evy_cb.isChecked(), self.shared_widget.filePath() or store.SHARED_DEFAULT,
                            self.local_widget.filePath() or store.LOCAL_DEFAULT)
        self.refresh_status()

    def _reset_folders(self):
        self.shared_widget.setFilePath(store.SHARED_DEFAULT)
        self.local_widget.setFilePath(store.LOCAL_DEFAULT)

    # -------------------------------------------------------------------- status
    def _timed_refresh(self):
        if self.isVisible() and self.task is None:
            self.refresh_status()

    def refresh_status(self):
        if self.status_task is not None:
            return
        self.status_task = StatusTask(store.settings(), self._status_finished)
        QgsApplication.taskManager().addTask(self.status_task)

    def _status_finished(self, task, ok):
        self.status_task = None
        if not ok:
            self.copy_label.setText(f"Could not read the data folder: {task.error}")
            return
        if task.evy_detected is not None:
            # First run: EVY staff mode is on when the shared folder is there.
            self.evy_cb.blockSignals(True)
            self.evy_cb.setChecked(task.evy_detected)
            self.evy_cb.blockSignals(False)
            store.save_settings(task.evy_detected, self.shared_widget.filePath() or store.SHARED_DEFAULT,
                                self.local_widget.filePath() or store.LOCAL_DEFAULT)
            if task.evy_detected:
                self.log("Shared folder found: EVY staff mode switched on (see Settings).")
        self.status = task
        self._show_status()
        self._fill_view_combo()
        self._update_buttons()

    def _show_status(self):
        st = self.status
        where = "shared folder" if st.origin == "shared" else "local folder"
        snap = st.snapshot
        lines = []
        if st.fell_back:
            lines.append("<span style='color:#b36b00'>The shared folder cannot be reached "
                         "(are you on the EVY VPN?). Using the local folder.</span>")
        if snap:
            lines.append(f"<b>Latest copy</b> ({where}): {store.describe(snap['created'])}, "
                         f"by {snap.get('created_by', '?')}, {len(snap.get('datasets', [])):,} datasets")
        else:
            lines.append(f"No copy of the connection list in the {where} yet. "
                         f"Use <b>{self._fetch_label()}</b> to create one.")
        self.copy_label.setText("<br>".join(lines))

        parts = []
        for info in (snap or {}).get("sources", {}).values():
            text = f"{info.get('label', '?')}: {store.describe(info['fetched']) if info.get('fetched') else '?'}"
            if not info.get("complete", True):
                text += " (incomplete: last update failed)"
            elif info.get("carried"):
                text += f" ({info['carried']} datasets kept in the last fast fetch)"
            parts.append(text)
        self.sources_label.setText(" · ".join(parts))
        self.sources_label.setToolTip(st.folder)

        applied = store.read_applied()
        if applied and applied.get("applied"):
            text = (f"<b>Your QGIS Browser</b>: updated {store.describe(applied['applied'])}, "
                    f"{len(applied.get('connections', [])):,} connections")
            if applied.get("created"):
                text += f" (copy of {store.describe(applied['created']).split(' (')[0]})"
            if snap and applied.get("created") != snap.get("created"):
                text += ". <b>A newer copy is available.</b>"
            elif snap and bool(applied.get("evy")) != self.evy_cb.isChecked():
                text += ". <b>EVY staff setting changed since: update to add or remove the OS connections.</b>"
            else:
                text += ". Up to date."
        else:
            text = "<b>Your QGIS Browser</b>: not updated by this plugin yet."
        self.browser_label.setText(text)

        lock = st.lock
        if lock and not lock["stale"] and not self._own_lock(lock):
            self.lock_label.setText(
                f"Update in progress by {lock.get('user', '?')} on {lock.get('host', '?')} "
                f"since {store.hhmm(lock['started'])}, last activity {store.hhmm(lock['heartbeat'])}.")
            self.lock_label.show()
        elif lock and lock["stale"]:
            self.lock_label.setText(
                f"An update by {lock.get('user', '?')} stopped at {store.hhmm(lock['heartbeat'])} "
                f"without finishing; the next fetch carries on from where it stopped.")
            self.lock_label.show()
        else:
            self.lock_label.hide()

    def _own_lock(self, lock):
        return self.task is not None and lock.get("token") == self.task.lock.token

    def _resumable(self, full):
        """Unfinished work for this kind of fetch."""
        st = self.status
        if not st or not st.work_keys:
            return False
        keys = {s.KEY for s in sources.for_fetch(full)}
        only_other = {s.KEY for s in sources.for_fetch(not full)} - keys
        # Work of sources shared by both kinds (Scotland, Wales) resumes either; DEFRA only a full one.
        return bool(set(st.work_keys) & keys) and not (set(st.work_keys) & only_other and not full)

    def _fetch_label(self, full=False):
        shared = self.evy_cb.isChecked() and not (self.status and self.status.fell_back)
        if full:
            return "Resume full fetch" if self._resumable(True) else "Full fetch (very slow)"
        if self._resumable(False):
            return "Resume fetch"
        return "Update shared copy (fast)" if shared else "Fetch latest data (fast)"

    def _update_buttons(self):
        busy = self.task is not None
        st = self.status
        locked = bool(st and st.lock and not st.lock["stale"] and not self._own_lock(st.lock))
        self.fetch_btn.setText(self._fetch_label(False))
        self.full_btn.setText(self._fetch_label(True))
        self.fetch_btn.setEnabled(not busy and st is not None and not locked)
        self.full_btn.setEnabled(not busy and st is not None and not locked)
        self.apply_btn.setEnabled(not busy and bool(st and st.snapshot))
        self.cancel_btn.setEnabled(busy)

    # ------------------------------------------------------------------- changes
    def _pending_rows(self):
        st = self.status
        if not st or not st.snapshot:
            return []
        applied = store.read_applied() or {}
        new = connections.build(st.snapshot, self.evy_cb.isChecked())
        return changes.diff(applied.get("connections", []), new)

    def _fill_view_combo(self, select=None):
        current = select or self.view_combo.currentData()
        self.view_combo.blockSignals(True)
        self.view_combo.clear()
        self.view_combo.addItem("Pending: latest copy compared with your Browser", PENDING)
        applied = store.read_applied()
        if applied and applied.get("changes") is not None:
            self.view_combo.addItem(f"Your last Browser update ({store.describe(applied['applied'])})",
                                    LAST_APPLIED)
        if self.status and self.status.reachable:
            where = "shared" if self.status.origin == "shared" else "local"
            for path in store.change_files(self.status.folder):
                stamp = os.path.basename(path)[:11]
                try:
                    label = f"20{stamp[:2]}-{stamp[2:4]}-{stamp[4:6]} {stamp[7:9]}:{stamp[9:11]}"
                except IndexError:
                    label = stamp
                self.view_combo.addItem(f"Update of the {where} copy on {label}", path)
        index = self.view_combo.findData(current) if current else 0
        self.view_combo.setCurrentIndex(max(index, 0))
        self.view_combo.blockSignals(False)
        self._show_changes()

    def _show_changes(self, *args):
        choice = self.view_combo.currentData()
        if choice == PENDING:
            rows = self._pending_rows()
        elif choice == LAST_APPLIED:
            rows = (store.read_applied() or {}).get("changes") or []
        elif choice:
            try:
                rows = changes.read_csv(choice)
            except OSError as e:
                self.log(f"Could not read {choice}: {e}")
                rows = []
        else:
            rows = []
        self.rows = rows
        self.table.setSortingEnabled(False)
        self.table.clear()
        items = []
        for r in rows:
            item = QTreeWidgetItem([r.get(c, "") for c in changes.COLUMNS])
            colour = CHANGE_COLOURS.get(r.get("Change"))
            if colour:
                item.setForeground(0, QBrush(QColor(colour)))
            if r.get("details"):
                for col in range(len(changes.COLUMNS)):
                    item.setToolTip(col, r["details"])
            items.append(item)
        self.table.addTopLevelItems(items)
        self.table.setSortingEnabled(True)
        for col in range(len(changes.COLUMNS) - 1):
            self.table.resizeColumnToContents(col)
        self.table.setColumnWidth(3, min(self.table.columnWidth(3), 420))
        self._filter_rows()

    def _filter_rows(self, *args):
        text = self.filter_edit.text().casefold()
        shown = 0
        for i in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(i)
            hide = bool(text) and not any(text in item.text(c).casefold()
                                          for c in range(len(changes.COLUMNS)))
            item.setHidden(hide)
            shown += not hide
        summary = changes.summary(self.rows)
        if self.view_combo.currentData() == PENDING and self.status and self.status.snapshot:
            summary = "Up to date: " + summary if not self.rows else "Will change: " + summary
        if text:
            summary += f" ({shown} shown)"
        self.counts_label.setText(summary)

    def save_csv(self):
        if not self.rows:
            QMessageBox.information(self, TITLE, "There are no changes to save.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save changes", "changes.csv", "CSV files (*.csv)")
        if path:
            changes.write_csv(path, self.rows, store.now())
            self.log(f"Saved {path}")

    # --------------------------------------------------------------------- apply
    def apply(self):
        st = self.status
        if not st or not st.snapshot:
            return
        self._offer_legacy_cleanup()
        self._apply(st.snapshot, st.origin)

    def _apply(self, snapshot, origin):
        evy = self.evy_cb.isChecked()
        conns = connections.build(snapshot, evy)
        applied = store.read_applied() or {}
        rows = changes.diff(applied.get("connections", []), conns)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            added, removed, skipped = connections.apply(conns, self.log)
            self.iface.reloadConnections()
            store.write_applied(snapshot, origin, evy, conns)
            applied = store.read_applied()
            applied["changes"] = [{k: r[k] for k in changes.COLUMNS} for r in rows]
            store.write_json(store.APPLIED_PATH, applied)
            if origin == "shared":
                self._mirror_locally(snapshot)
        except Exception as e:
            self.log(f"<span style='color:#c00'>Updating the Browser failed: {e}</span>")
            raise
        finally:
            QApplication.restoreOverrideCursor()
        self.log(f"<b>QGIS Browser updated</b>: {added:,} connections "
                 f"({changes.summary(rows)}).")
        if self.status:
            self._show_status()
        self._fill_view_combo(select=LAST_APPLIED)
        self._update_buttons()

    def _mirror_locally(self, snapshot):
        """Keep a local copy of the shared list, used when the O: drive cannot be reached."""
        local = self.local_widget.filePath() or store.LOCAL_DEFAULT
        old = store.read_snapshot(local)
        if not old or old.get("created", "") < snapshot["created"]:
            try:
                store.write_json(store.snapshot_path(local), snapshot)
            except OSError as e:
                self.log(f"Could not keep a local copy in {local}: {e}")

    def _offer_legacy_cleanup(self):
        if store.legacy_checked():
            return
        found = connections.find_legacy()
        store.set_legacy_checked()
        if found:
            self._ask_remove_legacy(found)

    def _ask_remove_legacy(self, found):
        examples = "\n".join(f"  {name}" for _, name in found[:5])
        reply = QMessageBox.question(
            self, TITLE,
            f"Found {len(found):,} connections that look like they were added by the old "
            f"AddConnectionsToQGIS.py script, for example:\n\n{examples}\n\n"
            f"Remove them? The plugin adds its own up-to-date versions. Connections with "
            f"other names are not touched.")
        if reply == QMessageBox.StandardButton.Yes:
            count = connections.remove_legacy(found)
            self.iface.reloadConnections()
            self.log(f"Removed {count:,} connections added by the old script.")

    def remove_legacy(self):
        found = connections.find_legacy()
        store.set_legacy_checked()
        if not found:
            QMessageBox.information(self, TITLE, "No connections from the old script found.")
            return
        self._ask_remove_legacy(found)

    def remove_owned(self):
        reply = QMessageBox.question(self, TITLE, "Remove every connection this plugin added "
                                                  "to the QGIS Browser?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        count = connections.remove_owned()
        self.iface.reloadConnections()
        applied = store.read_applied()
        if applied:
            applied.update(connections=[], created=None, changes=None)
            store.write_json(store.APPLIED_PATH, applied)
        self.log(f"Removed {count:,} connections.")
        if self.status:
            self._show_status()
        self._fill_view_combo()

    def export_xml(self):
        st = self.status
        folder = QFileDialog.getExistingDirectory(self, "Export connections as QGIS XML")
        if not folder:
            return
        conns = connections.build(st.snapshot if st else None, self.evy_cb.isChecked())
        paths = connections.export_xml(conns, folder)
        self.log(f"Exported {len(conns):,} connections to {len(paths)} XML files in {folder}")

    # ------------------------------------------------------------------ editors
    def edit_custom(self):
        if CustomConnectionsDialog(self).exec():
            self.log("Custom connections saved: <b>Update QGIS Browser</b> to apply them.")
            self._fill_view_combo(select=PENDING)

    def edit_sources(self):
        if SourcesDialog(self).exec():
            self.log("Catalogue sources saved; they are used by the next fetch.")
            self._update_buttons()

    # --------------------------------------------------------------------- fetch
    def fetch(self, full=False):
        st = self.status
        if st is None:
            return
        folder = st.folder
        configs = sources.load_config()
        run = sources.for_fetch(full, configs)
        if not run:
            QMessageBox.warning(self, TITLE, "No catalogue sources are switched on for this fetch "
                                             "(Settings > Catalogue sources).")
            return
        where = "the shared folder, for everyone" if st.origin == "shared" else "your local folder"
        if st.fell_back:
            where += " (the shared folder cannot be reached)"
        names = ", ".join(s.LABEL for s in run)
        if full and not self._resumable(True):
            reply = QMessageBox.question(
                self, TITLE,
                f"Fetch {names} and save a new copy of the connection list to {where}?\n\n"
                f"This takes an hour or more (the DEFRA catalogue is very slow). You can keep "
                f"working in QGIS; if it is cancelled or QGIS is closed, the next full fetch "
                f"resumes where it stopped.")
            if reply != QMessageBox.StandardButton.Yes:
                return
        run_keys = {s.KEY for s in run}
        run_slots = {s.slot for s in run}
        enabled = [c for c in configs if c.get("enabled", True)]
        keep_slots = {c.get("slot", c["key"]) for c in enabled} - run_slots
        full_only = {c["key"] for c in enabled if c.get("use") == "full"} - run_keys
        try:
            lock = store.Lock(folder).acquire()
        except store.LockBusy as e:
            info = e.info
            QMessageBox.warning(self, TITLE, f"{info.get('user', 'Someone')} on {info.get('host', '?')} "
                                             f"is updating this copy right now.")
            self.refresh_status()
            return
        except OSError as e:
            QMessageBox.warning(self, TITLE, f"Cannot write to {folder}:\n{e}")
            return
        self.log(f"{'Full fetch' if full else 'Fetching'}: {names} into {folder}")
        self.task = FetchTask(folder, lock, self._fetch_finished, run, full=full,
                              keep_slots=keep_slots, regions=sources.regions(configs),
                              full_only=full_only)
        self.task.message.connect(self.log)
        self.task.progressChanged.connect(lambda p: self.progress.setValue(int(p)))
        self.progress.setValue(0)
        self._update_buttons()
        QgsApplication.taskManager().addTask(self.task)

    def _fetch_finished(self, task, ok):
        self.task = None
        self._update_buttons()
        if ok:
            self.progress.setValue(100)
            self._offer_legacy_cleanup()
            origin = self.status.origin if self.status else "local"
            self._apply(task.snapshot, origin)
        elif task.lost_lock:
            self.log(f"<span style='color:#c00'>Stopped: {task.error}.</span>")
        elif task.error:
            self.log(f"<span style='color:#c00'>Update failed: {task.error.splitlines()[0]}</span>")
            QgsApplication.messageLog().logMessage(task.error, TITLE)
        else:
            self.log("Cancelled. Pages fetched so far are kept: the next fetch of the same "
                     "kind carries on from there.")
        self.refresh_status()

    def cancel(self):
        if self.task is not None:
            self.task.cancel()

    def cleanup(self):
        self.timer.stop()
        if self.task is not None:
            self.task.cancel()
            self.task.lock.release()

    def log(self, text):
        self.log_box.append(text)
