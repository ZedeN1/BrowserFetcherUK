"""Background tasks: fetch the catalogues into a new snapshot, and read the folder status.

FetchTask holds the data folder's update lock (acquired by the caller) and has
one SourceTask per catalogue as subtasks, so the three run in parallel. Each
source saves every page to work/<source>.json, so a cancelled or crashed
update resumes where it stopped, on any machine that can see the folder.
"""
import os
import re
import time
import traceback

from qgis.core import QgsTask, QgsFeedback
from qgis.PyQt.QtCore import pyqtSignal

from . import changes, connections, store
from .sources import BASE_SLEEP, MAX_RETRIES, SLEEP_INCREMENT

# Attempts at a catalogue's first request in a run (MAX_RETRIES once it has answered).
FIRST_PAGE_RETRIES = 3

_TAGS = re.compile(r"<[^>]+>")


class LockLost(Exception):
    pass


# No "Task complete" pop-ups for quick background work.
SILENT = QgsTask.Flag.CanCancel | QgsTask.Flag.Silent


class _Task(QgsTask):
    message = pyqtSignal(str)

    def __init__(self, description, flags=SILENT):
        super().__init__(description, flags)
        self.feedback = QgsFeedback()
        self.error = None

    def cancel(self):
        self.feedback.cancel()
        super().cancel()

    def log(self, text):
        self.message.emit(text)

    def warn(self, text):
        self.log(f"<span style='color:#b36b00'>{text}</span>")

    def sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.isCanceled():
            time.sleep(min(0.5, max(0.0, end - time.monotonic())))


class SourceTask(_Task):
    """Fetch every page of one catalogue into its work file.

    Progress goes out as counted(key, records done, total) for the dialog's
    per-source bars; only milestones and problems go to the log (page by page
    progress is written to update.log only).
    """

    counted = pyqtSignal(str, int, int)

    def __init__(self, source, folder, lock):
        super().__init__(f"Browser Fetcher: {source.LABEL}")
        self.source = source
        self.folder = folder
        self.lock = lock
        self.complete = False
        self.lost_lock = False
        self.answered = False  # the catalogue has answered at least once in this run

    def run(self):
        try:
            self.fetch()
            return not self.isCanceled()
        except LockLost as e:
            self.lost_lock = True
            self.error = str(e)
            return False
        except Exception as e:
            if self.isCanceled():
                return False
            # A source that fails is not fatal: the snapshot keeps its previous datasets.
            self.error = str(e)
            if self.answered:
                self.warn(f"{self.source.LABEL}: failed ({e}); pages fetched so far are kept and "
                          f"the next fetch carries on from there")
            else:
                self.warn(f"{self.source.LABEL}: not answering at the moment ({e}); try again later. "
                          f"Its datasets from the previous copy are kept")
            self.log_detail(traceback.format_exc())
            return True

    def log_detail(self, text):
        store.append_log(self.folder, text)

    def save(self, work):
        if not self.lock.still_mine():
            holder = self.lock.holder() or {}
            raise LockLost(f"the update was taken over by {holder.get('user', 'someone else')}")
        store.write_json(store.work_path(self.folder, self.source.KEY), work)

    def fetch(self):
        src = self.source
        work = store.read_work(self.folder, src.KEY)
        if work is None:
            work = {"started": store.now(), "total": None, "complete": False, "pages": {}}
        pages = work["pages"]
        offset = max((int(o) + p["count"] for o, p in pages.items()), default=0)
        total = work.get("total")
        if work.get("complete"):
            self.complete = True
            self.counted.emit(src.KEY, offset, total or offset)
            self.log(f"{src.LABEL}: already fetched ({offset} records)")
            return
        self.counted.emit(src.KEY, offset, total or 0)
        if offset:
            self.log(f"{src.LABEL}: resuming at record {offset}" + (f" of {total}" if total else ""))
        else:
            self.log_detail(f"{src.LABEL}: starting")

        while not self.isCanceled():
            if total is not None and offset >= total:
                break
            raw = self.fetch_page(offset)
            if raw is None:
                return  # cancelled
            count, raw = raw
            self.answered = True
            if total is None and count is not None:
                total = work["total"] = count
                self.log_detail(f"{src.LABEL}: {total} records in the catalogue")
            if not raw:
                break
            pages[str(offset)] = {"count": len(raw), "records": src.parse(raw)}
            self.save(work)
            offset += len(raw)
            if total:
                self.setProgress(min(100.0, 100.0 * offset / total))
                self.counted.emit(src.KEY, min(offset, total), total)
                self.log_detail(f"{src.LABEL}: {min(offset, total)} / {total}")
            self.sleep(BASE_SLEEP)

        if not self.isCanceled():
            work["complete"] = True
            self.save(work)
            self.complete = True
            self.setProgress(100.0)
            self.counted.emit(src.KEY, total or offset, total or offset)
            kept = sum(len(p["records"]) for p in pages.values())
            self.log(f"{src.LABEL}: done, {kept} datasets with map services")

    def fetch_page(self, offset):
        # A catalogue that has not answered once in this run is probably down: give up soon
        # rather than retry for half an hour. Once it answers, ride out occasional failures.
        tries = MAX_RETRIES if self.answered else FIRST_PAGE_RETRIES
        wait = BASE_SLEEP
        for attempt in range(1, tries + 1):
            try:
                return self.source.fetch_page(offset, self.feedback)
            except Exception as e:
                if self.isCanceled():
                    return None
                if attempt == tries:
                    raise RuntimeError(f"record {offset}: {e}; gave up after {tries} attempts")
                self.warn(f"{self.source.LABEL}: record {offset}: {e} "
                          f"(attempt {attempt}/{tries}, retrying in {wait} s)")
                self.sleep(wait)
                if self.isCanceled():
                    return None
                wait += SLEEP_INCREMENT
        return None


def _dataset_key(name):
    return name.replace("[RETIRED] ", "").casefold()


def carry_over(full_sets, new):
    """Datasets from a full-only source that the new (quick) datasets do not cover,
    matched by name or by any shared service link."""
    names = {_dataset_key(d["name"]) for d in new}
    urls = {u for d in new for u in d["services"].values()}
    return [d for d in full_sets
            if _dataset_key(d["name"]) not in names and not urls & set(d["services"].values())]


def assemble(work):
    """Records of a work file in catalogue order, each id once (pages can overlap)."""
    seen = set()
    out = []
    for offset in sorted(work["pages"], key=int):
        for rec in work["pages"][offset]["records"]:
            if rec["id"] in seen:
                continue
            seen.add(rec["id"])
            out.append(rec)
    return out


class FetchTask(_Task):
    """Parent task: runs after every SourceTask and publishes the new snapshot."""

    def __init__(self, folder, lock, on_finished, sources, full=False, keep_slots=(), regions=None,
                 full_only=()):
        """sources: Source objects to fetch. keep_slots: slots of enabled sources not run
        this time: their datasets are carried over from the previous snapshot.
        full_only: keys of sources used only by a full fetch (DEFRA). In a quick fetch,
        their datasets that the quick source of the same slot does not have (MMO ones
        missing from data.gov.uk) are carried over, until a full fetch drops them."""
        # A slow fetch runs for an hour or more: let QGIS say when it is done.
        super().__init__("Browser Fetcher: " + ("slow fetch" if full else "fast fetch"),
                         QgsTask.Flag.CanCancel if full else SILENT)
        self.folder = folder
        self.lock = lock
        self.on_finished = on_finished
        self.full = full
        self.keep_slots = set(keep_slots)
        self.full_only = set(full_only)
        self.regions = regions
        self.snapshot = None
        self.rows = []
        self.lost_lock = False
        self.subtasks = [SourceTask(src, folder, lock) for src in sources]
        for sub in self.subtasks:
            sub.message.connect(self.message.emit)
            self.addSubTask(sub, [], QgsTask.SubTaskDependency.ParentDependsOnSubTask)
        self.message.connect(self._log_to_file)

    def _log_to_file(self, text):
        store.append_log(self.folder, _TAGS.sub("", text))

    def run(self):
        try:
            self.build()
            return not self.isCanceled()
        except LockLost as e:
            self.lost_lock = True
            self.error = str(e)
            return False
        except Exception as e:
            if not self.isCanceled():
                self.error = f"{e}\n{traceback.format_exc()}"
            return False

    def build(self):
        prev = store.read_snapshot(self.folder) or {}
        prev_sources = prev.get("sources", {})
        prev_sets = prev.get("datasets", [])

        def prev_slot(slot):
            keys = [k for k, v in prev_sources.items() if v.get("slot", k) == slot]
            return keys, [d for d in prev_sets if d.get("slot", d.get("source")) == slot]

        datasets, info = [], {}
        for sub in self.subtasks:
            src = sub.source
            key, label, slot = src.KEY, src.LABEL, src.slot
            work = store.read_work(self.folder, key) or {"pages": {}, "total": None}
            recs = assemble(work)
            old_keys, old = prev_slot(slot)
            if sub.complete or not old:
                if not sub.complete:
                    self.warn(f"{label}: incomplete, using the {len(recs)} datasets fetched so far")
                new = [{"source": key, "slot": slot, "name": r["name"], "services": r["services"]}
                       for r in recs]
                datasets += new
                info[key] = {"label": label, "slot": slot, "fetched": store.now(),
                             "records": work.get("total"), "datasets": len(recs),
                             "complete": sub.complete, "error": sub.error}
                if not self.full and sub.complete:
                    carried = carry_over([d for d in old if d.get("source") in self.full_only], new)
                    if carried:
                        datasets += carried
                        for k in {d["source"] for d in carried}:
                            info[k] = dict(prev_sources.get(k, {"label": k}), slot=slot,
                                           carried=sum(d["source"] == k for d in carried))
                        self.log(f"{label}: kept {len(carried)} datasets found only by the last "
                                 f"slow fetch")
            else:
                self.warn(f"{label}: keeping the {len(old)} datasets from the previous copy")
                datasets += old
                for k in old_keys:
                    info[k] = dict(prev_sources[k], complete=False, error=sub.error)
        # Slots this fetch does not cover keep their previous datasets.
        done_slots = {s.source.slot for s in self.subtasks}
        for slot in self.keep_slots - done_slots:
            old_keys, old = prev_slot(slot)
            datasets += old
            for k in old_keys:
                info[k] = prev_sources[k]
        from . import __version__
        # Only a slow fetch whose slow-only sources (DEFRA) completed counts as one: otherwise
        # England is still the fast fetch's, and the copy must not claim otherwise.
        slow_only = [s for s in self.subtasks if s.source.config.get("use") == "full"]
        full = self.full and bool(slow_only) and all(s.complete for s in slow_only)
        if self.full and not full:
            self.warn("The slow-fetch-only sources did not complete, so this copy counts as a fast fetch")
        self.snapshot = {"created": store.now(), "created_by": store.who(),
                         "plugin_version": __version__, "full": full, "sources": info,
                         "datasets": datasets}
        self.rows = changes.diff(connections.dataset_connections(prev_sets, prev_sources),
                                 connections.dataset_connections(datasets, info), self.regions)
        if not self.lock.still_mine():
            raise LockLost("the update was taken over by someone else")
        store.publish(self.folder, self.snapshot, self.rows)
        store.clear_work(self.folder, [s.source.KEY for s in self.subtasks if s.complete])
        self.log(f"New copy saved: {len(datasets)} datasets, {changes.summary(self.rows)}")

    def finished(self, result):
        self.lock.release()
        if not result:
            lost = [s for s in self.subtasks if s.lost_lock]
            if lost:
                self.lost_lock = True
                self.error = self.error or lost[0].error
        self.on_finished(self, result)


class StatusTask(_Task):
    """Reads a data folder (can hang for a while on an unreachable network drive)."""

    def __init__(self, settings, on_finished):
        super().__init__("Browser Fetcher: reading data folder", SILENT | QgsTask.Flag.Hidden)
        self.settings = settings
        self.on_finished = on_finished
        self.evy = settings["evy"]
        self.local_folder = settings["local"]
        self.shared_folder = settings["shared"]
        self.shared_ok = False
        self.fell_back = False       # EVY staff, but the shared folder cannot be reached
        self.local_snapshot = None
        self.shared_snapshot = None
        self.local_work_keys = []
        # Where fetches go (shared folder for EVY staff when reachable), its lock and unfinished work.
        self.folder = self.local_folder
        self.lock = None
        self.work_keys = []
        # The copy to use: the newer of the shared and local ones.
        self.snapshot = None
        self.origin = "local"
        self.snapshot_folder = self.local_folder
        self.local_newer = False     # EVY staff with a local copy newer than the shared one
        self.reachable = False

    def run(self):
        try:
            evy = self.evy
            os.makedirs(self.local_folder, exist_ok=True)
            self.local_snapshot = store.read_snapshot(self.local_folder)
            self.local_work_keys = store.work_keys(self.local_folder)
            if evy:
                self.shared_ok = os.path.isdir(self.shared_folder)
                self.fell_back = not self.shared_ok
            if self.shared_ok:
                self.folder = self.shared_folder
                self.shared_snapshot = store.read_snapshot(self.shared_folder)
                self.lock = store.read_lock(self.shared_folder)
                self.work_keys = store.work_keys(self.shared_folder)
            else:
                self.lock = store.read_lock(self.local_folder)
                self.work_keys = self.local_work_keys
            self.reachable = True

            local_time = (self.local_snapshot or {}).get("created", "")
            shared_time = (self.shared_snapshot or {}).get("created", "")
            if self.shared_ok and shared_time >= local_time:
                self.snapshot, self.origin, self.snapshot_folder = (
                    self.shared_snapshot, "shared", self.shared_folder)
            else:
                self.snapshot = self.local_snapshot
                self.local_newer = self.shared_ok and bool(local_time)
            return True
        except Exception as e:
            self.error = str(e)
            return False

    @property
    def can_copy_to_shared(self):
        """A newer local copy from a complete slow fetch, which is worth sharing (fast ones
        take seconds to refetch into the shared folder instead)."""
        snap = self.local_snapshot or {}
        return (self.local_newer and snap.get("full")
                and all(i.get("complete", True) for i in snap.get("sources", {}).values()))

    def finished(self, result):
        self.on_finished(self, result)
