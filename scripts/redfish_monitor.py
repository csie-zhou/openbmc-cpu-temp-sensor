#!/usr/bin/env python3
"""
redfish_monitor.py — OpenBMC Redfish Live Sensor Dashboard
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Polls a QEMU-emulated OpenBMC Romulus BMC via the Redfish API
and renders a real-time terminal dashboard showing CPU temperature
and CPU frequency with sparkline history and health indicators.

Usage:
    python3 redfish_monitor.py [--host localhost] [--port 2443]

Requirements: Python 3.6+, no third-party dependencies.
Tested against OpenBMC bmcweb on QEMU Romulus (AST2500).
"""

import argparse
import base64
import collections
import json
import os
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Deque, Optional, Tuple

# ── Configuration ─────────────────────────────────────────────────────────────

POLL_INTERVAL = 2          # seconds between polls
HISTORY_LEN   = 55         # number of samples kept (= terminal width - margin)

TEMP_MIN, TEMP_MAX = 30.0, 85.0    # °C display range
FREQ_MIN, FREQ_MAX = 400.0, 1400.0 # MHz display range

TEMP_WARN  = 65.0   # °C — yellow zone
TEMP_CRIT  = 78.0   # °C — red zone
FREQ_WARN  = 900.0  # MHz — shows system is under load
FREQ_CRIT  = 1200.0 # MHz

# ── ANSI helpers ───────────────────────────────────────────────────────────────

ESC = "\033"

def fg(r, g, b)  -> str: return f"{ESC}[38;2;{r};{g};{b}m"
def bg(r, g, b)  -> str: return f"{ESC}[48;2;{r};{g};{b}m"
def bold()       -> str: return f"{ESC}[1m"
def dim()        -> str: return f"{ESC}[2m"
def reset()      -> str: return f"{ESC}[0m"
def clearscreen()-> str: return f"{ESC}[2J{ESC}[H"
def hide_cursor(): print(f"{ESC}[?25l", end="", flush=True)
def show_cursor(): print(f"{ESC}[?25h", end="", flush=True)

# Palette — terminal-safe 24-bit colours
C_TEAL   = fg(32, 178, 140)
C_AMBER  = fg(255, 180, 50)
C_RED    = fg(220, 70, 70)
C_BLUE   = fg(100, 160, 255)
C_WHITE  = fg(230, 230, 230)
C_MUTED  = fg(110, 110, 110)
C_BORDER = fg(60, 65, 75)
C_BG_HDR = bg(18, 22, 30)
C_BG_BOX = bg(22, 26, 35)

def health_color(val: float, warn: float, crit: float) -> str:
    if val >= crit:  return C_RED
    if val >= warn:  return C_AMBER
    return C_TEAL

def health_label(val: float, warn: float, crit: float) -> str:
    if val >= crit:  return f"{C_RED}CRITICAL{reset()}"
    if val >= warn:  return f"{C_AMBER}WARNING {reset()}"
    return f"{C_TEAL}OK      {reset()}"

# ── Sparkline ─────────────────────────────────────────────────────────────────

SPARK_CHARS = " ▁▂▃▄▅▆▇█"

def sparkline(history: Deque, vmin: float, vmax: float, width: int) -> str:
    samples = list(history)[-width:]
    out = ""
    for v in samples:
        if v is None:
            out += f"{C_MUTED}·{reset()}"
            continue
        idx = int((v - vmin) / (vmax - vmin) * (len(SPARK_CHARS) - 1))
        idx = max(0, min(idx, len(SPARK_CHARS) - 1))
        color = health_color(v, *((TEMP_WARN, TEMP_CRIT)
                                  if vmax == TEMP_MAX else (FREQ_WARN, FREQ_CRIT)))
        out += f"{color}{SPARK_CHARS[idx]}{reset()}"
    # Pad left if not enough history yet
    pad = width - len(samples)
    return f"{C_MUTED}{' ' * pad}{reset()}" + out

# ── Progress bar ──────────────────────────────────────────────────────────────

def bar(val: Optional[float], vmin: float, vmax: float,
        warn: float, crit: float, width: int = 40) -> str:
    if val is None:
        return f"{C_MUTED}{'─' * width}  N/A{reset()}"
    pct    = (val - vmin) / (vmax - vmin)
    pct    = max(0.0, min(1.0, pct))
    filled = int(pct * width)
    empty  = width - filled
    color  = health_color(val, warn, crit)
    bar_s  = f"{color}{'█' * filled}{C_MUTED}{'░' * empty}{reset()}"
    return bar_s

# ── Redfish client ────────────────────────────────────────────────────────────

class RedfishClient:
    def __init__(self, host: str, port: int, user: str, password: str):
        self.base    = f"https://{host}:{port}"
        self.headers = {
            "Authorization": "Basic " + base64.b64encode(
                f"{user}:{password}".encode()).decode(),
            "Accept": "application/json",
        }
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode    = ssl.CERT_NONE

    def get(self, path: str) -> Optional[dict]:
        url = self.base + path
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=3) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    def fetch_sensor(self, chassis: str, sensor_path: str) -> Optional[float]:
        path = f"/redfish/v1/Chassis/{chassis}/Sensors/{sensor_path}"
        data = self.get(path)
        if data and "Reading" in data:
            return float(data["Reading"])
        return None

# ── Stats tracker ─────────────────────────────────────────────────────────────

class SensorStats:
    def __init__(self):
        self.values: Deque[Optional[float]] = collections.deque(maxlen=HISTORY_LEN)
        self.min_seen: Optional[float] = None
        self.max_seen: Optional[float] = None
        self.errors   = 0
        self.polls    = 0

    def record(self, val: Optional[float]):
        self.polls += 1
        self.values.append(val)
        if val is None:
            self.errors += 1
            return
        if self.min_seen is None or val < self.min_seen: self.min_seen = val
        if self.max_seen is None or val > self.max_seen: self.max_seen = val

    @property
    def current(self) -> Optional[float]:
        for v in reversed(self.values):
            if v is not None: return v
        return None

    @property
    def avg(self) -> Optional[float]:
        valid = [v for v in self.values if v is not None]
        return sum(valid) / len(valid) if valid else None

# ── Dashboard renderer ────────────────────────────────────────────────────────

def render(temp: SensorStats, freq: SensorStats,
           uptime: int, poll_count: int, bmc_host: str):

    W = 72   # total dashboard width
    now = datetime.now().strftime("%H:%M:%S")

    lines = []

    def line(s=""): lines.append(s)
    def hline(char="─"):
        lines.append(f"{C_BORDER}{char * W}{reset()}")
    def section(title: str):
        pad = W - len(title) - 4
        lines.append(
            f"{C_BORDER}┌─{reset()}{bold()}{C_WHITE} {title} "
            f"{reset()}{C_BORDER}{'─' * pad}┐{reset()}"
        )

    def end_section():
        lines.append(f"{C_BORDER}└{'─' * (W - 2)}┘{reset()}")

    def row(label: str, content: str, indent: int = 2):
        lines.append(f"{C_BORDER}│{reset()}{' ' * indent}"
                     f"{C_MUTED}{label:<14}{reset()}{content}"
                     f"{C_BORDER}│{reset()}")

    def blank_row():
        lines.append(f"{C_BORDER}│{' ' * (W - 2)}│{reset()}")

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(
        f"{bold()}{C_TEAL}  ██████╗ ███████╗██████╗ ███████╗██╗███████╗██╗  ██╗{reset()}"
    )
    lines.append(
        f"{bold()}{C_TEAL}  ██╔══██╗██╔════╝██╔══██╗██╔════╝██║██╔════╝██║  ██║{reset()}"
    )
    lines.append(
        f"{bold()}{C_TEAL}  ██████╔╝█████╗  ██║  ██║█████╗  ██║███████╗███████║{reset()}"
    )
    lines.append(
        f"{bold()}{C_TEAL}  ██╔══██╗██╔══╝  ██║  ██║██╔══╝  ██║╚════██║██╔══██║{reset()}"
    )
    lines.append(
        f"{bold()}{C_TEAL}  ██║  ██║███████╗██████╔╝██║     ██║███████║██║  ██║{reset()}"
    )
    lines.append(
        f"{bold()}{C_TEAL}  ╚═╝  ╚═╝╚══════╝╚═════╝ ╚═╝     ╚═╝╚══════╝╚═╝  ╚═╝{reset()}"
    )
    lines.append(
        f"  {C_MUTED}OpenBMC · Redfish Sensor Monitor · "
        f"bmcweb @ {bmc_host}{reset()}"
    )
    line()
    lines.append(
        f"  {C_MUTED}{'─' * 20}{reset()}"
        f"  {C_WHITE}Polls: {bold()}{poll_count}{reset()}"
        f"  {C_MUTED}│{reset()}"
        f"  {C_WHITE}Uptime: {bold()}{uptime // 60:02d}:{uptime % 60:02d}{reset()}"
        f"  {C_MUTED}│{reset()}"
        f"  {C_WHITE}Time: {bold()}{now}{reset()}"
    )
    line()

    # ── Temperature section ───────────────────────────────────────────────────
    t_cur = temp.current
    t_color = health_color(t_cur, TEMP_WARN, TEMP_CRIT) if t_cur else C_MUTED

    section("CPU TEMPERATURE  /redfish/v1/Chassis/chassis/Sensors/temperature/cpu_temp")
    blank_row()

    # Big reading
    reading_str = f"{t_cur:>6.1f} °C" if t_cur is not None else "  N/A   "
    row("Reading",
        f"{bold()}{t_color}{reading_str}{reset()}"
        f"   {health_label(t_cur or 0, TEMP_WARN, TEMP_CRIT)}"
        f"   {C_MUTED}warn≥{TEMP_WARN}°C  crit≥{TEMP_CRIT}°C{reset()}")

    # Bar
    bar_str = bar(t_cur, TEMP_MIN, TEMP_MAX, TEMP_WARN, TEMP_CRIT, width=40)
    row("",
        f"{bar_str}  "
        f"{C_MUTED}{TEMP_MIN}°C {'─' * 3} {TEMP_MAX}°C{reset()}")

    blank_row()

    # Sparkline
    spark_t = sparkline(temp.values, TEMP_MIN, TEMP_MAX, W - 20)
    row("History", f"{spark_t}")

    blank_row()

    # Stats row
    t_min  = f"{temp.min_seen:.1f}" if temp.min_seen else "─"
    t_max  = f"{temp.max_seen:.1f}" if temp.max_seen else "─"
    t_avg  = f"{temp.avg:.1f}"      if temp.avg      else "─"
    row("Session",
        f"{C_MUTED}min {C_BLUE}{t_min}°C{reset()}"
        f"  {C_MUTED}avg {C_WHITE}{t_avg}°C{reset()}"
        f"  {C_MUTED}max {C_RED}{t_max}°C{reset()}"
        f"  {C_MUTED}errors {temp.errors}/{temp.polls}{reset()}")

    blank_row()
    end_section()
    line()

    # ── Frequency section ─────────────────────────────────────────────────────
    f_cur = freq.current
    f_color = health_color(f_cur, FREQ_WARN, FREQ_CRIT) if f_cur else C_MUTED

    section("CPU FREQUENCY    /redfish/v1/Chassis/chassis/Sensors/frequency/cpu_freq")
    blank_row()

    freq_str = f"{f_cur:>7.1f} MHz" if f_cur is not None else "   N/A    "
    row("Reading",
        f"{bold()}{f_color}{freq_str}{reset()}"
        f"   {health_label(f_cur or 0, FREQ_WARN, FREQ_CRIT)}"
        f"   {C_MUTED}warn≥{FREQ_WARN:.0f}  crit≥{FREQ_CRIT:.0f} MHz{reset()}")

    bar_str_f = bar(f_cur, FREQ_MIN, FREQ_MAX, FREQ_WARN, FREQ_CRIT, width=40)
    row("",
        f"{bar_str_f}  "
        f"{C_MUTED}{FREQ_MIN:.0f} {'─' * 3} {FREQ_MAX:.0f} MHz{reset()}")

    blank_row()

    spark_f = sparkline(freq.values, FREQ_MIN, FREQ_MAX, W - 20)
    row("History", f"{spark_f}")

    blank_row()

    f_min  = f"{freq.min_seen:.1f}" if freq.min_seen else "─"
    f_max  = f"{freq.max_seen:.1f}" if freq.max_seen else "─"
    f_avg  = f"{freq.avg:.1f}"      if freq.avg      else "─"
    row("Session",
        f"{C_MUTED}min {C_BLUE}{f_min} MHz{reset()}"
        f"  {C_MUTED}avg {C_WHITE}{f_avg} MHz{reset()}"
        f"  {C_MUTED}max {C_RED}{f_max} MHz{reset()}"
        f"  {C_MUTED}errors {freq.errors}/{freq.polls}{reset()}")

    blank_row()
    end_section()
    line()

    # ── Footer ────────────────────────────────────────────────────────────────
    lines.append(
        f"  {C_MUTED}Endpoint:  {bmc_host}/redfish/v1/Chassis/chassis/Sensors/{reset()}"
    )
    lines.append(
        f"  {C_MUTED}Mock sensor: /tmp/mock_thermal.sh → /tmp/cpu_temp, /tmp/cpu_freq{reset()}"
    )
    lines.append(
        f"  {C_MUTED}Polling every {POLL_INTERVAL}s · Ctrl-C to exit{reset()}"
    )

    # ── Render ────────────────────────────────────────────────────────────────
    print(clearscreen() + "\n".join(lines), flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="OpenBMC Redfish live sensor dashboard"
    )
    parser.add_argument("--host",     default="localhost")
    parser.add_argument("--port",     default=2443, type=int)
    parser.add_argument("--user",     default="root")
    parser.add_argument("--password", default="0penBmc")
    parser.add_argument("--chassis",  default="chassis")
    args = parser.parse_args()

    client = RedfishClient(args.host, args.port, args.user, args.password)
    temp   = SensorStats()
    freq   = SensorStats()

    start_time  = time.time()
    poll_count  = 0
    bmc_addr    = f"https://{args.host}:{args.port}"

    hide_cursor()

    def cleanup(sig=None, frame=None):
        show_cursor()
        print(f"\n{C_MUTED}  Dashboard stopped.{reset()}\n")
        sys.exit(0)

    signal.signal(signal.SIGINT,  cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    while True:
        t_val = client.fetch_sensor(
            args.chassis, "temperature/cpu_temp")
        f_val = client.fetch_sensor(
            args.chassis, "frequency/cpu_freq")

        temp.record(t_val)
        freq.record(f_val)
        poll_count += 1

        uptime = int(time.time() - start_time)
        render(temp, freq, uptime, poll_count, bmc_addr)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
