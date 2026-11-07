"""Getting bytes on and off the disk safely: atomic replace, a lock between programs, and backups."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from datetime import datetime

from .schema import Unavailable

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

KEEP_SNAPSHOTS = 30
LOCK_TIMEOUT = 5.0
TEMP_PREFIX = ".boards-"
TEMP_SUFFIX = ".tmp"
PREVIOUS = "previous"
SNAPSHOT_RE = re.compile(r"boards-\d{4}-\d{2}-\d{2}(T\d{6})?(-[a-z-]+)?\.json")


def reason(error: OSError) -> str:
    return error.strerror or str(error)


class FileLock:
    """Keeps two Laneboard programs (a server and a command, say) from saving at the same moment.

    It's an operating system lock, so it goes away by itself if a program dies holding it.
    """

    def __init__(self, path, timeout=LOCK_TIMEOUT):
        self.path = path
        self.timeout = timeout
        self._fd = None

    def _try(self):
        try:
            if fcntl:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        while not self._try():
            if time.monotonic() > deadline:
                os.close(self._fd)
                self._fd = None
                raise Unavailable(f"Another program has been holding {self.path} for {self.timeout:.0f} seconds. "
                                  "Your change was not saved. Try again.")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        try:
            if not fcntl:
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(self._fd)  # closing also drops a flock
            self._fd = None


def _replace(source, target, tries=12):
    """os.replace, waiting out the moment Windows refuses because a scanner or reader has the file open."""
    for attempt in range(tries):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if os.name != "nt" or attempt == tries - 1:
                raise
            time.sleep(0.05 + attempt * 0.02)


def _sync_folder(folder):
    if os.name == "nt":
        return  # Windows can't open a folder for flushing
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path, data: bytes):
    """Replace a file in one step. If anything goes wrong the old file is still there, whole."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    existing_mode = None
    if os.path.exists(path):
        if not os.access(path, os.W_OK):
            raise PermissionError(13, "the file is read-only", path)
        existing_mode = os.stat(path).st_mode
    fd, tmp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if existing_mode is not None:
            os.chmod(tmp, existing_mode & 0o777)
        _replace(tmp, path)
        _sync_folder(folder)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def copy_atomic(source, target):
    folder = os.path.dirname(target)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=TEMP_SUFFIX, dir=folder)
    os.close(fd)
    try:
        shutil.copyfile(source, tmp)
        _replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def remove_stale_temp_files(path, older_than=3600):
    """A program killed in the middle of a save leaves its temp file behind. The real file is fine."""
    folder = os.path.dirname(path)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    cutoff = time.time() - older_than
    for name in names:
        if name.startswith(TEMP_PREFIX) and name.endswith(TEMP_SUFFIX):
            full = os.path.join(folder, name)
            try:
                if os.path.getmtime(full) < cutoff:
                    os.remove(full)
            except OSError:
                pass


# ---- backups --------------------------------------------------------------------------------------


def backups_folder(path):
    return path + ".backups"


def previous_path(path):
    return path + ".bak"


def _prune(folder, keep=KEEP_SNAPSHOTS):
    try:
        names = [n for n in os.listdir(folder) if SNAPSHOT_RE.fullmatch(n)]
    except OSError:
        return
    full = sorted((os.path.join(folder, n) for n in names), key=os.path.getmtime, reverse=True)
    for old in full[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass


def daily_snapshot(path):
    """Keep the first version of each day, so a mistake found tomorrow can still be walked back."""
    if not os.path.exists(path):
        return None
    folder = backups_folder(path)
    target = os.path.join(folder, f"boards-{datetime.now():%Y-%m-%d}.json")
    if os.path.exists(target):
        return None
    try:
        copy_atomic(path, target)
    except OSError:
        return None  # a missed extra copy must not stop a save; the .bak still protects the last step
    _prune(folder)
    return target


def tagged_snapshot(path, tag):
    """Keep a copy right before something that removes a lot, like deleting a board."""
    if not os.path.exists(path):
        return None
    folder = backups_folder(path)
    target = os.path.join(folder, f"boards-{datetime.now():%Y-%m-%dT%H%M%S}-{tag}.json")
    n = 2
    while os.path.exists(target):
        target = os.path.join(folder, f"boards-{datetime.now():%Y-%m-%dT%H%M%S}-{tag}-{'x' * n}.json")
        n += 1
    copy_atomic(path, target)
    _prune(folder)
    return target


def list_backups(path):
    """Every copy that can be restored, newest first. Names are what `restore` accepts."""
    found = []
    bak = previous_path(path)
    if os.path.isfile(bak):
        found.append({"name": PREVIOUS, "path": bak})
    folder = backups_folder(path)
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    for name in names:
        if SNAPSHOT_RE.fullmatch(name):
            found.append({"name": name, "path": os.path.join(folder, name)})
    prefix = os.path.basename(path) + ".v"
    parent = os.path.dirname(path)
    try:
        for name in os.listdir(parent):
            if name.startswith(prefix) and name.endswith(".bak"):
                found.append({"name": name, "path": os.path.join(parent, name)})
    except OSError:
        pass
    for item in found:
        st = os.stat(item["path"])
        item.update(bytes=st.st_size, modified=st.st_mtime)
    found.sort(key=lambda b: b["modified"], reverse=True)
    return found
