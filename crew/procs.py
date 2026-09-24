"""Processes: is a recorded worker still the process it was, and running a
command so that it and everything it starts can be stopped together.

A pid alone is not an identity: once a worker dies without cleaning up, its
pid can be handed to an unrelated process (quickly, on Windows). A worker's
lock therefore records the pid with the process's start time, and nothing is
signalled unless both still match.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess

STILL_ACTIVE = 259
QUERY_LIMITED = 0x1000   # PROCESS_QUERY_LIMITED_INFORMATION


def _windows_identity(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(QUERY_LIMITED, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return None
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return ""
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def identity(pid: int) -> str | None:
    """None when no process has this pid; otherwise a string that changes when
    the pid is reused ("" when the platform cannot tell)."""
    if pid <= 0:
        return None
    if os.name == "nt":
        # Never os.kill(pid, 0) here: on Windows that terminates the process.
        return _windows_identity(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    stat = pathlib.Path(f"/proc/{pid}/stat")
    try:
        # Field 22, the start time; fields are counted after the parenthesised name.
        return stat.read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        pass
    try:
        return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def alive(pid: int, recorded: str | None = None) -> bool:
    """Whether pid is running and, when an identity was recorded, is still that process."""
    current = identity(pid)
    if current is None:
        return False
    return not (recorded and current and current != recorded)


def kill_tree(pid: int) -> None:
    """Stops a process and everything it started."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        return
    try:
        os.killpg(pid, signal.SIGTERM) if os.getpgid(pid) == pid else os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def run(command: str, cwd: pathlib.Path, timeout: int) -> subprocess.CompletedProcess:
    """subprocess.run(shell=True), but in its own process group, so a timeout,
    a cancel or an interrupt stops the build or test it started, not only the shell."""
    options: dict = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    process = subprocess.Popen(command, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace", **options)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        kill_tree(process.pid)
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
