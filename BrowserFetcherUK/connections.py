"""Browser connections: build them from a snapshot and write them to the QGIS settings.

A connection is {"kind": "wms" | "wfs" | "wcs" | "xyztiles" | "vectortile",
"name": str, "attrs": {xml attribute: value}}, the same shape as one line of a
QGIS connections XML export, so static.py lines, catalogue datasets and XML
export all share it.

Only connections this plugin wrote are ever removed: their names are kept per
settings group in owned.json in the QGIS profile folder.

Besides the catalogue datasets, every update adds the built-in connections
(static.py; the EVY ones only for EVY staff) and the user's own custom
connections (custom_connections.json in the profile, imported from QGIS
connection XML exports).
"""
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from xml.sax.saxutils import quoteattr

from qgis.PyQt.QtCore import QSettings

from . import static, store

# kind -> (settings group, XML root tag and version, XML export file)
KINDS = {
    "wms": ("connections/ows/items/wms/connections/items", "qgsWMSConnections", "1.0", "WMS_WMTS"),
    "wfs": ("connections/ows/items/wfs/connections/items", "qgsWFSConnections", "1.1", "WFS_OGC"),
    "wcs": ("connections/ows/items/wcs/connections/items", "qgsWCSConnections", "1.0", "WCS"),
    "xyztiles": ("connections/xyz/items", "qgsXYZTilesConnections", "1.0", "XYZ"),
    "vectortile": ("connections/vector-tile/items", "qgsVectorTileConnections", "1.0", "VT"),
    "arcgisfeatureserver": ("connections/arcgisfeatureserver/items",
                            "qgsARCGISFEATURESERVERConnections", "1.0", "ArcGIS"),
}
# Written by Export XML even when empty (the files filter_v07.py produced).
STANDARD_KINDS = ("wms", "wfs", "wcs", "xyztiles", "vectortile")
KIND_LABELS = {"wms": "WMS/WMTS", "wfs": "WFS/OGC API", "wcs": "WCS", "xyztiles": "XYZ",
               "vectortile": "Vector tiles", "arcgisfeatureserver": "ArcGIS REST"}

# XML attribute names -> settings keys (from AddConnectionsToQGIS.py).
KEY_REPLACEMENTS = {
    "dpiMode": "dpi-mode",
    "ignoreAxisOrientation": "ignore-axis-orientation",
    "ignoreGetFeatureInfoURI": "ignore-get-feature-info-uri",
    "ignoreGetMapURI": "ignore-get-map-uri",
    "invertAxisOrientation": "invert-axis-orientation",
    "maxnumfeatures": "max-num-features",
    "pagesize": "page-size",
    "pagingenabled": "paging-enabled",
    "serviceType": "service-type",
    "smoothPixmapTransform": "smooth-pixmap-transform",
    "tilePixelRatio": "tile-pixel-ratio",
}
HEADER_PREFIX = "http-header:"

_WMS = {"invertAxisOrientation": "0", "ignoreGetFeatureInfoURI": "0", "smoothPixmapTransform": "0",
        "dpiMode": "7", "ignoreAxisOrientation": "0", "ignoreGetMapURI": "0"}
_WFS = {"version": "auto", "invertAxisOrientation": "0", "ignoreAxisOrientation": "0",
        "pagesize": "300000", "pagingenabled": "enabled"}
# Catalogue service type -> (kind, attributes), from filter_v07 generate_master_xml.
TEMPLATES = {
    "WMS": ("wms", _WMS),
    "WMTS": ("wms", _WMS),
    "WFS": ("wfs", _WFS),
    "OGC": ("wfs", _WFS),
    "WCS": ("wcs", {}),
    "XYZ": ("xyztiles", {"zmax": "19", "zmin": "0", "tilePixelRatio": "0"}),
    "VT": ("vectortile", {"zmax": "15", "zmin": "7"}),
}

# Names written by the old AddConnectionsToQGIS.py / filter_v07.py, e.g.
# "WMS UK EA: ...", "WFS Scot SG: ...", "XYZ OS: ...", "WFS WMS: Boreholes".
LEGACY_RE = re.compile(r"^(WMS|WMTS|WFS|OGC|WCS|XYZ|VT) (UK|Scot|Wales|CEH|BGS|OS|OSM|MZ|WMS)\b[^:]*:")

OWNED_PATH = os.path.join(store.PROFILE_DIR, "owned.json")
CUSTOM_PATH = os.path.join(store.PROFILE_DIR, "custom_connections.json")


def clean_name(name):
    # "/" and "\" would split the settings path into nested groups.
    return re.sub(r"[/\\]", "-", " ".join(name.split()))


def _elements(root):
    out = []
    for el in root:
        if el.tag not in KINDS:
            continue
        attrs = {(HEADER_PREFIX + k[len("http_header_"):] if k.startswith("http_header_") else k): v
                 for k, v in el.attrib.items()}
        out.append({"kind": el.tag, "name": attrs.pop("name", ""), "attrs": attrs})
    return out


def _clean_xml(text):
    text = text.replace(HEADER_PREFIX, "http_header_")
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text)
    # Bare & in URLs, as pasted from browsers.
    return re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+);)", "&amp;", text)


def parse_xml_lines(text):
    """Connections from QGIS connection XML lines (without the root element)."""
    return _elements(ET.fromstring(f"<root>{_clean_xml(text)}</root>"))


def parse_xml_file(path):
    """Connections in a QGIS connections XML export (Settings > Options or the Browser's
    Save Connections). Raises ValueError when the file holds none this plugin handles."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        root = ET.fromstring(_clean_xml(text))
    except ET.ParseError as e:
        raise ValueError(f"not a valid XML file ({e})")
    conns = _elements(root)
    if not conns:
        raise ValueError(f"no WMS, WFS, WCS, XYZ, vector tile or ArcGIS connections in it "
                         f"(root element <{root.tag}>)")
    return conns


BUILTIN, BUILTIN_EVY, CUSTOM = "Built-in", "Built-in (EVY)", "Custom"


def builtin_connections(evy):
    """Built-in connections tagged with their source; the EVY ones only for EVY staff."""
    conns = _tagged(parse_xml_lines(static.PUBLIC), BUILTIN)
    if evy:
        conns += _tagged(parse_xml_lines(static.EVY), BUILTIN_EVY)
    return conns


def conn_key(c):
    return [c["kind"], c["name"]]


def load_custom():
    """{"disabled": [[kind, name]] of built-in connections switched off,
    "connections": [...] the user's own, each with "enabled"} from the profile."""
    data = store.read_json(CUSTOM_PATH) or {}
    disabled = [list(k) for k in data.get("disabled", [])]
    if data.get("include_builtin") is False:  # v0.01 had one switch for all built-in ones
        disabled += [conn_key(c) for c in parse_xml_lines(static.PUBLIC)]
    conns = [dict(c, enabled=c.get("enabled", True)) for c in data.get("connections", [])
             if c.get("kind") in KINDS]
    return {"disabled": disabled, "connections": conns}


def save_custom(data):
    store.write_json(CUSTOM_PATH, data, indent=1)


def merge_custom(existing, new):
    """existing + new; a new connection replaces one of the same kind and name. Returns (list, replaced)."""
    index = {(c["kind"], c["name"]): i for i, c in enumerate(existing)}
    out, replaced = list(existing), 0
    for c in new:
        key = (c["kind"], c["name"])
        if key in index:
            out[index[key]] = c
            replaced += 1
        else:
            index[key] = len(out)
            out.append(c)
    return out, replaced


def _tagged(conns, source):
    for c in conns:
        c["source"] = source
    return conns


def static_connections(evy, custom=None):
    """Ticked built-in connections (EVY ones for EVY staff only), then the user's own ticked ones.

    "source" on each connection is for the change list only; it is not written to QGIS.
    """
    custom = custom or load_custom()
    disabled = {tuple(k) for k in custom["disabled"]}
    conns = [c for c in builtin_connections(evy) if (c["kind"], c["name"]) not in disabled]
    return conns + [{"kind": c["kind"], "name": c["name"], "attrs": dict(c["attrs"]), "source": CUSTOM}
                    for c in custom["connections"] if c.get("enabled", True)]


def dataset_connections(datasets, source_info=None):
    """Connections for catalogue datasets; source_info (snapshot "sources") gives source labels."""
    labels = {k: v.get("label", k) for k, v in (source_info or {}).items()}
    out = []
    for ds in datasets:
        for service, url in ds["services"].items():
            kind, template = TEMPLATES[service]
            out.append({"kind": kind, "name": f"{service} {ds['name']}",
                        "attrs": dict(template, url=url),
                        "source": labels.get(ds.get("source"), ds.get("source", ""))})
    return out


def build(snapshot, evy):
    """All connections for a snapshot (None: static ones only), names cleaned and unique per kind."""
    conns = static_connections(evy)
    if snapshot:
        conns += dataset_connections(snapshot.get("datasets", []), snapshot.get("sources"))
    seen = set()
    for c in conns:
        name = clean_name(c["name"]) or "Unnamed"
        base, n = name, 1
        while (c["kind"], name) in seen:
            n += 1
            name = f"{base} ({n})"
        seen.add((c["kind"], name))
        c["name"] = name
    return conns


# ------------------------------------------------------------------ settings
def load_owned():
    try:
        with open(OWNED_PATH, encoding="utf-8") as f:
            return {k: set(v) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def save_owned(owned):
    store.write_json(OWNED_PATH, {k: sorted(v) for k, v in owned.items() if v})


def existing_names(qs, group):
    qs.beginGroup(group)
    try:
        return set(qs.childGroups())
    finally:
        qs.endGroup()


def write_connection(qs, group, conn):
    path = f"{group}/{conn['name']}"
    headers = {}
    for key, value in conn["attrs"].items():
        if not value:
            continue
        if key.startswith(HEADER_PREFIX):
            headers[key[len(HEADER_PREFIX):]] = value
        else:
            qs.setValue(f"{path}/{KEY_REPLACEMENTS.get(key, key)}", value)
    if headers:
        qs.setValue(f"{path}/http-header", headers)


OLD_SCRIPT = "Old script"


def read_connection(qs, group, name):
    """One connection from the settings, with every value (for backups and imports)."""
    reverse = {v: k for k, v in KEY_REPLACEMENTS.items()}
    qs.beginGroup(f"{group}/{name}")
    try:
        attrs = {}
        for key in qs.childKeys():
            value = qs.value(key)
            if key == "http-header" and isinstance(value, dict):
                for h, v in value.items():
                    attrs[HEADER_PREFIX + h] = str(v)
            elif isinstance(value, bool):
                attrs[reverse.get(key, key)] = str(value).lower()
            elif isinstance(value, (str, int, float)):
                attrs[reverse.get(key, key)] = str(value)
        return attrs
    finally:
        qs.endGroup()


def read_all():
    """Every Browser connection of the kinds this plugin handles, the user's own included."""
    qs = QSettings()
    out = []
    for kind, (group, *_) in KINDS.items():
        for name in sorted(existing_names(qs, group)):
            out.append({"kind": kind, "name": name, "attrs": read_connection(qs, group, name)})
    return out


def read_browser(conns):
    """What the QGIS Browser holds now, for the change list: the plugin's own connections,
    anything with the names of conns, and old-script ones (tagged source OLD_SCRIPT). URL only.
    The Browser may have been changed outside the plugin, so this is read every time."""
    qs = QSettings()
    owned = load_owned()
    wanted = {}
    for c in conns:
        wanted.setdefault(KINDS[c["kind"]][0], set()).add(c["name"])
    out = []
    for kind, (group, *_) in KINDS.items():
        existing = existing_names(qs, group)
        mine = owned.get(group, set())
        for name in sorted(existing):
            legacy = name not in mine and bool(LEGACY_RE.match(name))
            if name in mine or name in wanted.get(group, ()) or legacy:
                out.append({"kind": kind, "name": name, "source": OLD_SCRIPT if legacy else "",
                            "attrs": {"url": str(qs.value(f"{group}/{name}/url") or "")}})
    return out


def known_hosts(conns):
    """Hosts of every URL the plugin knows (catalogue, built-in and custom connections)."""
    return {urlparse(c["attrs"].get("url", "")).netloc.lower() for c in conns} - {""}


def looks_own(row, hosts):
    """A Removed row of old-script-style connections whose URLs come from no host the plugin
    knows: probably the user's own, not a stale catalogue dataset."""
    old = row.get("old") or []
    return (row["Change"] == "Removed" and bool(old)
            and all(c.get("source") == OLD_SCRIPT for c in old)
            and not any(urlparse(c["attrs"].get("url", "")).netloc.lower() in hosts for c in old))


def apply_rows(rows, log=None):
    """Make the changes of the given (ticked) rows: remove each row's old connections and
    write its new ones. Returns (written, removed, skipped names).

    A name taken by a connection the plugin did not write is left alone (skipped), unless it
    follows the old scripts' pattern (an older copy of the same connection) or the row
    replaces it anyway.
    """
    qs = QSettings()
    owned = load_owned()
    written = removed = 0
    skipped = []
    for row in rows:
        replacing = {(c["kind"], c["name"]) for c in row.get("old", [])}
        for c in row.get("old", []):
            group = KINDS[c["kind"]][0]
            qs.remove(f"{group}/{c['name']}")
            owned.get(group, set()).discard(c["name"])
            removed += 1
        for c in row.get("new", []):
            group = KINDS[c["kind"]][0]
            name = c["name"]
            if (name in existing_names(qs, group) and name not in owned.get(group, set())
                    and (c["kind"], name) not in replacing and not LEGACY_RE.match(name)):
                skipped.append(name)
                continue
            qs.remove(f"{group}/{name}")
            write_connection(qs, group, c)
            owned.setdefault(group, set()).add(name)
            written += 1
    qs.sync()
    save_owned(owned)
    if log and skipped:
        log(f"{len(skipped)} connection(s) left as they were: a connection with the same name "
            f"already exists that this plugin did not add (e.g. {skipped[0]})")
    return written, removed, skipped


def remove_owned():
    qs = QSettings()
    owned = load_owned()
    count = 0
    for group, names in owned.items():
        for name in names:
            qs.remove(f"{group}/{name}")
            count += 1
    qs.sync()
    save_owned({})
    return count


def find_legacy():
    """[(group, name)] of connections named like the old scripts' ones and not owned by the plugin."""
    qs = QSettings()
    owned = load_owned()
    found = []
    for group, *_ in KINDS.values():
        for name in sorted(existing_names(qs, group)):
            if LEGACY_RE.match(name) and name not in owned.get(group, ()):
                found.append((group, name))
    return found


def remove_legacy(found):
    qs = QSettings()
    for group, name in found:
        qs.remove(f"{group}/{name}")
    qs.sync()
    return len(found)


# ------------------------------------------------------------------ backups
BACKUP_DIR = os.path.join(store.PROFILE_DIR, "backups")
KEEP_BACKUPS = 10


def backup(reason):
    """Save every Browser connection (QGIS XML, one file per kind) and the plugin's records
    in backups/<YYYY-MM-DD_HHMMSS>/, keeping the last KEEP_BACKUPS. Returns the folder."""
    from datetime import datetime
    folder = os.path.join(BACKUP_DIR, f"{datetime.now():%Y-%m-%d_%H%M%S}")
    conns = read_all()
    export_xml(conns, folder, prefix="")
    store.write_json(os.path.join(folder, "backup.json"),
                     {"made": store.now(), "reason": reason, "connections": len(conns),
                      "owned": {k: sorted(v) for k, v in load_owned().items()},
                      "applied": store.read_applied()})
    for old in list_backups()[KEEP_BACKUPS:]:
        shutil.rmtree(old["folder"], ignore_errors=True)
    return folder


def list_backups():
    """Backups, newest first: [{"folder", "made", "reason", "connections"}]."""
    out = []
    if os.path.isdir(BACKUP_DIR):
        for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
            info = store.read_json(os.path.join(BACKUP_DIR, name, "backup.json"))
            if info:
                out.append(dict(info, folder=os.path.join(BACKUP_DIR, name)))
    return out


def restore(folder):
    """Put the Browser back exactly as in a backup: every connection of these kinds is replaced
    by the backup's, and the plugin's records with it. Returns the number restored."""
    info = store.read_json(os.path.join(folder, "backup.json")) or {}
    conns = []
    for kind, (*_, filename) in KINDS.items():
        path = os.path.join(folder, f"{filename}.xml")
        if os.path.exists(path):
            try:
                conns += parse_xml_file(path)
            except ValueError:
                pass  # an empty file: no connections of this kind
    qs = QSettings()
    for group, *_ in KINDS.values():
        qs.remove(group)
    for c in conns:
        write_connection(qs, KINDS[c["kind"]][0], c)
    qs.sync()
    save_owned({k: set(v) for k, v in info.get("owned", {}).items()})
    if info.get("applied"):
        store.write_json(store.APPLIED_PATH, info["applied"])
    return len(conns)


# ---------------------------------------------------------------------- XML
def xml_text(kind, conns):
    _, root, version, _ = KINDS[kind]
    lines = ["<!DOCTYPE connections>", f'<{root} version="{version}">']
    for c in conns:
        if c["kind"] == kind:
            attrs = " ".join(f"{k}={quoteattr(str(v))}" for k, v in c["attrs"].items())
            lines.append(f"    <{kind} name={quoteattr(c['name'])} {attrs}/>")
    lines.append(f"</{root}>")
    return "\n".join(lines) + "\n"


def export_xml(conns, folder, prefix=""):
    """Write one QGIS connection XML file per kind (as filter_v07 did). Returns the paths."""
    os.makedirs(folder, exist_ok=True)
    paths = []
    kinds = {c["kind"] for c in conns}
    for kind, (*_, filename) in KINDS.items():
        if kind not in kinds and (prefix or kind not in STANDARD_KINDS):
            continue
        path = os.path.join(folder, f"{prefix}{filename}.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml_text(kind, conns))
        paths.append(path)
    return paths
