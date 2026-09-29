"""Settings windows: the user's custom connections and the catalogue sources list."""
import copy
import json

from qgis.PyQt.QtCore import Qt, QSettings
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                                 QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
                                 QComboBox, QHeaderView, QAbstractItemView, QDialogButtonBox,
                                 QMessageBox, QFileDialog)

from . import connections, sources

TITLE = "Browser Fetcher UK"


# --------------------------------------------------------- custom connections
class CustomConnectionsDialog(QDialog):
    """Built-in and the user's own connections, each ticked or not; added on every Browser update."""

    COLS = ["Use", "Source", "Type", "Name", "URL"]

    def __init__(self, evy, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom connections")
        self.resize(900, 560)
        self.evy = evy
        self.data = connections.load_custom()
        self.data["connections"] = [copy.deepcopy(c) for c in self.data["connections"]]
        self.builtin = connections.builtin_connections(evy)

        layout = QVBoxLayout(self)
        note = QLabel("Connections added to the Browser on every update besides the catalogue datasets. "
                      "Untick any you do not want. <b>Built-in</b> ones come with the plugin"
                      + (" (<b>Built-in (EVY)</b>: Ordnance Survey through the Niagara proxy, EVY VPN only)"
                         if evy else "") +
                      "; <b>Custom</b> ones are yours: import the XML files QGIS writes when you save "
                      "connections from the Browser or Data Source Manager, or pick connections already "
                      "in your Browser.")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(self.COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setSortingEnabled(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setStretchLastSection(True)
        self.tree.itemChanged.connect(self._update_count)
        layout.addWidget(self.tree, 1)
        self.count_label = QLabel()
        layout.addWidget(self.count_label)

        row = QHBoxLayout()
        for text, slot, tip in (
                ("Tick all", lambda: self._tick_all(True), "Tick every listed connection"),
                ("Untick all", lambda: self._tick_all(False), "Untick every listed connection"),
                ("Import XML...", self.import_xml, "Add connections from QGIS connection XML files"),
                ("Add from Browser...", self.add_from_browser,
                 "Pick connections that are already in your QGIS Browser"),
                ("Export XML...", self.export_xml,
                 "Save the selected (or all ticked) connections as QGIS XML files"),
                ("Remove", self.remove_selected, "Remove the selected custom connections from the list")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._fill()

    def _items(self):
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def _fill(self):
        disabled = {tuple(k) for k in self.data["disabled"]}
        rows = [(c, ("builtin", c["kind"], c["name"]), (c["kind"], c["name"]) not in disabled)
                for c in self.builtin]
        rows += [(c, ("custom", i), c.get("enabled", True)) for i, c in enumerate(self.data["connections"])]
        self.tree.blockSignals(True)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        grey = self.palette().color(self.palette().ColorRole.PlaceholderText)
        for c, ref, ticked in rows:
            source = c.get("source", connections.CUSTOM) if ref[0] == "builtin" else connections.CUSTOM
            item = QTreeWidgetItem(["", source, connections.KIND_LABELS.get(c["kind"], c["kind"]),
                                    c["name"], c["attrs"].get("url", "")])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, ref)
            if ref[0] == "builtin":
                for col in range(1, len(self.COLS)):
                    item.setForeground(col, grey)
            self.tree.addTopLevelItem(item)
        self.tree.setSortingEnabled(True)
        self.tree.sortItems(1, Qt.SortOrder.DescendingOrder)  # Custom first, then Built-in
        self.tree.blockSignals(False)
        for col in range(len(self.COLS) - 1):
            self.tree.resizeColumnToContents(col)
        self.tree.setColumnWidth(3, min(self.tree.columnWidth(3), 380))
        self._update_count()

    def _sync(self):
        """Copy the ticks from the table back into self.data."""
        disabled = [k for k in self.data["disabled"]
                    if (k[0], k[1]) not in {(c["kind"], c["name"]) for c in self.builtin}]
        for item in self._items():
            ref = item.data(0, Qt.ItemDataRole.UserRole)
            ticked = item.checkState(0) == Qt.CheckState.Checked
            if ref[0] == "builtin":
                if not ticked:
                    disabled.append([ref[1], ref[2]])
            else:
                self.data["connections"][ref[1]]["enabled"] = ticked
        self.data["disabled"] = disabled

    def _update_count(self, *args):
        items = self._items()
        ticked = sum(i.checkState(0) == Qt.CheckState.Checked for i in items)
        own = len(self.data["connections"])
        self.count_label.setText(f"{ticked} of {len(items)} connections used ({own} custom)")

    def _tick_all(self, ticked):
        state = Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked
        self.tree.blockSignals(True)
        for item in self._items():
            if not item.isHidden():
                item.setCheckState(0, state)
        self.tree.blockSignals(False)
        self._update_count()

    def _selected(self):
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems()]

    def _add(self, new):
        self._sync()
        new = [dict(c, enabled=True) for c in new]
        self.data["connections"], replaced = connections.merge_custom(self.data["connections"], new)
        self._fill()
        return replaced

    def import_xml(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Import QGIS connections", "", "XML files (*.xml)")
        if not paths:
            return
        new, errors = [], []
        for path in paths:
            try:
                new += connections.parse_xml_file(path)
            except (OSError, ValueError) as e:
                errors.append(f"{path}: {e}")
        replaced = self._add(new)
        text = f"Imported {len(new)} connection(s)"
        if replaced:
            text += f", {replaced} of them replacing ones with the same name"
        if errors:
            text += ".\n\nSkipped:\n" + "\n".join(errors)
        QMessageBox.information(self, TITLE, text + ".")

    def add_from_browser(self):
        found = read_browser_connections()
        if not found:
            QMessageBox.information(self, TITLE, "No connections of your own found in the QGIS Browser "
                                                 "(connections added by this plugin are not listed).")
            return
        picker = _PickDialog(found, self)
        if picker.exec():
            self._add(picker.chosen())

    def _conn(self, ref):
        if ref[0] == "custom":
            return self.data["connections"][ref[1]]
        return next(c for c in self.builtin if (c["kind"], c["name"]) == (ref[1], ref[2]))

    def export_xml(self):
        refs = self._selected() or [item.data(0, Qt.ItemDataRole.UserRole) for item in self._items()
                                    if item.checkState(0) == Qt.CheckState.Checked]
        conns = [self._conn(ref) for ref in refs]
        if not conns:
            return
        folder = QFileDialog.getExistingDirectory(self, "Export connections as QGIS XML")
        if folder:
            conns = [{"kind": c["kind"], "name": c["name"], "attrs": c["attrs"]} for c in conns]
            paths = connections.export_xml(conns, folder, prefix="custom_")
            QMessageBox.information(self, TITLE, f"Saved {len(conns)} connection(s) to:\n" + "\n".join(paths)
                                    + "\n\nUsernames and passwords, if any, are included.")

    def remove_selected(self):
        refs = self._selected()
        rows = {ref[1] for ref in refs if ref[0] == "custom"}
        if refs and not rows:
            QMessageBox.information(self, TITLE, "Built-in connections cannot be removed; untick them "
                                                 "instead.")
            return
        self._sync()
        self.data["connections"] = [c for i, c in enumerate(self.data["connections"]) if i not in rows]
        self._fill()

    def accept(self):
        self._sync()
        connections.save_custom(self.data)
        super().accept()


class _PickDialog(QDialog):
    def __init__(self, conns, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add connections from the Browser")
        self.resize(760, 480)
        self.conns = conns
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Type", "Name", "URL"])
        self.tree.setRootIsDecorated(False)
        for i, c in enumerate(conns):
            item = QTreeWidgetItem([connections.KIND_LABELS.get(c["kind"], c["kind"]), c["name"],
                                    c["attrs"].get("url", "")])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            self.tree.addTopLevelItem(item)
        for col in (0, 1):
            self.tree.resizeColumnToContents(col)
        layout.addWidget(self.tree, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                out.append(self.conns[item.data(0, Qt.ItemDataRole.UserRole)])
        return out


def read_browser_connections():
    """Connections in the QGIS settings that this plugin did not add, as connection dicts."""
    reverse = {v: k for k, v in connections.KEY_REPLACEMENTS.items()}
    qs = QSettings()
    owned = connections.load_owned()
    out = []
    for kind, (group, *_) in connections.KINDS.items():
        for name in sorted(connections.existing_names(qs, group)):
            if name in owned.get(group, ()) or connections.LEGACY_RE.match(name):
                continue
            qs.beginGroup(f"{group}/{name}")
            attrs = {}
            for key in qs.childKeys():
                value = qs.value(key)
                if key == "http-header" and isinstance(value, dict):
                    for h, v in value.items():
                        attrs[connections.HEADER_PREFIX + h] = str(v)
                elif isinstance(value, (str, int, float, bool)):
                    attrs[reverse.get(key, key)] = str(value).lower() if isinstance(value, bool) else str(value)
            qs.endGroup()
            if attrs.get("url"):
                out.append({"kind": kind, "name": name, "attrs": attrs})
    return out


# ------------------------------------------------------------------- sources
COLUMNS = ["On", "Name", "Type", "Used by", "URL", "Name prefix", "Region", "Organisations (CKAN)"]
USE_LABELS = {"quick": "Fast fetch", "full": "Slow fetch", "both": "Both"}


class SourcesDialog(QDialog):
    """Edit the catalogue list: which APIs are searched, and how their datasets are named."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Catalogue sources")
        self.resize(1100, 460)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Catalogues searched for map services. <b>Used by</b>: which fetch runs the source; sources "
            "with the same region replace each other (England comes from data.gov.uk in a fast fetch "
            "and from the complete but very slow DEFRA catalogue in a slow fetch). <b>Name prefix</b> "
            "starts every connection name (\"WMS <i>UK</i> EA: ...\"). Types: <i>ckan</i> (e.g. "
            "data.gov.uk package_search), <i>defra</i> (DEFRA catalogue API), <i>geonetwork</i> "
            "(GeoNetwork 4 records/_search).")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        for text, slot in (("Add", self.add_row), ("Remove", self.remove_row),
                           ("Import...", self.import_json), ("Export...", self.export_json),
                           ("Reset to defaults", self.reset)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._fill(sources.load_config())

    def _fill(self, configs):
        self.table.setRowCount(0)
        for c in configs:
            self._append(c)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(4, min(self.table.columnWidth(4), 380))

    def _append(self, c):
        r = self.table.rowCount()
        self.table.insertRow(r)
        on = QTableWidgetItem()
        on.setFlags((on.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
        on.setCheckState(Qt.CheckState.Checked if c.get("enabled", True) else Qt.CheckState.Unchecked)
        # Fields without a column (key, slot, default_org, infer_wfs...) ride along here.
        on.setData(Qt.ItemDataRole.UserRole, c)
        self.table.setItem(r, 0, on)
        self.table.setItem(r, 1, QTableWidgetItem(c.get("label", "")))
        kind = QComboBox()
        kind.addItems(sources.TYPES)
        kind.setCurrentText(c.get("type", "ckan"))
        self.table.setCellWidget(r, 2, kind)
        use = QComboBox()
        for key, label in USE_LABELS.items():
            use.addItem(label, key)
        use.setCurrentIndex(max(0, use.findData(c.get("use", "both"))))
        self.table.setCellWidget(r, 3, use)
        self.table.setItem(r, 4, QTableWidgetItem(c.get("url", "")))
        self.table.setItem(r, 5, QTableWidgetItem(c.get("prefix", "")))
        self.table.setItem(r, 6, QTableWidgetItem(c.get("region", "")))
        self.table.setItem(r, 7, QTableWidgetItem(", ".join(c.get("organisations") or [])))

    def _configs(self):
        out = []
        for r in range(self.table.rowCount()):
            c = dict(self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) or {})
            text = lambda col: (self.table.item(r, col).text().strip() if self.table.item(r, col) else "")
            c.update(enabled=self.table.item(r, 0).checkState() == Qt.CheckState.Checked,
                     label=text(1), type=self.table.cellWidget(r, 2).currentText(),
                     use=self.table.cellWidget(r, 3).currentData(), url=text(4), prefix=text(5),
                     region=text(6) or "Other")
            orgs = [o.strip() for o in text(7).split(",") if o.strip()]
            if orgs or c["type"] == "ckan":
                c["organisations"] = orgs
            if not c.get("key"):
                c["key"] = c["label"] or f"source{r + 1}"
            # Sources in one region replace each other (see FetchTask keep_slots).
            c["slot"] = c["region"].casefold() if c["region"] != "Other" else c["key"]
            out.append(c)
        return sources.validate(out)

    def add_row(self):
        self._append({"enabled": True, "label": "New source", "type": "geonetwork", "use": "both",
                      "url": "https://", "prefix": "", "region": "Other"})

    def remove_row(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def import_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import sources", "", "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                self._fill(sources.validate(json.load(f)))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, TITLE, f"Could not import {path}:\n{e}")

    def export_json(self):
        try:
            configs = self._configs()
        except ValueError as e:
            QMessageBox.warning(self, TITLE, str(e))
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export sources", "browser_fetcher_sources.json",
                                              "JSON files (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(configs, f, indent=1)

    def reset(self):
        self._fill(copy.deepcopy(sources.DEFAULTS))

    def accept(self):
        try:
            sources.save_config(self._configs())
        except ValueError as e:
            QMessageBox.warning(self, TITLE, str(e))
            return
        super().accept()
