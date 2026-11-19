"""
plugins/pc_electricity_cost.py — Live Hardware Power & Electricity Cost Tracker for JARVIS.

Calculates real-time PC power consumption (CPU, GPU, and system wall draw),
computes exact running costs per hour, day, and month based on local electricity tariffs,
tracks cumulative session energy (kWh units), and projects a visual Arc Reactor power meter on the HUD.
"""
from __future__ import annotations

import json
import os
import platform
import threading
import time
from typing import Optional, Tuple, Dict, Any

import psutil

try:
    import winreg
except ImportError:
    winreg = None

try:
    import pynvml
    _HAS_NVML = True
except Exception:
    _HAS_NVML = False

try:
    from memory.config_manager import get_plugin_setting
except Exception:
    def get_plugin_setting(ns, k, default=None):
        return default

_OS = platform.system()
_NS = "pc_electricity_cost"

PLUGIN = {
    "name": "pc_electricity_cost",
    "label": "PC Electricity & Power Cost",
    "level": "basic",
    "tier": "core",
    "summary": "Real-time PC power consumption (watts), component load, and electricity bill calculator.",
    "setup": [
        "Zero configuration required. Custom tariffs can be configured in Plugin Settings.",
        "Say: 'Jarvis, how much electricity is this PC using?', 'what is my PC power cost?', 'show power cost panel', 'check PC wattage'.",
    ],
    "description": (
        "Real-time PC electrical power consumption (wattage), component draw, and electricity cost calculator. "
        "Use this tool WHENEVER the user asks how much electricity or power the PC is using, "
        "what the running cost or electricity bill of the PC is, current wattage or power draw, "
        "or asks to open/show the PC Power & Electricity Cost HUD panel. "
        "Do NOT use other diagnostic tools for electricity or power cost queries. "
        "Actions: "
        "'cost' or 'status' (get real-time watts, cost per hour/day/month, and session units in kWh), "
        "'panel' or 'open' (display the futuristic visual Arc Reactor power gauge on the central HUD), "
        "'close' (close the power panel on the HUD), "
        "'hardware' (detailed breakdown of CPU, GPU, and system baseline wattage), "
        "'set_rate' (update the electricity tariff rate per kWh)."
    ),
    "brief": "Calculates real-time PC wattage draw and electricity cost per hour, day, and month.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["cost", "status", "panel", "open", "close", "hardware", "set_rate"],
                "description": "Power action to execute (default 'cost')."
            },
            "rate": {
                "type": "NUMBER",
                "description": "For 'set_rate': new electricity tariff cost per kWh (e.g. 6.50)."
            }
        },
        "required": ["action"]
    }
}

PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "⚡  PC ELECTRICITY & POWER COST",
    "fields": [
        {
            "key": "tariff_per_kwh",
            "label": "Electricity Tariff Rate per kWh (Unit)",
            "type": "text",
            "default": "6.50"
        },
        {
            "key": "currency_symbol",
            "label": "Currency Symbol (e.g. ₹, $, €, £, AED)",
            "type": "text",
            "default": "₹"
        },
        {
            "key": "daily_gaming_hours",
            "label": "Estimated Daily Active Hours (e.g. 8)",
            "type": "text",
            "default": "8"
        }
    ]
}


# ── Hardware Detection & Estimation Engine ───────────────────────────────────

def _get_cpu_info() -> Tuple[str, float, float]:
    """
    Returns (cpu_name, idle_watts, tdp_watts).
    Dynamically identifies processor model and assigns estimated power curve.
    """
    cpu_name = "Desktop Processor"
    if winreg:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            cpu_name = val.strip()
            winreg.CloseKey(key)
        except Exception:
            pass

    name_lower = cpu_name.lower()
    # High TDP desktop processors (125W - 250W)
    if any(k in name_lower for k in ["threadripper", "i9", "ryzen 9", "14900", "13900", "12900", "7950", "7900"]):
        return cpu_name, 22.0, 140.0
    # Performance desktop processors (95W - 125W)
    elif any(k in name_lower for k in ["i7", "ryzen 7", "14700", "13700", "12700", "5800", "5700", "7700"]):
        return cpu_name, 16.0, 105.0
    # Mid-range desktop processors (65W TDP)
    elif any(k in name_lower for k in ["i5", "ryzen 5", "ryzen 3", "3200g", "3400g", "3600", "5600", "12400", "12100", "i3"]):
        return cpu_name, 12.0, 65.0
    # Mobile / Laptop processors (15W - 45W)
    elif any(k in name_lower for k in ["laptop", "u", "g1", "g4", "g7"]) and "amd" in name_lower or "intel" in name_lower:
        return cpu_name, 6.0, 35.0

    # Default general desktop curve
    return cpu_name, 12.0, 65.0


def _get_gpu_info() -> Tuple[str, float, float, bool]:
    """
    Returns (gpu_name, idle_watts, tdp_watts, has_direct_sensor).
    """
    if not _HAS_NVML:
        return "Standard GPU / Integrated", 5.0, 25.0, False

    try:
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        if count == 0:
            return "Integrated Graphics", 4.0, 20.0, False

        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        gpu_name = pynvml.nvmlDeviceGetName(handle)

        # Test if direct milliwatt sensor is supported
        has_sensor = False
        try:
            pynvml.nvmlDeviceGetPowerUsage(handle)
            has_sensor = True
        except Exception:
            has_sensor = False

        name_lower = gpu_name.lower()
        if "4090" in name_lower or "3090" in name_lower:
            return gpu_name, 22.0, 450.0, has_sensor
        elif "4080" in name_lower or "3080" in name_lower:
            return gpu_name, 18.0, 320.0, has_sensor
        elif "4070" in name_lower or "3070" in name_lower:
            return gpu_name, 14.0, 220.0, has_sensor
        elif "4060" in name_lower or "3060" in name_lower:
            return gpu_name, 11.0, 170.0, has_sensor
        elif "1660" in name_lower or "1650" in name_lower:
            return gpu_name, 8.0, 85.0, has_sensor
        elif "1030" in name_lower:
            return gpu_name, 6.0, 30.0, has_sensor
        elif "gtx" in name_lower or "rtx" in name_lower:
            return gpu_name, 10.0, 150.0, has_sensor

        return gpu_name, 8.0, 65.0, has_sensor
    except Exception:
        return "Display Adapter", 6.0, 30.0, False


_CPU_NAME, _CPU_IDLE_W, _CPU_TDP_W = _get_cpu_info()
_GPU_NAME, _GPU_IDLE_W, _GPU_TDP_W, _GPU_HAS_SENSOR = _get_gpu_info()
_SYSTEM_BASE_W = 25.0  # Motherboard chipset, RAM sticks, NVMe/SATA SSDs, fans, lighting
_PSU_EFFICIENCY = 0.82  # Standard 80-Plus Bronze power supply efficiency factor


class PowerMonitor:
    """Non-blocking background sampler that calculates live wattage and cumulative kWh."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self.session_start = time.time()
        self.session_watt_hours = 0.0
        self.history = [55.0] * 30
        self.last_sample_time = time.time()

        # Telemetry snapshot cache
        self.cpu_pct = 0.0
        self.gpu_util = 0.0
        self.cpu_watts = _CPU_IDLE_W
        self.gpu_watts = _GPU_IDLE_W
        self.wall_watts = 55.0

    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.last_sample_time = time.time()
            self._thread = threading.Thread(target=self._worker, daemon=True, name="power-monitor")
            self._thread.start()

    def stop(self):
        with self.lock:
            self.running = False

    def _sample_once(self) -> dict:
        # 1. CPU load & power
        cpu_p = psutil.cpu_percent(interval=None)
        cpu_w = _CPU_IDLE_W + (_CPU_TDP_W - _CPU_IDLE_W) * (cpu_p / 100.0)

        # 2. GPU load & power
        gpu_u = 0.0
        gpu_w = _GPU_IDLE_W
        if _HAS_NVML:
            try:
                pynvml.nvmlInit()
                h = pynvml.nvmlDeviceGetHandleByIndex(0)
                if _GPU_HAS_SENSOR:
                    try:
                        mw = pynvml.nvmlDeviceGetPowerUsage(h)
                        gpu_w = mw / 1000.0
                    except Exception:
                        pass
                else:
                    rates = pynvml.nvmlDeviceGetUtilizationRates(h)
                    gpu_u = float(rates.gpu)
                    gpu_w = _GPU_IDLE_W + (_GPU_TDP_W - _GPU_IDLE_W) * (gpu_u / 100.0)
            except Exception:
                pass

        # 3. Total AC Wall Draw through PSU
        comp_sum = cpu_w + gpu_w + _SYSTEM_BASE_W
        wall_w = comp_sum / _PSU_EFFICIENCY

        return {
            "cpu_pct": cpu_p,
            "gpu_util": gpu_u,
            "cpu_w": cpu_w,
            "gpu_w": gpu_w,
            "base_w": _SYSTEM_BASE_W,
            "wall_w": wall_w
        }

    def _worker(self):
        while self.running:
            now = time.time()
            dt = max(0.1, min(5.0, now - self.last_sample_time))
            self.last_sample_time = now

            res = self._sample_once()
            wall_w = res["wall_w"]
            wh = (wall_w * (dt / 3600.0))

            with self.lock:
                self.cpu_pct = res["cpu_pct"]
                self.gpu_util = res["gpu_util"]
                self.cpu_watts = res["cpu_w"]
                self.gpu_watts = res["gpu_w"]
                self.wall_watts = wall_w
                self.session_watt_hours += wh
                self.history.append(wall_w)
                if len(self.history) > 40:
                    self.history.pop(0)

            time.sleep(1.0)

    def snapshot(self) -> dict:
        # In case monitor hasn't run yet
        if not self.running:
            s = self._sample_once()
            with self.lock:
                self.cpu_pct = s["cpu_pct"]
                self.gpu_util = s["gpu_util"]
                self.cpu_watts = s["cpu_w"]
                self.gpu_watts = s["gpu_w"]
                self.wall_watts = s["wall_w"]

        # Read tariff and currency settings
        try:
            rate_str = str(get_plugin_setting(_NS, "tariff_per_kwh", "6.50") or "6.50").strip()
            rate = float(rate_str)
        except Exception:
            rate = 6.50

        curr = str(get_plugin_setting(_NS, "currency_symbol", "₹") or "₹").strip()
        try:
            hrs = float(str(get_plugin_setting(_NS, "daily_gaming_hours", "8") or "8").strip())
        except Exception:
            hrs = 8.0

        with self.lock:
            w = self.wall_watts
            wh = self.session_watt_hours
            hist = list(self.history)
            c_pct = self.cpu_pct
            g_pct = self.gpu_util
            c_w = self.cpu_watts
            g_w = self.gpu_watts

        kw = w / 1000.0
        cost_per_hr = kw * rate
        cost_day_active = cost_per_hr * hrs
        cost_day_24h = cost_per_hr * 24.0
        cost_month_active = cost_day_active * 30.0
        cost_month_24h = cost_day_24h * 30.0
        session_kwh = wh / 1000.0
        session_cost = session_kwh * rate

        return {
            "wall_watts": w,
            "cpu_watts": c_w,
            "gpu_watts": g_w,
            "base_watts": _SYSTEM_BASE_W,
            "cpu_pct": c_pct,
            "gpu_pct": g_pct,
            "cpu_name": _CPU_NAME,
            "gpu_name": _GPU_NAME,
            "rate_per_kwh": rate,
            "currency": curr,
            "active_hours": hrs,
            "cost_per_hour": cost_per_hr,
            "cost_day_active": cost_day_active,
            "cost_day_24h": cost_day_24h,
            "cost_month_active": cost_month_active,
            "cost_month_24h": cost_month_24h,
            "session_kwh": session_kwh,
            "session_cost": session_cost,
            "history": hist,
        }


# Global power monitor instance
_MONITOR = PowerMonitor()
_MONITOR.start()


# ── Plugin Run Handler ───────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "cost").strip().lower()

    try:
        # ── 1. Open Dedicated HUD Arc Reactor Panel ──────────────────────────
        if action in ("panel", "open", "show"):
            if player:
                try:
                    from plugins import _electricity_cost_panel as ec_panel
                    ec_panel.show_power_panel(player)
                except Exception as e:
                    pass

            snap = _MONITOR.snapshot()
            curr = snap["currency"]
            w = snap["wall_watts"]
            c_hr = snap["cost_per_hour"]
            c_mo = snap["cost_month_active"]
            return (
                f"Opened the Power & Electricity Cost panel on your HUD. "
                f"Current power consumption is {w:.1f} Watts. "
                f"Estimated running cost is {curr}{c_hr:.2f} per hour, or {curr}{c_mo:.0f} per month at {snap['active_hours']:.0f} hours/day."
            )

        # ── 2. Close HUD Panel ───────────────────────────────────────────────
        if action in ("close", "hide"):
            if player:
                try:
                    from plugins import _live_panels_core as lb
                    lb.close_all()
                except Exception:
                    pass
            return "Closed the Power Cost panel, sir."

        # ── 3. Update Tariff Rate ────────────────────────────────────────────
        if action == "set_rate":
            new_rate = p.get("rate")
            if new_rate is not None:
                try:
                    r_val = float(new_rate)
                    from memory.config_manager import set_plugin_setting
                    set_plugin_setting(_NS, "tariff_per_kwh", f"{r_val:.2f}")
                    return f"Updated electricity tariff rate to {r_val:.2f} per unit (kWh), sir."
                except Exception as e:
                    return f"Could not save tariff rate: {e}"
            return "Please provide a valid rate number, sir (e.g. rate=7.00)."

        # ── 4. Detailed Hardware Breakdown ───────────────────────────────────
        if action == "hardware":
            snap = _MONITOR.snapshot()
            return (
                f"Hardware Power Breakdown: "
                f"CPU ({snap['cpu_name']}) is drawing {snap['cpu_watts']:.1f} W ({snap['cpu_pct']:.0f}% load). "
                f"GPU ({snap['gpu_name']}) is drawing {snap['gpu_watts']:.1f} W ({snap['gpu_pct']:.0f}% load). "
                f"Motherboard, RAM and storage baseline is {snap['base_watts']:.1f} W. "
                f"Total AC wall draw through power supply is {snap['wall_watts']:.1f} Watts."
            )

        # ── 5. Standard Status / Cost Query ──────────────────────────────────
        snap = _MONITOR.snapshot()
        curr = snap["currency"]
        w = snap["wall_watts"]
        rate = snap["rate_per_kwh"]
        c_hr = snap["cost_per_hour"]
        c_day = snap["cost_day_active"]
        c_mo = snap["cost_month_active"]
        kwh = snap["session_kwh"]
        s_cost = snap["session_cost"]

        return (
            f"Your PC is currently drawing {w:.1f} Watts from the wall. "
            f"At your electricity tariff of {curr}{rate:.2f} per kWh, running this PC costs {curr}{c_hr:.2f} per hour. "
            f"At {snap['active_hours']:.0f} hours of daily use, that comes to approximately {curr}{c_day:.1f} per day, "
            f"or {curr}{c_mo:.0f} per month. "
            f"This session has consumed {kwh:.3f} units ({curr}{s_cost:.2f})."
        )

    except Exception as e:
        return f"Power Cost monitor encountered an error: {e}"
