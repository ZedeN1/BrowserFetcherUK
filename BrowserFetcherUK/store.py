"""Data folders, snapshots, history and the update lock.

Data folder layout (the shared NAS folder for EVY staff, else the local folder):
    snapshot.json                      current connection list
    history/YYMMDD_HHMM_snapshot.json  earlier snapshots (last KEEP_HISTORY kept)
    history/YYMMDD_HHMM_changes.csv    what each update changed
    work/<source>.json                 pages of an unfinished update (resume)
    update.lock                        who is updating right now
    update.log                         log of every update run

The QGIS profile folder holds what this user has applied (applied.json) and
which connections the plugin owns (owned.json).
"""
import csv
import getpass
import glob
import json
import os
import socket
import threading
import uuid
from datetime import datetime, timezone

from qgis.core import QgsApplication, QgsSettings

PROFILE_DIR = os.path.normpath(os.path.join(QgsApplication.qgisSettingsDirPath(), "BrowserFetcherUK"))
APPLIED_PATH = os.path.join(PROFILE_DIR, "applied.json")

SHARED_DEFAULT = (r"O:\0000_ElectronicLibrary\Computing\Software\GIS\QGIS\Gov_API" if os.name == "nt"
                  else "/net/O/0000_ElectronicLibrary/Computing/Software/GIS/QGIS/Gov_API")
LOCAL_DEFAULT = PROFILE_DIR

SETTINGS = "BrowserFetcherUK/"
KEEP_HISTORY = 12
# Unfinished update pages older than this are thrown away: the catalogue has moved on.
WORK_MAX_AGE_DAYS = 7
# A lock whose holder has not written a heartbeat for this long is treated as abandoned.
STALE_MINUTES = 15
HEARTBEAT_SECONDS = 60


# ------------------------------------------------------------------ settings
def settings():
    s = QgsSettings()
    evy = s.value(SETTINGS + "evy_mode", None)
    return {
        "evy": None if evy is None else evy in (True, "true", "True", 1, "1"),
        "shared": s.value(SETTINGS + "shared_folder", "") or SHARED_DEFAULT,
        "local": s.value(SETTINGS + "local_folder", "") or LOCAL_DEFAULT,
    }


def save_settings(evy, shared, local):
    s = QgsSettings()
    s.setValue(SETTINGS + "evy_mode", bool(evy))
    s.setValue(SETTINGS + "shared_folder", "" if os.path.normpath(shared) == SHARED_DEFAULT else shared)
    s.setValue(SETTINGS + "local_folder", "" if os.path.normpath(local) == LOCAL_DEFAULT else local)


def legacy_checked():
    return QgsSettings().value(SETTINGS + "legacy_checked", False, type=bool)


def set_legacy_checked():
    QgsSettings().setValue(SETTINGS + "legacy_checked", True)


# ---------------------------------------------------------------- utilities
def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def who():
    return f"{getpass.getuser()}@{socket.gethostname()}"


def parse_time(iso):
    return datetime.fromisoformat(iso)


def age_minutes(iso):
    return (datetime.now(timezone.utc) - parse_time(iso)).total_seconds() / 60


def describe(iso):
    """'29 Sep 2026 10:15 (today)' in local time."""
    when = parse_time(iso).astimezone()
    days = (datetime.now().astimezone().date() - when.date()).days
    age = "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"
    return f"{when:%d %b %Y %H:%M} ({age})"


def hhmm(iso):
    when = parse_time(iso).astimezone()
    if when.date() == datetime.now().astimezone().date():
        return f"{when:%H:%M}"
    return f"{when:%d %b %H:%M}"


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data, indent=None):
    """Write atomically, so readers on other machines never see half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- snapshots
def snapshot_path(folder):
    return os.path.join(folder, "snapshot.json")


def read_snapshot(folder):
    return read_json(snapshot_path(folder))


def history_dir(folder):
    return os.path.join(folder, "history")


def change_files(folder):
    """History change CSVs, newest first."""
    return sorted(glob.glob(os.path.join(history_dir(folder), "*_changes.csv")), reverse=True)


def publish(folder, snapshot, change_rows):
    """Make snapshot current: the old one goes to history with the CSV of what changed."""
    hist = history_dir(folder)
    os.makedirs(hist, exist_ok=True)
    stamp = f"{parse_time(snapshot['created']).astimezone():%y%m%d_%H%M}"
    old = snapshot_path(folder)
    if os.path.exists(old):
        prev = read_json(old) or {}
        prev_stamp = (f"{parse_time(prev['created']).astimezone():%y%m%d_%H%M}"
                      if prev.get("created") else stamp + "_old")
        os.replace(old, os.path.join(hist, f"{prev_stamp}_snapshot.json"))
    write_json(old, snapshot)
    from . import changes
    changes.write_csv(os.path.join(hist, f"{stamp}_changes.csv"), change_rows, snapshot["created"])
    prune_history(folder)


def prune_history(folder):
    hist = history_dir(folder)
    for pattern in ("*_snapshot.json", "*_changes.csv"):
        for path in sorted(glob.glob(os.path.join(hist, pattern)), reverse=True)[KEEP_HISTORY:]:
            try:
                os.remove(path)
            except OSError:
                pass


def read_applied():
    return read_json(APPLIED_PATH)


def write_applied(snapshot, origin, evy, conns):
    write_json(APPLIED_PATH, {"created": snapshot.get("created") if snapshot else None,
                              "origin": origin, "evy": evy, "applied": now(),
                              "connections": conns})


def append_log(folder, text):
    try:
        with open(os.path.join(folder, "update.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {who()} {text}\n")
    except OSError:
        pass


# ------------------------------------------------------------ work (resume)
def work_dir(folder):
    return os.path.join(folder, "work")


def work_path(folder, key):
    return os.path.join(work_dir(folder), f"{key}.json")


def read_work(folder, key):
    """Pages of an unfinished update for one source, or None if absent or too old."""
    work = read_json(work_path(folder, key))
    if not work or not work.get("started"):
        return None
    if age_minutes(work["started"]) > WORK_MAX_AGE_DAYS * 24 * 60:
        return None
    return work


def work_keys(folder):
    """Sources with an unfinished update."""
    return sorted(os.path.splitext(os.path.basename(p))[0]
                  for p in glob.glob(os.path.join(work_dir(folder), "*.json")))


def clear_work(folder, keys):
    for key in keys:
        try:
            os.remove(work_path(folder, key))
        except OSError:
            pass
    try:
        os.rmdir(work_dir(folder))
    except OSError:
        pass


# --------------------------------------------------------------------- lock
class LockBusy(Exception):
    def __init__(self, info):
        super().__init__(f"update in progress by {info.get('user', '?')}")
        self.info = info


def lock_path(folder):
    return os.path.join(folder, "update.lock")


def read_lock(folder):
    """The lock's content plus "stale": True when its holder stopped writing heartbeats."""
    path = lock_path(folder)
    if not os.path.exists(path):
        return None
    info = read_json(path)
    if not info or "heartbeat" not in info:
        # Being written right now, or garbage: judge by the file time instead.
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat()
        except OSError:
            return None
        info = {"user": "?", "started": mtime, "heartbeat": mtime}
    info["stale"] = age_minutes(info["heartbeat"]) > STALE_MINUTES
    return info


class Lock:
    """update.lock in a data folder. Only one holder at a time, on any machine.

    Created with O_CREAT | O_EXCL (atomic on local disks, SMB and NFS). The
    holder rewrites the heartbeat every HEARTBEAT_SECONDS from a thread; a
    lock without a heartbeat for STALE_MINUTES can be taken over, by renaming
    it away first so only one of several takers wins.
    """

    def __init__(self, folder):
        self.folder = folder
        self.path = lock_path(folder)
        self.token = uuid.uuid4().hex
        self.info = None
        self._stop = threading.Event()
        self._thread = None

    def acquire(self):
        os.makedirs(self.folder, exist_ok=True)
        current = read_lock(self.folder)
        if current is not None:
            if not current["stale"]:
                raise LockBusy(current)
            aside = f"{self.path}.stale-{uuid.uuid4().hex[:8]}"
            try:
                os.rename(self.path, aside)
            except OSError:
                pass  # someone else took it over first; the create below tells
            else:
                moved = read_json(aside) or {}
                if moved.get("heartbeat", current["heartbeat"]) != current["heartbeat"]:
                    # Another taker replaced the stale lock between our read and rename:
                    # that lock is live, put it back.
                    try:
                        os.rename(aside, self.path)
                    except OSError:
                        pass
                    raise LockBusy(dict(moved, stale=False))
                try:
                    os.remove(aside)
                except OSError:
                    pass
        self.info = {"user": getpass.getuser(), "host": socket.gethostname(), "started": now(),
                     "heartbeat": now(), "token": self.token}
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise LockBusy(read_lock(self.folder) or {"user": "?"})
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.info, f)
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()
        return self

    def still_mine(self):
        info = read_json(self.path)
        return bool(info) and info.get("token") == self.token

    def holder(self):
        return read_lock(self.folder)

    def _beat(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            if not self.still_mine():
                return
            self.info["heartbeat"] = now()
            try:
                write_json(self.path, self.info)
            except OSError:
                pass

    def release(self):
        self._stop.set()
        if self.still_mine():
            try:
                os.remove(self.path)
            except OSError:
                pass
