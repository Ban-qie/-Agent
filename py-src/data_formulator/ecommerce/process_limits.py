"""Windows Jobs or bounded Linux containers plus per-worker address-space limits."""
import ctypes
from ctypes import wintypes
import os
import sys


def _linux_limits(process, memory_bytes):
    import resource
    import signal
    from pathlib import Path

    # A worker limit alone cannot bound the sum of the application and descendants.
    # Require the production container's cgroup v2 memory and PID ceilings.
    root = Path('/sys/fs/cgroup')
    memory = (root / 'memory.max').read_text().strip()
    pids = (root / 'pids.max').read_text().strip()
    if memory == 'max' or not 0 < int(memory) <= 1024 ** 3:
        raise RuntimeError('Linux application requires a cgroup memory ceiling <= 1 GiB')
    if pids == 'max' or not 0 < int(pids) <= 64:
        raise RuntimeError('Linux application requires a cgroup PID ceiling <= 64')
    if os.getpgid(process.pid) != process.pid or os.getsid(process.pid) != process.pid:
        raise RuntimeError('Linux worker requires its own session and process group')
    resource.prlimit(process.pid, resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.prlimit(process.pid, resource.RLIMIT_CORE, (0, 0))

    def close():
        # Also kill children retaining pipes after the group leader exits.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    return close


def constrain_process(process, memory_bytes=512 * 1024 * 1024, *, active_processes=2):
    if sys.platform == 'linux':
        return _linux_limits(process, memory_bytes)
    if os.name != "nt":
        raise RuntimeError("This V0 worker limit implementation is verified on Windows only")
    if not isinstance(active_processes, int) or active_processes < 1:
        raise ValueError("active_processes must be a positive integer")
    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = EXTENDED()
    # uv's Windows venv executable may use a trampoline and interpreter child;
    # callers that exercise nested workers must budget those processes explicitly.
    limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x8 | 0x100 | 0x200
    limits.BasicLimitInformation.ActiveProcessLimit = active_processes
    limits.ProcessMemoryLimit = memory_bytes
    limits.JobMemoryLimit = memory_bytes
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
        code = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise ctypes.WinError(code)
    def close():
        kernel.CloseHandle(job)

    def peak_memory_bytes():
        observed = EXTENDED()
        returned = wintypes.DWORD()
        if not kernel.QueryInformationJobObject(job, 9, ctypes.byref(observed), ctypes.sizeof(observed),
                                                ctypes.byref(returned)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(observed.PeakJobMemoryUsed)

    close.peak_memory_bytes = peak_memory_bytes
    return close
