import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
                                 QGroupBox, QLabel, QPushButton, QCheckBox, QComboBox, QLineEdit,
                                 QSpinBox, QTreeWidget, QTreeWidgetItem, QHeaderView, QProgressBar,
                                 QTextBrowser, QMessageBox, QFileDialog, QWidget)
from qgis.core import QgsApplication
from qgis.gui import QgsCollapsibleGroupBox, QgsFileWidget

from . import changes, connections, sources, store
from .editors import CustomConnectionsDialog, SourcesDialog
from .fetch import FetchTask, StatusTask

TITLE = "Browser Fetcher UK"
EVY_TOOLTIP = ("For Edenvale Young staff: share one copy of the connection list through the\n"
               "shared folder on the O: drive, and add the Ordnance Survey connections that go\n"
               "through the Niagara proxy (these only work on the EVY VPN).\n"
               "Not EVY staff? Untick it; the choice is remembered.")
CHANGE_COLOURS = {"New": "#2e7d32", "Removed": "#c62828", "Updated": "#b36b00",
                  "Retired": "#b36b00", "Reinstated": "#2e7d32"}
WARN = "#b36b00"
# Changes list choices besides the history files.
PENDING, LAST_APPLIED = "pending", "applied"


class BrowserFetcherDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.task = None          # running FetchTask
        self.status_task = None   # running StatusTask
        self.status = None        # last finished StatusTask
        self.refresh_again = False
        self.rows = []            # rows shown in the Changes table
        self.pending = []         # what Apply would change
        self.bars = {}            # source key -> (label, progress bar) while fetching
        self.legacy_found = []    # old-script connections Apply would not replace
        self.setWindowTitle(TITLE)
        self.resize(900, 800)

        layout = QVBoxLayout(self)

        # --- Status ---
        box = QGroupBox("Status")
        grid = QGridLayout(box)
        self.copy_label = QLabel("Reading the data folder...")
        self.sources_label = QLabel()
        self.sources_label.setStyleSheet("color: gray;")
        self.browser_label = QLabel()
        self.lock_label = QLabel()
        self.lock_label.setStyleSheet(f"color: {WARN};")
        self.lock_label.hide()
        for row, label in enumerate((self.copy_label, self.sources_label, self.browser_label,
                                     self.lock_label)):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, row, 0)
        side = QVBoxLayout()
        self.evy_cb = QCheckBox("EVY staff")
        self.evy_cb.setToolTip(EVY_TOOLTIP)
        side.addWidget(self.evy_cb)
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Read the shared and local folders again")
        refresh.clicked.connect(self.refresh_status)
        side.addWidget(refresh)
        side.addStretch()
        grid.addLayout(side, 0, 1, 4, 1)
        grid.setColumnStretch(0, 1)
        layout.addWidget(box)

        # --- Fetch ---
        btns = QHBoxLayout()
        self.fetch_btn = QPushButton()
        self.fetch_btn.setToolTip(
            "Fetch the catalogues into a new copy of the connection list (under a minute).\n"
            "England comes from data.gov.uk, which lacks some DEFRA datasets (mostly Marine\n"
            "Management Organisation) and lists some retired services; datasets found by the\n"
            "last slow fetch are kept. Does not change your QGIS Browser: use Apply for that.")
        self.fetch_btn.clicked.connect(lambda: self.fetch(False))
        self.full_btn = QPushButton()
        self.full_btn.setToolTip(
            "As Fast fetch, but England comes from the complete DEFRA catalogue.\n"
            "Very slow: an hour or more. Progress is saved after every page, so it can be\n"
            "cancelled and resumed later. Does not change your QGIS Browser.")
        self.full_btn.clicked.connect(lambda: self.fetch(True))
        self.copy_btn = QPushButton("Copy slow fetch to shared folder...")
        self.copy_btn.setToolTip("Your local copy comes from a slow fetch and is newer than the shared "
                                 "copy:\nshare it with everyone instead of running the slow fetch again.")
        self.copy_btn.clicked.connect(self.copy_to_shared)
        self.copy_btn.hide()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setToolTip("Stop the fetch. Pages fetched so far are kept and the next "
                                   "fetch of the same kind carries on from there.")
        self.cancel_btn.clicked.connect(self.cancel)
        for b in (self.fetch_btn, self.full_btn, self.copy_btn, self.cancel_btn):
            btns.addWidget(b)
        btns.addStretch()
        layout.addLayout(btns)

        # One progress bar per source while fetching (they run in parallel).
        self.progress_box = QWidget()
        self.progress_grid = QGridLayout(self.progress_box)
        self.progress_grid.setContentsMargins(0, 0, 0, 0)
        self.progress_grid.setColumnStretch(1, 1)
        self.progress_box.hide()
        layout.addWidget(self.progress_box)

        # --- Changes and Apply ---
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
        legacy_row = QHBoxLayout()
        self.legacy_label = QLabel()
        self.legacy_label.setWordWrap(True)
        self.legacy_label.setStyleSheet(f"color: {WARN};")
        legacy_row.addWidget(self.legacy_label, 1)
        self.legacy_btn = QPushButton("Remove them...")
        self.legacy_btn.clicked.connect(self.remove_legacy)
        legacy_row.addWidget(self.legacy_btn)
        self.legacy_widget = QWidget()
        self.legacy_widget.setLayout(legacy_row)
        legacy_row.setContentsMargins(0, 0, 0, 0)
        self.legacy_widget.hide()
        cl.addWidget(self.legacy_widget)
        bottom = QHBoxLayout()
        self.counts_label = QLabel()
        bottom.addWidget(self.counts_label, 1)
        self.save_csv_btn = QPushButton("Save CSV...")
        self.save_csv_btn.clicked.connect(self.save_csv)
        bottom.addWidget(self.save_csv_btn)
        self.apply_btn = QPushButton("Apply to QGIS Browser")
        self.apply_btn.setToolTip("Replace the connections this plugin added with the latest copy of the "
                                  "list.\nNo downloading: reads the copy in the shared or local folder. "
                                  "Connections you added yourself are never touched.")
        self.apply_btn.clicked.connect(self.apply)
        font = self.apply_btn.font()
        font.setBold(True)
        self.apply_btn.setFont(font)
        bottom.addWidget(self.apply_btn)
        cl.addLayout(bottom)
        layout.addWidget(chg, 1)

        # --- Settings ---
        sett = QgsCollapsibleGroupBox("Settings")
        sett.setCollapsed(True)
        sg = QGridLayout(sett)
        sg.addWidget(QLabel("Shared folder"), 0, 0)
        self.shared_widget = QgsFileWidget()
        self.shared_widget.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.shared_widget.setToolTip("Holds the copy of the connection list shared by everyone "
                                      "(used when EVY staff is ticked)")
        sg.addWidget(self.shared_widget, 0, 1, 1, 3)
        sg.addWidget(QLabel("Local folder"), 1, 0)
        self.local_widget = QgsFileWidget()
        self.local_widget.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.local_widget.setToolTip("Holds your own copy of the connection list (used when EVY staff "
                                     "is not ticked, or the shared folder cannot be reached)")
        sg.addWidget(self.local_widget, 1, 1, 1, 3)
        self.reuse_spin = QSpinBox()
        self.reuse_spin.setRange(0, 24 * 30)
        self.reuse_spin.setSuffix(" h")
        self.reuse_spin.setSpecialValueText("never (always fetch)")
        self.reuse_spin.setToolTip("Fast fetch does not fetch when the copy is younger than this;\n"
                                   "0 = always fetch")
        sg.addWidget(QLabel("Fast fetch: reuse a copy younger than"), 2, 0, 1, 2)
        sg.addWidget(self.reuse_spin, 2, 2)
        self.slow_spin = QSpinBox()
        self.slow_spin.setRange(0, 3650)
        self.slow_spin.setSuffix(" days")
        self.slow_spin.setSpecialValueText("never")
        self.slow_spin.setToolTip("Fast fetch suggests a slow fetch when the last one is older than "
                                  "this;\n0 = never suggest")
        sg.addWidget(QLabel("Suggest a slow fetch when the last one is older than"), 3, 0, 1, 2)
        sg.addWidget(self.slow_spin, 3, 2)
        row = QHBoxLayout()
        first_row = True
        for text, slot, tip in (
                ("Custom connections...", self.edit_custom,
                 "Built-in and your own connections added on every update: tick which to use, import, export"),
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
            if row.count() == 3 and first_row:
                first_row = False
                row.addStretch()
                sg.addLayout(row, 4, 0, 1, 4)
                row = QHBoxLayout()
        row.addStretch()
        sg.addLayout(row, 5, 0, 1, 4)
        sg.setColumnStretch(3, 1)
        layout.addWidget(sett)

        # --- Log ---
        log_box = QgsCollapsibleGroupBox("Log")
        log_box.setCollapsed(True)
        ll = QVBoxLayout(log_box)
        self.log_box = QTextBrowser()
        self.log_box.setMaximumHeight(140)
        ll.addWidget(self.log_box)
        layout.addWidget(log_box)
        self.log_group = log_box

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
        self.reuse_spin.valueChanged.connect(lambda v: store.set_setting("reuse_hours", v))
        self.slow_spin.valueChanged.connect(lambda v: store.set_setting("slow_days", v))
        self._update_buttons()

    # ------------------------------------------------------------------ settings
    def _load_settings(self):
        s = store.settings()
        self.evy_cb.setChecked(s["evy"])
        self.shared_widget.setFilePath(s["shared"])
        self.local_widget.setFilePath(s["local"])
        self.reuse_spin.setValue(s["reuse_hours"])
        self.slow_spin.setValue(s["slow_days"])

    def _settings_changed(self, *args):
        store.save_settings(self.evy_cb.isChecked(), self.shared_widget.filePath() or store.SHARED_DEFAULT,
                            self.local_widget.filePath() or store.LOCAL_DEFAULT)
        self.refresh_status()

    def _reset_folders(self):
        self.shared_widget.setFilePath(store.SHARED_DEFAULT)
        self.local_widget.setFilePath(store.LOCAL_DEFAULT)

    # -------------------------------------------------------------------- status
    def refresh_status(self):
        """Read the folders in the background (an unreachable network drive can take a while)."""
        if self.status_task is not None:
            # Settings changed while reading: read again once this one is done.
            self.refresh_again = True
            return
        self.refresh_again = False
        self.status_task = StatusTask(store.settings(), self._status_finished)
        QgsApplication.taskManager().addTask(self.status_task)

    def _status_finished(self, task, ok):
        self.status_task = None
        if self.refresh_again:
            self.refresh_status()
            return
        if not ok:
            self.copy_label.setText(f"Could not read the data folder: {task.error}")
            return
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
            lines.append(f"<span style='color:{WARN}'>The shared folder cannot be reached (are you on "
                         f"the EVY VPN?); using the local folder. Not EVY staff? Untick EVY staff.</span>")
        if snap:
            lines.append(f"<b>Latest copy</b> ({where}, {self._kind(snap)}): "
                         f"{store.describe(snap['created'])}, by {snap.get('created_by', '?')}, "
                         f"{len(snap.get('datasets', [])):,} datasets")
            shared = st.shared_snapshot
            if st.local_newer:
                older = (f"the shared copy is from {store.describe(shared['created'])}" if shared
                         else "the shared folder has no copy yet")
                hint = ("<b>Copy slow fetch to shared folder</b> shares it" if st.can_copy_to_shared
                        else "<b>Fast fetch</b> refreshes the shared copy in under a minute")
                lines.append(f"<span style='color:{WARN}'>Your local copy is newer: {older}. "
                             f"{hint}.</span>")
        else:
            lines.append(f"No copy of the connection list in the {where} yet: use <b>Fast fetch</b> "
                         f"to create one.")
        self.copy_label.setText("<br>".join(lines))
        tip = [f"Local folder: {st.local_folder}"]
        if st.evy:
            tip.insert(0, f"Shared folder: {st.shared_folder}" + ("" if st.shared_ok else " (not reachable)"))
        self.copy_label.setToolTip("\n".join(tip))
        self.sources_label.setText(self._sources_text(snap))

        applied = store.read_applied()
        if applied and applied.get("applied"):
            text = (f"<b>Your QGIS Browser</b>: last applied {store.describe(applied['applied'])}, "
                    f"{len(applied.get('connections', [])):,} connections")
            if applied.get("created"):
                text += f" (copy of {store.describe(applied['created']).split(' (')[0]})"
        else:
            text = "<b>Your QGIS Browser</b>: nothing applied by this plugin yet."
        self.browser_label.setText(text)

        lock = st.lock
        if lock and not lock["stale"] and not self._own_lock(lock):
            self.lock_label.setText(
                f"Fetch in progress by {lock.get('user', '?')} on {lock.get('host', '?')} "
                f"since {store.hhmm(lock['started'])}, last activity {store.hhmm(lock['heartbeat'])}. "
                f"Click Refresh to check again.")
            self.lock_label.show()
        elif lock and lock["stale"]:
            self.lock_label.setText(
                f"A fetch by {lock.get('user', '?')} stopped at {store.hhmm(lock['heartbeat'])} "
                f"without finishing; the next fetch carries on from where it stopped.")
            self.lock_label.show()
        else:
            self.lock_label.hide()

    @staticmethod
    def _kind(snap):
        return "slow fetch" if snap.get("full") else "fast fetch"

    @staticmethod
    def _sources_text(snap):
        """'England 617 (data.gov.uk) + 93 only in the slow fetch of 2026-09-22 · Scotland 625 · Wales 214'.
        Dates only where they differ from the copy's own."""
        if not snap:
            return ""
        created = store.parse_time(snap["created"]).astimezone().date()
        counts = Counter(d.get("source") for d in snap.get("datasets", []))

        def date(info):
            when = info.get("fetched")
            if not when:
                return ""
            day = store.parse_time(when).astimezone()
            return "" if day.date() == created else f"{day:%Y-%m-%d}"

        parts = []
        for key, info in snap.get("sources", {}).items():
            label = info.get("label", key)
            region, _, detail = label.partition(" (")
            n = counts.get(key, 0)
            if info.get("carried"):
                when = (f"{store.parse_time(info['fetched']).astimezone():%Y-%m-%d}"
                        if info.get("fetched") else "earlier")
                parts[-1:] = [f"{parts[-1]} + {n:,} only in the slow fetch of {when}"] if parts else []
                continue
            text = f"{region} {n:,}" + (f" ({detail}" if detail else "")
            if not info.get("complete", True):
                text += f" <span style='color:{WARN}'>(last fetch failed, kept from {date(info) or 'before'})</span>"
            elif date(info):
                text += f" (fetched {date(info)})"
            parts.append(text)
        return "Datasets: " + " · ".join(parts)

    def _own_lock(self, lock):
        return self.task is not None and lock.get("token") == self.task.lock.token

    def _full_only_keys(self):
        return {s.KEY for s in sources.for_fetch(True)} - {s.KEY for s in sources.for_fetch(False)}

    def _fetch_folder(self, full):
        """Where a fetch goes: the shared folder when reachable, except that a slow fetch
        interrupted in the local folder (e.g. off the VPN) resumes there."""
        st = self.status
        if (full and st.shared_ok and set(st.local_work_keys) & self._full_only_keys()
                and not set(st.work_keys) & self._full_only_keys()):
            return st.local_folder
        return st.folder

    def _folder_snapshot(self, folder):
        st = self.status
        return st.shared_snapshot if folder == st.shared_folder and st.shared_ok else st.local_snapshot

    def _resumable(self, full):
        """Unfinished work for this kind of fetch in the folder it would use."""
        st = self.status
        if not st:
            return False
        work = set(st.local_work_keys if self._fetch_folder(full) == st.local_folder else st.work_keys)
        if full:
            return bool(work & {s.KEY for s in sources.for_fetch(True)})
        # Unfinished DEFRA pages belong to a slow fetch; a fast one only resumes its own sources.
        return bool((work - self._full_only_keys()) & {s.KEY for s in sources.for_fetch(False)})

    def _update_buttons(self):
        busy = self.task is not None
        st = self.status
        locked = bool(st and st.lock and not st.lock["stale"] and not self._own_lock(st.lock))
        self.fetch_btn.setText("Resume fast fetch" if self._resumable(False) else "Fast fetch")
        self.full_btn.setText("Resume slow fetch" if self._resumable(True) else "Slow fetch")
        self.fetch_btn.setEnabled(not busy and st is not None and not locked)
        self.full_btn.setEnabled(not busy and st is not None and not locked)
        self.copy_btn.setVisible(bool(st and st.can_copy_to_shared) and not busy)
        self.copy_btn.setEnabled(not locked)
        self.apply_btn.setEnabled(not busy and bool(st and st.snapshot) and bool(self.pending))
        self.cancel_btn.setEnabled(busy)

    # ------------------------------------------------------------------- changes
    @staticmethod
    def _browser_now(conns):
        """The real Browser for these connections (it may have been changed outside the plugin),
        labelled with the sources recorded at the last Apply."""
        labels = {(c["kind"], c["name"]): c.get("source", "")
                  for c in (store.read_applied() or {}).get("connections", [])}
        current = connections.read_browser(conns)
        for c in current:
            c["source"] = labels.get((c["kind"], c["name"]), "")
        return current

    def _pending_rows(self):
        st = self.status
        if not st or not st.snapshot:
            self._show_legacy(set())
            return []
        new = connections.build(st.snapshot, self.evy_cb.isChecked())
        self._show_legacy({(connections.KINDS[c["kind"]][0], c["name"]) for c in new})
        return changes.diff(self._browser_now(new), new)

    def _show_legacy(self, targets):
        """Note old-script connections that Apply will not replace (no longer in the list)."""
        leftover = [x for x in connections.find_legacy() if x not in targets]
        self.legacy_found = leftover
        if leftover:
            self.legacy_label.setText(
                f"{len(leftover):,} connections in your Browser look like old-script ones (e.g. "
                f"\"{leftover[0][1]}\") and are not in the current list, so Apply leaves them alone.")
        self.legacy_widget.setVisible(bool(leftover))

    def _fill_view_combo(self, select=None):
        self.pending = self._pending_rows()
        current = select or self.view_combo.currentData()
        self.view_combo.blockSignals(True)
        self.view_combo.clear()
        self.view_combo.addItem("Apply would change: latest copy compared with your Browser", PENDING)
        applied = store.read_applied()
        if applied and applied.get("changes") is not None:
            self.view_combo.addItem(f"Your last Apply ({store.describe(applied['applied'])})",
                                    LAST_APPLIED)
        if self.status and self.status.reachable:
            where = "shared" if self.status.origin == "shared" else "local"
            for path in store.change_files(self.status.snapshot_folder):
                stamp = os.path.basename(path)[:11]
                label = f"20{stamp[:2]}-{stamp[2:4]}-{stamp[4:6]} {stamp[7:9]}:{stamp[9:11]}"
                self.view_combo.addItem(f"Fetch into the {where} copy on {label}", path)
        index = self.view_combo.findData(current) if current else 0
        self.view_combo.setCurrentIndex(max(index, 0))
        self.view_combo.blockSignals(False)
        self._show_changes()
        self._update_buttons()

    def _show_changes(self, *args):
        choice = self.view_combo.currentData()
        if choice == PENDING:
            rows = self.pending
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
        if self.view_combo.currentData() == PENDING and self.status:
            if not self.status.snapshot:
                summary = "No copy of the connection list yet: use Fast fetch"
            else:
                summary = "Up to date: nothing to apply" if not self.rows else "Apply would change: " + summary
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
        """Write the latest copy to the QGIS Browser. No network: the copy is already on disk."""
        st = self.status
        if not st or not st.snapshot:
            return
        self._offer_legacy_cleanup()
        evy = self.evy_cb.isChecked()
        snapshot = st.snapshot
        conns = connections.build(snapshot, evy)
        rows = changes.diff(self._browser_now(conns), conns)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            added, removed, skipped = connections.apply(conns, self.log)
            self.iface.reloadConnections()
            store.write_applied(snapshot, st.origin, evy, conns)
            applied = store.read_applied()
            applied["changes"] = [{k: r[k] for k in changes.COLUMNS} for r in rows]
            store.write_json(store.APPLIED_PATH, applied)
            if st.origin == "shared":
                self._mirror_locally(snapshot)
        except Exception as e:
            self.log(f"<span style='color:#c00'>Applying to the Browser failed: {e}</span>")
            raise
        finally:
            QApplication.restoreOverrideCursor()
        self.log(f"<b>Applied to the QGIS Browser</b>: {added:,} connections ({changes.summary(rows)}).")
        self._show_status()
        self._fill_view_combo(select=LAST_APPLIED)

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
        # From the note under the table: only the leftovers; from Settings: every old-script one.
        found = (self.legacy_found if self.sender() is self.legacy_btn else None) or connections.find_legacy()
        store.set_legacy_checked()
        if not found:
            QMessageBox.information(self, TITLE, "No connections from the old script found.")
            return
        self._ask_remove_legacy(found)
        self._fill_view_combo()

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
        if CustomConnectionsDialog(self.evy_cb.isChecked(), self).exec():
            self.log("Custom connections saved: <b>Apply to QGIS Browser</b> to use them.")
            self._fill_view_combo(select=PENDING)

    def edit_sources(self):
        if SourcesDialog(self).exec():
            self.log("Catalogue sources saved; they are used by the next fetch.")
            self._update_buttons()

    # --------------------------------------------------------------------- fetch
    def _last_slow_fetch(self, snap):
        """When the slow-fetch-only sources (DEFRA) were last fetched into snap, or None."""
        full_only = self._full_only_keys()
        dates = [i["fetched"] for k, i in (snap or {}).get("sources", {}).items()
                 if k in full_only and i.get("fetched")]
        return max(dates) if dates else None

    def _fast_fetch_check(self, folder):
        """Before a fast fetch: None to go ahead, "slow" to run a slow fetch instead, "stop"."""
        s = store.settings()
        snap = self._folder_snapshot(folder)
        reuse = s["reuse_hours"]
        if reuse and snap and not self._resumable(False):
            age_h = store.age_minutes(snap["created"]) / 60
            if age_h < reuse:
                box = QMessageBox(QMessageBox.Icon.Information, TITLE,
                                  f"The copy is {self._age_text(age_h)} old ({store.describe(snap['created'])}); "
                                  f"copies younger than {reuse} h are reused (Settings), so there is "
                                  f"nothing to fetch.\n\nThe Changes list shows what Apply to QGIS "
                                  f"Browser would change.", parent=self)
                anyway = box.addButton("Fetch anyway", QMessageBox.ButtonRole.AcceptRole)
                box.addButton(QMessageBox.StandardButton.Ok)
                box.exec()
                if box.clickedButton() is not anyway:
                    return "stop"
        days = s["slow_days"]
        if days and self._full_only_keys():
            snoozed = s["slow_snoozed_until"]
            if snoozed and datetime.now(timezone.utc) < store.parse_time(snoozed):
                return None
            last = self._last_slow_fetch(snap)
            age_d = store.age_minutes(last) / 1440 if last else None
            if age_d is None or age_d > days:
                what = (f"The last slow fetch was {int(age_d)} days ago" if age_d is not None
                        else "No slow fetch has been done yet")
                box = QMessageBox(QMessageBox.Icon.Question, TITLE,
                                  f"{what}. A slow fetch uses the complete DEFRA catalogue: it adds "
                                  f"datasets data.gov.uk lacks (mostly Marine Management Organisation) "
                                  f"and drops retired ones, but takes an hour or more.\n\n"
                                  f"Run a slow fetch instead?", parent=self)
                slow = box.addButton("Slow fetch", QMessageBox.ButtonRole.AcceptRole)
                fast = box.addButton("Fast fetch only", QMessageBox.ButtonRole.RejectRole)
                box.addButton(QMessageBox.StandardButton.Cancel)
                box.exec()
                if box.clickedButton() is slow:
                    return "slow"
                if box.clickedButton() is fast:
                    until = datetime.now(timezone.utc) + timedelta(days=days)
                    store.set_setting("slow_snoozed_until", until.isoformat(timespec="seconds"))
                    return None
                return "stop"
        return None

    @staticmethod
    def _age_text(hours):
        if hours < 1:
            return f"{max(1, int(hours * 60))} min"
        return f"{int(hours)} h"

    def fetch(self, full=False):
        st = self.status
        if st is None:
            return
        folder = self._fetch_folder(full)
        if not full:
            check = self._fast_fetch_check(folder)
            if check == "stop":
                return
            if check == "slow":
                full = True
                folder = self._fetch_folder(True)
        configs = sources.load_config()
        run = sources.for_fetch(full, configs)
        if not run:
            QMessageBox.warning(self, TITLE, "No catalogue sources are switched on for this fetch "
                                             "(Settings > Catalogue sources).")
            return
        to_shared = folder == st.shared_folder and st.shared_ok
        where = "the shared folder, for everyone" if to_shared else "your local folder"
        if st.fell_back:
            where += " (the shared folder cannot be reached)"
        names = ", ".join(s.LABEL for s in run)
        if full and not self._resumable(True):
            reply = QMessageBox.question(
                self, TITLE,
                f"Slow fetch {names} into a new copy of the connection list in {where}?\n\n"
                f"This takes an hour or more (the DEFRA catalogue is very slow). You can keep "
                f"working in QGIS; if it is cancelled or QGIS is closed, the next slow fetch "
                f"resumes where it stopped.")
            if reply != QMessageBox.StandardButton.Yes:
                return
        run_keys = {s.KEY for s in run}
        enabled = [c for c in configs if c.get("enabled", True)]
        keep_slots = {c.get("slot", c["key"]) for c in enabled} - {s.slot for s in run}
        full_only = {c["key"] for c in enabled if c.get("use") == "full"} - run_keys
        try:
            lock = store.Lock(folder).acquire()
        except store.LockBusy as e:
            info = e.info
            QMessageBox.warning(self, TITLE, f"{info.get('user', 'Someone')} on {info.get('host', '?')} "
                                             f"is fetching into this copy right now.")
            self.refresh_status()
            return
        except OSError as e:
            QMessageBox.warning(self, TITLE, f"Cannot write to {folder}:\n{e}")
            return
        self.log(f"{'Slow' if full else 'Fast'} fetch into {folder}")
        self.task = FetchTask(folder, lock, self._fetch_finished, run, full=full,
                              keep_slots=keep_slots, regions=sources.regions(configs),
                              full_only=full_only)
        self.task.message.connect(self.log)
        self._show_progress(run)
        for sub in self.task.subtasks:
            sub.counted.connect(self._counted)
        self._update_buttons()
        QgsApplication.taskManager().addTask(self.task)

    def _show_progress(self, run):
        while self.progress_grid.count():
            w = self.progress_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.bars = {}
        for i, src in enumerate(run):
            label = QLabel(src.LABEL)
            bar = QProgressBar()
            bar.setRange(0, 0)  # busy until the record count is known
            bar.setFormat("starting")
            bar.setTextVisible(True)
            self.progress_grid.addWidget(label, i, 0)
            self.progress_grid.addWidget(bar, i, 1)
            self.bars[src.KEY] = bar
        self.progress_box.show()

    def _counted(self, key, done, total):
        bar = self.bars.get(key)
        if bar is None or not total:
            return
        bar.setRange(0, total)
        bar.setValue(done)
        bar.setFormat(f"{done:,} / {total:,}" + (" done" if done >= total else ""))

    def _fetch_finished(self, task, ok):
        self.task = None
        if ok:
            for bar in self.bars.values():
                if bar.maximum() == 0:
                    bar.setRange(0, 1)
                    bar.setValue(1)
                    bar.setFormat("done")
            self.log(f"<b>New copy saved</b> ({changes.summary(task.rows)} since the previous copy). "
                     f"Check the Changes list, then <b>Apply to QGIS Browser</b>.")
        elif task.lost_lock:
            self.log(f"<span style='color:#c00'>Stopped: {task.error}.</span>")
        elif task.error:
            self.log(f"<span style='color:#c00'>Fetch failed: {task.error.splitlines()[0]}</span>")
            QgsApplication.messageLog().logMessage(task.error, TITLE)
        else:
            self.log("Cancelled. Pages fetched so far are kept: the next fetch of the same "
                     "kind carries on from there.")
        if not ok:
            self.log_group.setCollapsed(False)
        self._update_buttons()
        self.view_combo.setCurrentIndex(0)
        self.refresh_status()

    def copy_to_shared(self):
        """Publish a newer local slow-fetch copy to the shared folder, after a preview."""
        st = self.status
        if not st or not st.can_copy_to_shared:
            return
        local = st.local_snapshot
        try:
            lock = store.Lock(st.shared_folder).acquire()
        except store.LockBusy as e:
            QMessageBox.warning(self, TITLE, f"{e.info.get('user', 'Someone')} is fetching into the shared "
                                             f"copy right now; try again when they have finished.")
            self.refresh_status()
            return
        except OSError as e:
            QMessageBox.warning(self, TITLE, f"Cannot write to {st.shared_folder}:\n{e}")
            return
        try:
            shared = store.read_snapshot(st.shared_folder) or {}
            if shared.get("created", "") >= local["created"]:
                QMessageBox.information(self, TITLE, "The shared copy has been updated in the meantime "
                                                     "and is now as new as yours; nothing to copy.")
                return
            # Regions the shared copy has but this slow fetch did not cover stay as they are.
            slot = lambda info, k: info.get("slot", k)
            local_slots = {slot(v, k) for k, v in local.get("sources", {}).items()}
            kept = [d for d in shared.get("datasets", []) if d.get("slot", d.get("source")) not in local_slots]
            snapshot = dict(local, datasets=local["datasets"] + kept,
                            sources=dict(local["sources"]), copied_by=store.who(), copied=store.now())
            for k, v in shared.get("sources", {}).items():
                if slot(v, k) not in local_slots:
                    snapshot["sources"][k] = v
            rows = changes.diff(connections.dataset_connections(shared.get("datasets", []),
                                                                shared.get("sources")),
                                connections.dataset_connections(snapshot["datasets"], snapshot["sources"]))
            extra = ""
            missing = {slot(v, k) for k, v in shared.get("sources", {}).items()} - local_slots
            if missing:
                extra = (f"\n\nYour copy does not cover {', '.join(sorted(missing))}: the shared "
                         f"copy's datasets for it are kept.")
            reply = QMessageBox.question(
                self, TITLE,
                f"Copy your slow fetch of {store.describe(local['created'])} to the shared folder, "
                f"replacing the shared copy of "
                f"{store.describe(shared['created']) if shared.get('created') else '(none)'}?\n\n"
                f"Everyone sees these changes the next time they apply: "
                f"{changes.summary(rows)}.{extra}")
            if reply != QMessageBox.StandardButton.Yes:
                return
            store.publish(st.shared_folder, snapshot, rows)
            store.append_log(st.shared_folder, f"copied the slow fetch of {local['created']} "
                                               f"from {st.local_folder}: {changes.summary(rows)}")
            self.log(f"<b>Copied your slow fetch to the shared folder</b> ({changes.summary(rows)}).")
        finally:
            lock.release()
        self.refresh_status()

    def cancel(self):
        if self.task is not None:
            self.task.cancel()

    def cleanup(self):
        if self.task is not None:
            self.task.cancel()
            self.task.lock.release()

    def log(self, text):
        self.log_box.append(text)
