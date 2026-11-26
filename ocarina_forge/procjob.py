"""Keep converter workers and the processes the builder starts from outliving their parent.

On Windows the API holds a job object with kill-on-close and is a member of
it. Pool workers inherit that membership when they are created, and the Node
meshopt helper a worker starts inherits it too. A hard kill of the API (Task
Manager, or ``taskkill /F``) closes the job and ends those processes. If the
API could not join (it was already in a job that refused a nested one), each
worker still opens the job by name and joins it from the pool initializer.
The builder holds a separate job and is a member of it, so the API and the
preview process die with the builder. ``CREATE_NEW_PROCESS_GROUP`` stays in
place: Ctrl+C still belongs to the builder, which stops those processes itself.

Shutdown and a pool replacement also terminate the workers. That path is the
same on Linux and macOS. Linux workers additionally ask for SIGTERM when the
parent exits. Every worker also watches the parent, so a missed job still ends
the process. Nothing here signals a Python process that is not one of these
workers.
"""

from __future__ import annotations

import ctypes
import logging
import multiprocessing
import os
import re
import select
import signal
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

# Recycle a worker after this many converts so a 2–4 GB high-water mark does
# not sit for the rest of the server. ProcessPoolExecutor grew the argument
# in 3.11, which is the oldest Python this project runs.
POOL_TASKS_PER_CHILD = 16
POOL_WORKER_ENV = "OCARINA_FORGE_POOL_WORKER"

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9

_SPAWN_PARENT = re.compile(r"spawn_main\(\s*parent_pid\s*=\s*(\d+)")
_GUARD = threading.Lock()
_JOBS: dict[str, int] = {}
_KERNEL = None
_NTDLL = None


# DWORD is 32 bits on Windows (LLP64). ctypes.wintypes.DWORD follows the host
# C long, which is 64 bits on Linux, so these fields use an explicit width.
# The layout then matches the 64-bit Windows job structure on either host.
_DWORD = ctypes.c_uint32


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", _DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", _DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", _DWORD),
        ("SchedulingClass", _DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def kill_on_close_limits() -> JOBOBJECT_EXTENDED_LIMIT_INFORMATION:
    """The job limit that ends every member when the last handle closes."""
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    return info


def server_job_name(pid: int | None = None) -> str:
    return f"Local\\OcarinaForge-{os.getpid() if pid is None else pid}"


def builder_job_name(pid: int | None = None) -> str:
    return f"Local\\OcarinaForgeBuilder-{os.getpid() if pid is None else pid}"


def ensure_server_job() -> bool:
    """Create the API job and put the API in it so pool workers inherit it.

    The handle is not inheritable and stays open until the API process exits.
    That close (``taskkill /F``, or ending the process in Task Manager) is
    what ends the workers. If the API is already in a job that refuses a
    nested one, the job is still kept and each worker joins it by name.
    """
    if sys.platform != "win32":
        return False
    return _ensure_job(server_job_name(), assign_self=True, require_member=False)


def ensure_builder_job() -> bool:
    """Put the builder in a job before it starts the API and the preview.

    Children inherit the membership. The job handle is not inheritable, so a
    child does not keep the job alive after the builder exits.
    """
    if sys.platform != "win32":
        return False
    return _ensure_job(builder_job_name(), assign_self=True, require_member=True)


def join_server_job() -> bool:
    """A pool worker joins the API job named for its parent. No-op elsewhere.

    A worker spawned by an API that is already in the job inherited that
    membership. Joining again is success, not an error.
    """
    if sys.platform != "win32":
        return False
    kernel = _kernel()
    if kernel is None:
        return False
    name = server_job_name(os.getppid())
    # JOB_OBJECT_ASSIGN_PROCESS | JOB_OBJECT_QUERY
    handle = kernel.OpenJobObjectW(0x0005, False, name)
    if not handle:
        _note(f"converter worker could not open job {name} (winerror {ctypes.get_last_error()})")
        return False
    try:
        current = kernel.GetCurrentProcess()
        already = wintypes.BOOL()
        if kernel.IsProcessInJob(current, handle, ctypes.byref(already)) and already:
            return True
        if kernel.AssignProcessToJobObject(handle, current):
            return True
        _note(
            "converter worker was not added to the API job "
            f"(winerror {ctypes.get_last_error()}). It will exit when the API does."
        )
        return False
    finally:
        kernel.CloseHandle(handle)


def prepare_pool_worker() -> None:
    """Marker, parent-death signal, job membership, and a parent watch.

    Called from the pool initializer. An exception here must not escape: a
    failing initializer marks the whole pool broken.
    """
    try:
        os.environ[POOL_WORKER_ENV] = "1"
        arm_parent_death_signal()
        join_server_job()
        _watch_parent()
    except Exception as exc:
        _note(f"converter worker could not arm process lifetime: {exc}")


def arm_parent_death_signal() -> bool:
    """Linux: SIGTERM when the parent exits. The call is skipped on other systems."""
    if not sys.platform.startswith("linux"):
        return False
    # Pid 1 can be the real parent (the API is the container's init). Only a
    # change of ppid across this call means the parent died and we were reparented.
    parent = os.getppid()
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int
        # PR_SET_PDEATHSIG = 1
        if libc.prctl(1, int(signal.SIGTERM), 0, 0, 0) != 0:
            return False
    except (OSError, AttributeError):
        return False
    if os.getppid() != parent:
        os._exit(0)
    return True


def parent_death_signal() -> int | None:
    """The signal Linux will deliver on parent exit, or None when it is unset."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int
        sig = ctypes.c_int(0)
        # PR_GET_PDEATHSIG = 2
        if libc.prctl(2, ctypes.byref(sig), 0, 0, 0) != 0:
            return None
        return int(sig.value)
    except (OSError, AttributeError):
        return None


def pool_executor_kwargs(count: int, initializer) -> dict:
    """Spawn-context pool arguments, including the 3.11 recycle limit."""
    kwargs = {
        "max_workers": count,
        "mp_context": multiprocessing.get_context("spawn"),
        "initializer": initializer,
    }
    if sys.version_info >= (3, 11):
        kwargs["max_tasks_per_child"] = POOL_TASKS_PER_CHILD
    return kwargs


def terminate_executor(executor) -> None:
    """Cancel queued work and kill the workers and their children now.

    ``shutdown(wait=False)`` only asks idle workers to exit. A convert that is
    already running would keep its process, and on Windows that process would
    keep its Node helper. Copy the pids before shutdown: the executor drops
    its process map before those processes are actually gone.
    """
    if executor is None:
        return
    pids: list[int] = []
    current = getattr(executor, "_processes", None) or {}
    for proc in list(current.values()):
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int):
            pids.append(pid)
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except Exception as exc:
        _note(f"pool shutdown returned early: {exc}")
    for pid in pids:
        kill_tree(pid)


def kill_tree(pid: int, *, seen: set[int] | None = None) -> None:
    """End this pid and its descendants. Never the current process."""
    if pid <= 0 or pid == os.getpid():
        return
    if seen is None:
        seen = set()
    if pid in seen:
        return
    seen.add(pid)
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
        return
    for child in child_pids(pid):
        kill_tree(child, seen=seen)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return


def pid_alive(pid: int) -> bool:
    """True when ``pid`` is still a process. Access denied counts as alive."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        kernel = _kernel()
        if kernel is None:
            return False
        # PROCESS_QUERY_LIMITED_INFORMATION
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            kernel.CloseHandle(handle)
            return True
        # ERROR_ACCESS_DENIED: it exists and we cannot open it.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still accepts signal 0. It has already exited.
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True
        end = stat.rfind(")")
        fields = stat[end + 2 :].split() if end >= 0 else []
        if fields and fields[0] == "Z":
            return False
    return True


def child_pids(pid: int) -> list[int]:
    """Direct children. Linux reads ``/proc``. Windows uses the process snapshot."""
    if pid <= 0:
        return []
    if sys.platform == "win32":
        return [item.pid for item in _windows_raw() if item.ppid == pid]
    found = []
    root = Path("/proc")
    if not root.is_dir():
        return []
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _ppid_from_stat(stat) == pid:
            found.append(int(entry.name))
    return found


def spawn_parent_pid(cmdline: str) -> int | None:
    """The ``parent_pid`` from a ``multiprocessing`` spawn command line."""
    if not cmdline or "spawn_main" not in cmdline:
        return None
    match = _SPAWN_PARENT.search(cmdline)
    if match is None:
        return None
    return int(match.group(1))


@dataclass(frozen=True)
class ProcessFact:
    """One process the leftover scan decided was worth reading."""

    pid: int
    ppid: int
    cmdline: str
    environ: str
    image: str
    children: tuple[str, ...] = ()


def is_forge_worker(fact: ProcessFact) -> bool:
    """True only for a Forge pool worker, not for an arbitrary Python process.

    The command line of every spawn worker looks the same. A match needs the
    marker set in the worker, a checkout path on that command line or image,
    or a ``meshopt-worker.mjs`` child. A working directory is not enough: other
    tools get launched from this repo.
    """
    if f"{POOL_WORKER_ENV}=1" in (fact.environ or ""):
        return True
    parts = [fact.cmdline or "", fact.image or "", *(fact.children or ())]
    blob = " ".join(parts).lower().replace("\\", "/")
    if "meshopt-worker.mjs" in blob:
        return True
    if "ocarina-forge" in blob or "ocarina_forge" in blob:
        return True
    return False


def leftover_workers(facts: list[ProcessFact], alive_pids: set[int]) -> list[ProcessFact]:
    """Forge spawn workers whose ``parent_pid`` is not running. Nothing is signaled."""
    found = []
    for fact in facts:
        parent = spawn_parent_pid(fact.cmdline)
        if parent is None or parent in alive_pids:
            continue
        if is_forge_worker(fact):
            found.append(fact)
    return found


def collect_process_facts(proc_root: Path | None = None) -> tuple[list[ProcessFact], set[int]]:
    """Spawn workers and the set of pids that currently exist.

    ``proc_root`` is the Linux procfs. Tests pass a fake directory. Windows
    ignores it and reads the process snapshot.
    """
    if proc_root is None and sys.platform == "win32":
        raw = _windows_raw()
    else:
        raw = _procfs_raw(proc_root or Path("/proc"))
    alive = {item.pid for item in raw}
    children: dict[int, list[str]] = {}
    for item in raw:
        if item.cmdline:
            children.setdefault(item.ppid, []).append(item.cmdline)
    facts = []
    for item in raw:
        if spawn_parent_pid(item.cmdline) is None:
            continue
        facts.append(
            ProcessFact(
                pid=item.pid,
                ppid=item.ppid,
                cmdline=item.cmdline,
                environ=item.environ,
                image=item.image,
                children=tuple(children.get(item.pid, ())),
            )
        )
    return facts, alive


def log_leftover_workers() -> list[ProcessFact]:
    """Log Forge workers whose parent is already gone. Do not stop them."""
    try:
        facts, alive = collect_process_facts()
        rows = leftover_workers(facts, alive)
    except Exception as exc:
        _note(f"could not scan for leftover converter workers: {exc}")
        return []
    log = logging.getLogger("uvicorn.error")
    for fact in rows:
        parent = spawn_parent_pid(fact.cmdline)
        helper = any("meshopt-worker.mjs" in (child or "").replace("\\", "/") for child in fact.children)
        suffix = " A node meshopt helper is still attached." if helper else ""
        log.warning(
            "leftover Forge converter worker pid %s (spawn_main parent %s is gone). "
            "It was not stopped. Close that python process in Task Manager if it is still using memory.%s",
            fact.pid,
            parent,
            suffix,
        )
    return rows


def create_job(name: str, *, assign_self: bool = False, require_member: bool = True) -> int | None:
    """A kill-on-close job the caller owns. Windows only; None elsewhere.

    The returned handle is not stored. Closing it ends members when this is
    the last handle. Tests use that. The server and the builder keep theirs.
    When ``require_member`` is false, a failed self-assign still returns the
    handle so other processes can join the job by name.
    """
    if sys.platform != "win32":
        return None
    handle = _open_or_create_job(name)
    if not handle:
        return None
    if not _set_kill_on_close(handle):
        _close_handle(handle)
        return None
    _set_not_inherited(handle)
    if assign_self and not _assign_current(handle):
        if require_member:
            _close_handle(handle)
            return None
        _note(f"could not add this process to job {name}; children will join it by name")
    return int(handle)


def assign_pid(handle: int, pid: int) -> bool:
    """Add an existing process to a job. Used by the Windows kill-on-close test."""
    if sys.platform != "win32" or not handle or pid <= 0:
        return False
    kernel = _kernel()
    if kernel is None:
        return False
    # PROCESS_SET_QUOTA | PROCESS_TERMINATE, required by AssignProcessToJobObject.
    process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
    if not process:
        return False
    try:
        return bool(kernel.AssignProcessToJobObject(handle, process))
    finally:
        kernel.CloseHandle(process)


def close_handle(handle: int) -> None:
    """Close a job handle. On Windows that can kill members of a kill-on-close job."""
    _close_handle(handle)


def _ensure_job(name: str, *, assign_self: bool, require_member: bool) -> bool:
    with _GUARD:
        if name in _JOBS:
            return True
        handle = create_job(name, assign_self=assign_self, require_member=require_member)
        if not handle:
            _note(f"could not create job {name}")
            return False
        _JOBS[name] = handle
        return True


def _watch_parent() -> None:
    parent = os.getppid()
    if parent <= 0:
        return

    def run() -> None:
        try:
            _wait_for_parent_exit(parent)
        except Exception:
            return
        if pid_alive(parent):
            return
        try:
            from ocarina_forge.formats.meshopt import _stop

            _stop()
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=run, name="forge-parent-watch", daemon=True).start()


def _wait_for_parent_exit(parent: int) -> None:
    if sys.platform == "win32" and _wait_parent_windows(parent):
        return
    if sys.platform.startswith("linux") and _wait_parent_linux(parent):
        return
    while pid_alive(parent):
        time.sleep(0.5)


def _wait_parent_windows(parent: int) -> bool:
    kernel = _kernel()
    if kernel is None:
        return False
    # SYNCHRONIZE. The handle is signaled when that process exits.
    handle = kernel.OpenProcess(0x00100000, False, parent)
    if not handle:
        return not pid_alive(parent)
    try:
        kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
    finally:
        kernel.CloseHandle(handle)
    return True


def _wait_parent_linux(parent: int) -> bool:
    opener = getattr(os, "pidfd_open", None)
    if opener is None:
        return False
    try:
        fd = opener(parent)
    except OSError:
        return not pid_alive(parent)
    try:
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        while pid_alive(parent):
            poller.poll(1000)
        return True
    finally:
        os.close(fd)


def _ppid_from_stat(text: str) -> int | None:
    end = text.rfind(")")
    if end < 0:
        return None
    fields = text[end + 2 :].split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


@dataclass(frozen=True)
class _Raw:
    pid: int
    ppid: int
    cmdline: str
    environ: str
    image: str


def _procfs_raw(root: Path) -> list[_Raw]:
    found = []
    if not root.is_dir():
        return found
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            cmdline = ""
        try:
            environ = (entry / "environ").read_bytes().replace(b"\x00", b"\n").decode("utf-8", "replace")
        except OSError:
            environ = ""
        try:
            ppid = _ppid_from_stat((entry / "stat").read_text(encoding="utf-8", errors="replace")) or 0
        except OSError:
            ppid = 0
        image = ""
        try:
            image = os.readlink(entry / "exe")
        except OSError:
            image = ""
        found.append(_Raw(pid, ppid, cmdline, environ, image))
    return found


def _windows_raw() -> list[_Raw]:
    """Process snapshot. Command lines are read only for Python and its children."""
    kernel = _kernel()
    if kernel is None:
        return []
    rows = _windows_snapshot(kernel)
    python_pids = {pid for pid, _ppid, image in rows if _looks_like_python(image)}
    interesting = set(python_pids)
    for pid, ppid, _image in rows:
        if ppid in python_pids:
            interesting.add(pid)
    found = []
    for pid, ppid, image in rows:
        # Every pid stays in the alive set. Command lines are read only for
        # Python and its children, which is the leftover-worker search.
        if pid in interesting:
            cmdline, environ = _windows_command_and_env(kernel, pid)
        else:
            cmdline, environ = "", ""
        found.append(_Raw(pid, ppid, cmdline, environ, image))
    return found


def _looks_like_python(image: str) -> bool:
    name = Path(image).name.lower()
    return name.startswith("python")


def _windows_snapshot(kernel) -> list[tuple[int, int, str]]:
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snapshot or snapshot == invalid:
        return []
    rows = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        while True:
            rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel.CloseHandle(snapshot)
    return rows


def _windows_command_and_env(kernel, pid: int) -> tuple[str, str]:
    # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return "", ""
    try:
        return _read_peb_strings(kernel, handle)
    except Exception:
        return "", ""
    finally:
        kernel.CloseHandle(handle)


def _read_peb_strings(kernel, handle) -> tuple[str, str]:
    """Command line and environment from a 64-bit PEB. Empty when the read fails."""
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return "", ""
    ntdll = _ntdll()
    if ntdll is None:
        return "", ""

    class PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("ExitStatus", wintypes.LONG),
            ("PebBaseAddress", ctypes.c_void_p),
            ("AffinityMask", ctypes.c_size_t),
            ("BasePriority", wintypes.LONG),
            ("UniqueProcessId", ctypes.c_size_t),
            ("InheritedFromUniqueProcessId", ctypes.c_size_t),
        ]

    info = PROCESS_BASIC_INFORMATION()
    status = ntdll.NtQueryInformationProcess(
        handle,
        0,
        ctypes.byref(info),
        ctypes.sizeof(info),
        None,
    )
    if status != 0 or not info.PebBaseAddress:
        return "", ""
    # PEB.ProcessParameters is the pointer at 0x20 on 64-bit Windows.
    params = _read_ptr(kernel, handle, int(info.PebBaseAddress) + 0x20)
    if not params:
        return "", ""
    # RTL_USER_PROCESS_PARAMETERS.CommandLine is a UNICODE_STRING at 0x70.
    # Environment is the wchar block pointer at 0x80.
    command = _read_remote_unicode(kernel, handle, params + 0x70)
    env_ptr = _read_ptr(kernel, handle, params + 0x80)
    environ = _read_env_block(kernel, handle, env_ptr) if env_ptr else ""
    return command, environ


def _read_ptr(kernel, handle, address: int) -> int:
    raw = _read_remote(kernel, handle, address, ctypes.sizeof(ctypes.c_void_p))
    if not raw or len(raw) < ctypes.sizeof(ctypes.c_void_p):
        return 0
    return int.from_bytes(raw[: ctypes.sizeof(ctypes.c_void_p)], "little")


def _read_remote_unicode(kernel, handle, address: int) -> str:
    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", ctypes.c_ushort),
            ("MaximumLength", ctypes.c_ushort),
            ("Buffer", ctypes.c_void_p),
        ]

    raw = _read_remote(kernel, handle, address, ctypes.sizeof(UNICODE_STRING))
    if not raw:
        return ""
    string = UNICODE_STRING.from_buffer_copy(raw)
    if not string.Buffer or string.Length <= 0:
        return ""
    data = _read_remote(kernel, handle, int(string.Buffer), int(string.Length))
    if not data:
        return ""
    return data.decode("utf-16-le", errors="replace")


def _read_env_block(kernel, handle, address: int) -> str:
    chunks = []
    total = 0
    limit = 256 * 1024
    while total < limit:
        part = _read_remote(kernel, handle, address + total, 4096)
        if not part:
            break
        chunks.append(part)
        total += len(part)
        if b"\x00\x00\x00\x00" in part:
            break
        if len(part) < 4096:
            break
    if not chunks:
        return ""
    return b"".join(chunks).decode("utf-16-le", errors="ignore")


def _read_remote(kernel, handle, address: int, size: int) -> bytes | None:
    if size <= 0 or address <= 0:
        return None
    buf = (ctypes.c_char * size)()
    read = ctypes.c_size_t()
    ok = kernel.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read))
    if not ok or not read.value:
        return None
    return bytes(buf[: read.value])


def _kernel():
    global _KERNEL
    if sys.platform != "win32":
        return None
    if _KERNEL is not None:
        return _KERNEL
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel.SetHandleInformation.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel.ReadProcessMemory.restype = wintypes.BOOL
    _KERNEL = kernel
    return kernel


def _ntdll():
    global _NTDLL
    if sys.platform != "win32":
        return None
    if _NTDLL is not None:
        return _NTDLL
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    _NTDLL = ntdll
    return ntdll


def _open_or_create_job(name: str):
    kernel = _kernel()
    if kernel is None:
        return None
    handle = kernel.CreateJobObjectW(None, name)
    if not handle:
        _note(f"CreateJobObject failed for {name} (winerror {ctypes.get_last_error()})")
        return None
    return handle


def _set_kill_on_close(handle) -> bool:
    kernel = _kernel()
    if kernel is None:
        return False
    info = kill_on_close_limits()
    ok = kernel.SetInformationJobObject(
        handle,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        _note(f"could not set kill-on-close (winerror {ctypes.get_last_error()})")
    return bool(ok)


def _set_not_inherited(handle) -> None:
    kernel = _kernel()
    if kernel is None:
        return
    # HANDLE_FLAG_INHERIT. A child that inherited the handle would keep the job open.
    kernel.SetHandleInformation(handle, 0x00000001, 0)


def _assign_current(handle) -> bool:
    kernel = _kernel()
    if kernel is None:
        return False
    if kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        return True
    _note(f"could not assign this process to the job (winerror {ctypes.get_last_error()})")
    return False


def _close_handle(handle) -> None:
    if not handle or sys.platform != "win32":
        return
    kernel = _kernel()
    if kernel is None:
        return
    kernel.CloseHandle(handle)


def _note(text: str) -> None:
    try:
        sys.stderr.write(f"ocarina-forge: {text}\n")
        sys.stderr.flush()
    except OSError:
        return
