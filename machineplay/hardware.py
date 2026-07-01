"""Runner hardware detection and live utilization sampling.

`read_hardware()` is called once at connect to fill the Introduction's static
`HardwareInfo`; `read_telemetry()` is sampled on a timer for the live feed. CPU
and RAM come from psutil; the CPU model name is read from ``/proc/cpuinfo`` since
psutil doesn't expose it (falling back to :func:`platform.processor`). GPU stats
are intentionally not collected yet — the schema leaves room to add them.
"""

import platform

import psutil

from machineplay.schemas import HardwareInfo, Telemetry


def _cpu_model() -> str:
    """Human-readable CPU model, e.g. "AMD Ryzen 9 5950X 16-Core Processor"."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def read_hardware() -> HardwareInfo:
    """Static hardware description reported once in the Introduction."""
    return HardwareInfo(
        cpu_model=_cpu_model(),
        cpu_physical_cores=psutil.cpu_count(logical=False) or 0,
        cpu_logical_cores=psutil.cpu_count(logical=True) or 0,
        ram_total_bytes=psutil.virtual_memory().total,
    )


def prime_cpu_percent() -> None:
    """Seed psutil's CPU percent baseline so the first real sample isn't 0.0.

    ``psutil.cpu_percent(interval=None)`` measures usage since the previous call;
    the very first call has no baseline and returns 0.0. Call this once before
    the telemetry loop starts.
    """
    psutil.cpu_percent(interval=None)


def read_telemetry() -> Telemetry:
    """Current CPU/RAM utilization for the live feed.

    CPU percent is the average over the interval since the last call (whether
    :func:`prime_cpu_percent` or the previous :func:`read_telemetry`).
    """
    vm = psutil.virtual_memory()
    return Telemetry(
        cpu_percent=psutil.cpu_percent(interval=None),
        ram_used_bytes=vm.used,
        ram_percent=vm.percent,
    )
