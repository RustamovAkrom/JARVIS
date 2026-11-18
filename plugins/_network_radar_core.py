"""
Network Radar's core — who is on the network, how fast the connection is,
drawn as a live, clickable radar where the avatar sits.

A shared driver for network_radar.py (the leading underscore keeps the plugin
loader from treating it as a skill). It builds on Live Panels' frame
(_live_panels_core.py: the backdrop, header, ×, fade, scaling and placement on
the HUD), so it opens, closes and gives way exactly like the other live panels.

    The radar   this computer in the middle; every device on the network is a
                blip, as far out as it is slow to answer (1 ms, 10 ms, 100 ms
                rings). The sweep lights each one as it passes. A device never
                seen on this network before is marked NEW. Click one for its
                details; right-click or Esc clears the selection.
    The side    the link (Wi-Fi name, signal, band — or Ethernet and its speed),
                the internet provider, every device with its maker and address,
                and below: live latency to the router and to the internet, a
                speed test (download, upload, ping and ping under load), and
                the selected device's details (TRUST, OPEN WEB, COPY IP).

HOW DEVICES ARE FOUND (the helper process, standard library + ctypes)
    Every address of the local network (a /24 at most) is asked for its
    hardware address with ARP — phones that ignore ping still answer ARP. Each
    device found is then pinged and asked for its name (reverse DNS, NetBIOS,
    multicast DNS) and a handful of well-known ports are tried (printer, cast,
    AirPlay, iPhone sync, web…). UPnP and multicast DNS announcements give
    friendly names ("Living Room TV"). The maker comes from the first half of
    the hardware address, looked up in the Wireshark project's public list,
    downloaded once into memory/. Nothing is changed on any device, and nothing
    is sent anywhere but this network — except the speed test (Cloudflare's
    public speed servers) and one look-up of the provider's name (ipinfo.io).

WHY A HELPER PROCESS
    The same reason as Disk Map: hundreds of blocking network calls would hold
    Python's interpreter lock, and JARVIS's voice is written to the speaker
    from Python too. The helper has its own lock, runs at below-normal
    priority, and ends by itself when JARVIS does (its stdin closes). If it
    cannot start, the same code runs in a thread here.

WHAT IS REMEMBERED (memory/network_radar_cache.json)
    Per network (keyed by the router's hardware address, so home and office
    never mix): every device's hardware address, when it was first and last
    seen, whether the user trusts it and the name they gave it. The first scan
    of a network learns what is there; after that, anything new is NEW until
    the user trusts or names it.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import zlib
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6 import sip
from PyQt6.QtCore import QPointF, QRectF, Qt
from core.runtime_paths import PATHS
from PyQt6.QtGui import (QBrush, QConicalGradient, QFontMetricsF, QGuiApplication, QPainter,
                         QPainterPath, QPen, QPixmap, QRadialGradient)

try:
    from plugins import _live_panels_core as lb
except ImportError:  # loaded outside the package
    import _live_panels_core as lb  # type: ignore

_q, _mix, _once = lb._q, lb._mix, lb._once
BG, TEXT, DIM, WARN, HOT = lb.BG, lb.TEXT, lb.DIM, lb.WARN, lb.HOT
UP_COL = (186, 120, 255)                       # upload, the internet line

MEMORY_DIR = PATHS.memory_dir
STORE_FILE = MEMORY_DIR / "network_radar_cache.json"
OUI_FILE = MEMORY_DIR / "network_radar_oui_cache.json"

# Layout on the 1600 × 1000 canvas
RC = QPointF(455.0, 582.0)                     # the radar's centre
RR = 305.0                                     # and radius
RX0, RX1 = 862.0, 1530.0                       # the side column
LIST_Y, ROW_H, ROWS = 346.0, 46.0, 7
TAB_Y = 690.0
LAT_MAX = 300.0                                # ms at the radar's edge


# ─────────────────────────────────────────────────────────────────────────────
# The helper (runs as its own process; see the module docstring)
# ─────────────────────────────────────────────────────────────────────────────

_AGENT_SCRIPT = r'''
import ipaddress, json, os, random, re, socket, struct, subprocess, sys, threading, time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

WIN = os.name == "nt"
UA = "Mozilla/5.0 (JARVIS network radar)"
PORTS = (22, 80, 443, 445, 548, 554, 631, 1883, 3389, 5000, 7000, 8008, 8009, 8060, 8080,
         9100, 32400, 62078)
SERVICES = ("_googlecast._tcp", "_airplay._tcp", "_raop._tcp", "_ipp._tcp", "_printer._tcp",
            "_spotify-connect._tcp", "_hap._tcp", "_companion-link._tcp", "_device-info._tcp",
            "_androidtvremote2._tcp", "_sonos._tcp", "_smb._tcp", "_workstation._tcp",
            "_amzn-wplay._tcp", "_hue._tcp", "_miio._udp", "_mqtt._tcp", "_rdlink._tcp")
NO_WIN = 0x08000000


def _q(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception:
        return None


def _dw(ip):
    return struct.unpack("<I", socket.inet_aton(ip))[0]


def _ip(dw):
    return socket.inet_ntoa(struct.pack("<I", dw))


if WIN:
    import ctypes
    from ctypes import wintypes
    IPH = ctypes.WinDLL("iphlpapi")
    IPH.IcmpCreateFile.restype = wintypes.HANDLE
    IPH.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
    IPH.IcmpSendEcho.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.WORD,
                                 ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    IPH.IcmpSendEcho.restype = wintypes.DWORD
    IPH.SendARP.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            ctypes.POINTER(wintypes.ULONG)]
    IPH.SendARP.restype = wintypes.DWORD

    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                    ("d", ctypes.c_ubyte * 8)]

    class ROUTE(ctypes.Structure):
        _fields_ = [(n, wintypes.DWORD) for n in ("dest", "mask", "policy", "hop", "ifi", "type",
                                                   "proto", "age", "nhas", "m1", "m2", "m3",
                                                   "m4", "m5")]

    class IFROW2(ctypes.Structure):
        _fields_ = [("luid", ctypes.c_uint64), ("index", wintypes.ULONG), ("guid", GUID),
                    ("alias", ctypes.c_wchar * 257), ("desc", ctypes.c_wchar * 257),
                    ("palen", wintypes.ULONG), ("pa", ctypes.c_ubyte * 32),
                    ("ppa", ctypes.c_ubyte * 32), ("mtu", wintypes.ULONG),
                    ("type", wintypes.ULONG), ("tunnel", wintypes.DWORD),
                    ("media", wintypes.DWORD), ("phys", wintypes.DWORD),
                    ("access", wintypes.DWORD), ("direction", wintypes.DWORD),
                    ("flags", ctypes.c_ubyte), ("oper", wintypes.DWORD),
                    ("admin", wintypes.DWORD), ("mstate", wintypes.DWORD),
                    ("netguid", GUID), ("ctype", wintypes.DWORD),
                    ("tx", ctypes.c_uint64), ("rx", ctypes.c_uint64),
                    ("rest", ctypes.c_ubyte * 1024)]

    class IFINFO(ctypes.Structure):
        _fields_ = [("guid", GUID), ("desc", ctypes.c_wchar * 256), ("state", wintypes.DWORD)]

    class IFLIST(ctypes.Structure):
        _fields_ = [("n", wintypes.DWORD), ("i", wintypes.DWORD), ("items", IFINFO * 32)]

    class SSID(ctypes.Structure):
        _fields_ = [("len", wintypes.ULONG), ("ssid", ctypes.c_ubyte * 32)]

    class ASSOC(ctypes.Structure):
        _fields_ = [("ssid", SSID), ("bsstype", wintypes.DWORD), ("bssid", ctypes.c_ubyte * 6),
                    ("phy", wintypes.DWORD), ("phyidx", wintypes.ULONG),
                    ("quality", wintypes.ULONG), ("rx", wintypes.ULONG), ("tx", wintypes.ULONG)]

    class SEC(ctypes.Structure):
        _fields_ = [("sec", wintypes.BOOL), ("onex", wintypes.BOOL), ("auth", wintypes.DWORD),
                    ("cipher", wintypes.DWORD)]

    class CONN(ctypes.Structure):
        _fields_ = [("state", wintypes.DWORD), ("mode", wintypes.DWORD),
                    ("profile", ctypes.c_wchar * 256), ("assoc", ASSOC), ("sec", SEC)]


# ── ping / ARP ──────────────────────────────────────────────────────────────
def ping(ip, timeout=1000):
    """Round trip in ms, None when there is no answer."""
    if WIN:
        h = IPH.IcmpCreateFile()
        try:
            req = b"JARVIS-network-radar-0123456789"
            rep = ctypes.create_string_buffer(256)
            t0 = time.perf_counter()
            n = IPH.IcmpSendEcho(h, _dw(ip), req, len(req), None, rep, len(rep), timeout)
            dt = (time.perf_counter() - t0) * 1000
            if not n:
                return None
            addr, status, rtt = struct.unpack_from("<III", rep, 0)
            if status != 0:
                return None
            return float(rtt) if rtt >= 1 else round(max(0.05, min(dt, 0.99)), 2)
        finally:
            IPH.IcmpCloseHandle(h)
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", str(max(1, timeout // 1000)), ip],
                           capture_output=True, text=True, timeout=timeout / 1000 + 1.5)
        m = re.search(r"time[=<]\s*([\d.]+)", r.stdout)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def _neigh():
    out = {}
    txt = ""
    for cmd in (["ip", "neigh"], ["arp", "-an"]):
        try:
            txt = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
            if txt:
                break
        except Exception:
            continue
    for line in txt.splitlines():
        ip = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
        mac = re.search(r"(([0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2})", line)
        if ip and mac:
            out[ip.group(1)] = ":".join("%02X" % int(x, 16) for x in re.split("[:-]", mac.group(1)))
    return out


def arp(ip):
    """(hardware address, ms) of whoever has `ip`, None if nobody answers."""
    if WIN:
        mac = (ctypes.c_ubyte * 8)()
        ln = wintypes.ULONG(8)
        t0 = time.perf_counter()
        rc = IPH.SendARP(_dw(ip), 0, mac, ctypes.byref(ln))
        if rc != 0 or ln.value < 6:
            return None
        m = ":".join("%02X" % b for b in mac[:6])
        if m in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
            return None
        return m, round((time.perf_counter() - t0) * 1000, 2)
    ms = ping(ip, 800)
    m = _neigh().get(ip)
    return (m, ms or 0.0) if m else None


def arp_cache(ifindex):
    """Addresses this computer has talked to lately: asked first, they answer fast."""
    out = []
    if WIN:
        try:
            size = wintypes.ULONG(0)
            IPH.GetIpNetTable(None, ctypes.byref(size), False)
            buf = ctypes.create_string_buffer(size.value + 240)
            size = wintypes.ULONG(len(buf))
            if IPH.GetIpNetTable(buf, ctypes.byref(size), False) == 0:
                n = struct.unpack_from("<I", buf, 0)[0]
                for i in range(n):
                    idx, ln, mac, addr, typ = struct.unpack_from("<II8sII", buf, 4 + i * 24)
                    if typ == 3 and ln == 6 and (ifindex is None or idx == ifindex):
                        out.append(_ip(addr))
        except Exception:
            pass
    else:
        out = list(_neigh())
    return out


# ── where this computer is ──────────────────────────────────────────────────
def _wifi(guid):
    w = ctypes.WinDLL("wlanapi")
    h = wintypes.HANDLE()
    neg = wintypes.DWORD()
    if w.WlanOpenHandle(2, None, ctypes.byref(neg), ctypes.byref(h)) != 0:
        return {}
    try:
        lst = ctypes.POINTER(IFLIST)()
        if w.WlanEnumInterfaces(h, None, ctypes.byref(lst)) != 0 or not lst:
            return {}
        pick = None
        try:
            for i in range(min(lst.contents.n, 32)):
                it = lst.contents.items[i]
                if it.state == 1 and (pick is None or bytes(it.guid) == guid):
                    pick = GUID.from_buffer_copy(bytes(it.guid))
        finally:
            w.WlanFreeMemory(lst)
        if pick is None:
            return {}
        out = {}
        size = wintypes.DWORD()
        data = ctypes.c_void_p()
        if w.WlanQueryInterface(h, ctypes.byref(pick), 7, None, ctypes.byref(size),
                                ctypes.byref(data), None) == 0 and data.value:
            c = CONN.from_address(data.value)
            a = c.assoc
            out["ssid"] = bytes(a.ssid.ssid[:min(32, a.ssid.len)]).decode("utf-8", "replace")
            out["signal"] = int(a.quality)
            out["rx"] = round(a.rx / 1000)
            out["tx"] = round(a.tx / 1000)
            out["phy"] = {7: "Wi-Fi 4", 8: "Wi-Fi 5", 10: "Wi-Fi 6", 11: "Wi-Fi 7"}.get(a.phy, "")
            out["secure"] = bool(c.sec.sec)
            w.WlanFreeMemory(data)
        for op, key in ((8, "channel"), (0x10000102, "rssi")):
            data = ctypes.c_void_p()
            if w.WlanQueryInterface(h, ctypes.byref(pick), op, None, ctypes.byref(size),
                                    ctypes.byref(data), None) == 0 and data.value:
                out[key] = ctypes.c_long.from_address(data.value).value
                w.WlanFreeMemory(data)
        ch = out.get("channel") or 0
        out["band"] = "2.4 GHz" if 1 <= ch <= 14 else ("5 GHz" if 32 <= ch <= 177 else "")
        return out
    finally:
        w.WlanCloseHandle(h, None)


def route_info():
    info = {"me": None, "gw": None, "prefix": 24, "net": None, "kind": "other", "iface": "",
            "desc": "", "speed": 0, "ifindex": None, "wifi": {}}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 53))                  # no packet is sent: only a route is chosen
        info["me"] = s.getsockname()[0]
    except OSError:
        pass
    finally:
        s.close()
    guid = None
    if WIN:
        try:
            r = ROUTE()
            if IPH.GetBestRoute(_dw("1.1.1.1"), 0, ctypes.byref(r)) == 0:
                info["ifindex"] = int(r.ifi)
                if r.hop:
                    info["gw"] = _ip(r.hop)
                row = IFROW2()
                row.index = r.ifi
                if IPH.GetIfEntry2(ctypes.byref(row)) == 0:
                    info["iface"], info["desc"] = row.alias, row.desc
                    info["speed"] = int(row.rx // 1000000)
                    info["kind"] = {71: "wifi", 6: "ethernet"}.get(int(row.type), "other")
                    guid = bytes(row.guid)
        except Exception:
            pass
    else:
        for cmd in (["ip", "route", "show", "default"], ["route", "-n", "get", "default"]):
            try:
                txt = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
            except Exception:
                continue
            m = re.search(r"(?:via|gateway:)\s+(\d+\.\d+\.\d+\.\d+)", txt)
            if m:
                info["gw"] = m.group(1)
                break
    try:
        import psutil
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and a.address == info["me"] and a.netmask:
                    info["prefix"] = ipaddress.IPv4Network(f"0.0.0.0/{a.netmask}").prefixlen
                    if not info["iface"]:
                        info["iface"] = name
                        st = stats.get(name)
                        info["speed"] = st.speed if st else 0
                        low = name.lower()
                        if any(w in low for w in ("wi-fi", "wifi", "wlan", "wireless", "kablosuz")):
                            info["kind"] = "wifi"
                        elif low.startswith(("eth", "en", "ethernet")):
                            info["kind"] = "ethernet"
    except Exception:
        pass
    if info["me"]:
        info["net"] = str(ipaddress.IPv4Network(f"{info['me']}/{info['prefix']}", strict=False))
    if WIN and info["kind"] == "wifi":
        info["wifi"] = _q(_wifi, guid) or {}
    return info


def hosts_of(info):
    """The addresses to ask: the whole network, or the /24 around this computer
    when the network is larger (an office /16 would be 65 000 questions)."""
    me = info["me"]
    net = ipaddress.IPv4Network(f"{me}/{info['prefix']}", strict=False)
    if net.prefixlen < 24:
        net = ipaddress.IPv4Network(f"{me}/24", strict=False)
    hosts = [str(h) for h in net.hosts()]
    if info["gw"] and info["gw"] not in hosts:
        hosts.insert(0, info["gw"])
    return hosts, str(net)


# ── names ───────────────────────────────────────────────────────────────────
def rdns(ip):
    try:
        name = socket.gethostbyaddr(ip)[0]
        return None if not name or name == ip else name
    except Exception:
        return None


def nbns(ip):
    q = (struct.pack(">HHHHHH", random.randint(1, 65535), 0, 1, 0, 0, 0)
         + b"\x20" + b"CK" + b"A" * 30 + b"\x00" + struct.pack(">HH", 0x21, 1))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.9)
    try:
        s.sendto(q, (ip, 137))
        d, _ = s.recvfrom(2048)
        if len(d) < 57:
            return None
        for i in range(d[56]):
            e = d[57 + i * 18: 75 + i * 18]
            if len(e) < 18:
                break
            if e[15] == 0 and not struct.unpack(">H", e[16:18])[0] & 0x8000:
                name = e[:15].decode("ascii", "replace").strip()
                if name:
                    return name
    except Exception:
        return None
    finally:
        s.close()
    return None


def _qname(n):
    return b"".join(bytes([len(p)]) + p.encode() for p in n.split(".") if p) + b"\x00"


def _rname(d, o):
    parts, end = [], None
    for _ in range(60):
        ln = d[o]
        if ln == 0:
            o += 1
            break
        if ln & 0xC0 == 0xC0:
            if end is None:
                end = o + 2
            o = ((ln & 0x3F) << 8) | d[o + 1]
            continue
        parts.append(d[o + 1:o + 1 + ln].decode("utf-8", "replace"))
        o += 1 + ln
    return ".".join(parts), (end if end is not None else o)


def _records(d):
    """[(type, name, value)] of a DNS answer: PTR / TXT / A / SRV target."""
    out = []
    qd, an, ns, ar = struct.unpack(">HHHH", d[4:12])
    o = 12
    for _ in range(qd):
        _, o = _rname(d, o)
        o += 4
    for _ in range(an + ns + ar):
        name, o = _rname(d, o)
        t, c, ttl, ln = struct.unpack(">HHIH", d[o:o + 10])
        o += 10
        if t == 12:
            out.append(("PTR", name, _rname(d, o)[0]))
        elif t == 16:
            txt, i = [], o
            while i < o + ln:
                L = d[i]
                txt.append(d[i + 1:i + 1 + L].decode("utf-8", "replace"))
                i += 1 + L
            out.append(("TXT", name, txt))
        elif t == 1 and ln == 4:
            out.append(("A", name, socket.inet_ntoa(d[o:o + 4])))
        o += ln
    return out


def mdns_host(ip):
    """The device's own multicast-DNS name, asked of it directly."""
    rev = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    q = struct.pack(">HHHHHH", random.randint(1, 65535), 0, 1, 0, 0, 0) + _qname(rev) + struct.pack(">HH", 12, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    try:
        s.sendto(q, (ip, 5353))
        d, _ = s.recvfrom(4096)
        for t, n, v in _records(d):
            if t == "PTR":
                return v
    except Exception:
        return None
    finally:
        s.close()
    return None


def mdns_browse(wait=2.6):
    """{ip: {"svc": [...], "name": friendly name, "model": ...}} from the
    devices that announce services (casts, AirPlay, printers, speakers…)."""
    q = struct.pack(">HHHHHH", 0, 0, len(SERVICES), 0, 0, 0) + b"".join(
        _qname(s + ".local") + struct.pack(">HH", 12, 0x8001) for s in SERVICES)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.4)
    found = {}
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s.sendto(q, ("224.0.0.251", 5353))
        end = time.time() + wait
        while time.time() < end:
            try:
                d, a = s.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                recs = _records(d)
            except Exception:
                continue
            f = found.setdefault(a[0], {"svc": [], "name": "", "model": ""})
            for t, n, v in recs:
                if t == "PTR":
                    for svc in SERVICES:
                        if n.startswith(svc):
                            tag = svc.split(".")[0].lstrip("_")
                            if tag not in f["svc"]:
                                f["svc"].append(tag)
                            inst = v.split("." + svc)[0]
                            if "@" in inst:
                                inst = inst.split("@", 1)[1]
                            if (inst and not f["name"] and not re.fullmatch(r"[0-9A-Fa-f-]{12,}", inst)
                                    and not inst.lower().startswith(("chromecast-", "google-"))):
                                f["name"] = inst
                elif t == "TXT":
                    for kv in v:
                        k, _, val = kv.partition("=")
                        if k == "fn" and val:
                            f["name"] = val
                        elif k in ("md", "model", "ty", "usb_MDL") and val and not f["model"]:
                            f["model"] = val
    except Exception:
        pass
    finally:
        s.close()
    return found


def ssdp(wait=2.4):
    """{ip: {"name", "maker", "model", "type"}} from UPnP devices (TVs, routers,
    media servers, consoles). A description is read only from the device that
    answered, never from another address."""
    from urllib.parse import urlparse
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.4)
    locs = {}
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
               "MX: 2\r\nST: ssdp:all\r\n\r\n").encode()
        s.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + wait
        while time.time() < end:
            try:
                d, a = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            m = re.search(rb"(?im)^location:\s*(\S+)", d)
            if m:
                loc = m.group(1).decode("latin-1")
                if urlparse(loc).hostname == a[0]:
                    locs.setdefault(a[0], [])
                    if loc not in locs[a[0]] and len(locs[a[0]]) < 4:
                        locs[a[0]].append(loc)
    except Exception:
        pass
    finally:
        s.close()
    rank = ("internetgatewaydevice", "mediarenderer", "tv", "printer", "")

    def fetch(item):
        ip, urls = item
        best = None
        for u in urls:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": UA})
                x = urllib.request.urlopen(req, timeout=2).read(200000).decode("utf-8", "replace")
            except Exception:
                continue

            def tag(t):
                m = re.search(rf"<{t}>\s*([^<]*?)\s*</{t}>", x)
                return m.group(1).strip() if m else ""
            d = {"name": tag("friendlyName"), "maker": tag("manufacturer"),
                 "model": tag("modelName"), "type": tag("deviceType").lower()}
            score = next(i for i, k in enumerate(rank) if k in d["type"])
            if best is None or score < best[0]:
                best = (score, d)
        return ip, (best[1] if best else None)

    out = {}
    with ThreadPoolExecutor(8) as ex:
        for ip, d in ex.map(fetch, locs.items()):
            if d:
                out[ip] = d
    return out


def probe_ports(ip, pool):
    def one(port):
        c = socket.socket()
        c.settimeout(0.45)
        try:
            return port if c.connect_ex((ip, port)) == 0 else None
        except Exception:
            return None
        finally:
            c.close()
    return sorted(p for p in pool.map(one, PORTS) if p)


# ── makers ──────────────────────────────────────────────────────────────────
_DROP = set("""inc inc. incorporated co co. company corp corp. corporation corporate ltd ltd. limited
llc l.l.c. gmbh ag sa s.a. bv b.v. nv plc pte pty srl s.r.l. kg oy ab as a/s a.s. a.ş. spa s.p.a.
technologies technology tech electronics electronic communications communication international intl
industrial industries ind ind. holdings holding group systems system network networks telecom
digital mobile devices device products manufacturing enterprise enterprises trading precision
solutions labs co.,ltd co.,ltd. ltda anonim sirketi şirketi ticaret sanayi""".split())
_CITY = {"shenzhen", "guangdong", "guangzhou", "hangzhou", "beijing", "shanghai", "zhejiang",
         "jiangsu", "xiamen", "dongguan", "suzhou", "fujian", "chongqing", "wuhan", "qingdao"}
_ALIAS = {"micro-star": "MSI", "hon hai": "Foxconn", "hewlett packard": "HP",
          "sony interactive entertainment": "Sony PlayStation", "seiko epson": "Epson",
          "ieee registration authority": "", "private": ""}


def clean_vendor(s):
    s = re.sub(r"\(.*?\)", " ", s or "").replace(",", " ")
    words = s.split()
    while len(words) > 1 and words[0].lower().strip(".") in _CITY:
        words.pop(0)
    while len(words) > 1 and words[-1].lower() in _DROP | {w.strip(".") for w in _DROP}:
        words.pop()
    name = " ".join(words).strip(" .-")
    for k, v in _ALIAS.items():
        if name.lower().startswith(k):
            return v
    return name[:26]


def load_oui(path):
    old = None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        old = data.get("m") or None
        if old and time.time() - float(data.get("t", 0)) < 120 * 86400:
            return old
    except Exception:
        pass
    try:
        req = urllib.request.Request("https://www.wireshark.org/download/automated/data/manuf",
                                     headers={"User-Agent": UA})
        txt = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
        m = {}
        for line in txt.splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            pre = parts[0].strip()
            bits = 24
            if "/" in pre:
                pre, _, b = pre.partition("/")
                bits = int(b) if b.isdigit() else 24
            hexs = re.sub(r"[^0-9A-Fa-f]", "", pre).upper()
            if len(hexs) < 6:
                continue
            name = clean_vendor((parts[2] if len(parts) > 2 and parts[2].strip() else parts[1]).strip())
            if name:
                m[hexs[:max(6, bits // 4)]] = name
        if len(m) > 1000:
            tmp = str(path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"t": time.time(), "m": m}, f, separators=(",", ":"))
            os.replace(tmp, path)
            return m
    except Exception:
        pass
    return old or {}


def vendor_of(oui, mac):
    h = mac.replace(":", "").upper()
    for n in (9, 7, 6):
        v = oui.get(h[:n])
        if v:
            return v
    return ""


def private_mac(mac):
    return bool(int(mac[:2], 16) & 0x02)


# ── the jobs ────────────────────────────────────────────────────────────────
class Agent:
    def __init__(self, emit, memory_dir):
        self.emit = emit
        self.mem = memory_dir
        self.lock = threading.Lock()
        self.info = {}
        self.sel = None
        self.busy = set()
        self.oui = None
        self.oui_ready = threading.Event()
        self.stop = threading.Event()

    def start(self):
        threading.Thread(target=self._oui, daemon=True).start()
        threading.Thread(target=self._isp, daemon=True).start()
        self.info = route_info()
        self.emit({"t": "net", **self.info})
        threading.Thread(target=self._pings, daemon=True).start()

    def _oui(self):
        self.oui = load_oui(os.path.join(self.mem, "network_radar_oui_cache.json"))
        self.oui_ready.set()

    def _isp(self):
        d = {}
        for url in ("https://ipinfo.io/json", "http://ip-api.com/json/?fields=query,isp,org,city,country"):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
                j = json.loads(urllib.request.urlopen(req, timeout=6).read().decode("utf-8"))
                d = {"ip": j.get("ip") or j.get("query") or "",
                     "isp": clean_vendor(re.sub(r"^AS\d+\s+", "", j.get("org") or j.get("isp") or "")),
                     "city": j.get("city") or "", "country": j.get("country") or ""}
                if d["isp"]:
                    break
            except Exception:
                continue
        self.emit({"t": "isp", **d})

    def _pings(self):
        """Once a second: the router, the internet and the selected device."""
        fails = 0
        far = "1.1.1.1"
        with ThreadPoolExecutor(3) as ex:
            while not self.stop.is_set():
                t0 = time.time()
                gw, sel = self.info.get("gw"), self.sel
                fg = ex.submit(ping, gw, 900) if gw else None
                fn = ex.submit(ping, far, 900)
                fs = ex.submit(ping, sel, 900) if sel else None
                out = {"t": "ping", "gw": fg.result() if fg else None, "net": fn.result()}
                if fs:
                    out["sel"] = [sel, fs.result()]
                if out["net"] is None:
                    fails += 1
                    if fails >= 3:
                        far, fails = ("8.8.8.8" if far == "1.1.1.1" else "1.1.1.1"), 0
                else:
                    fails = 0
                self.emit(out)
                self.stop.wait(max(0.1, 1.0 - (time.time() - t0)))

    # scan ─────────────────────────────────────────────────────────────────
    def scan(self, light=False, skip=()):
        t0 = time.time()
        skip = set(skip or ())
        info = self.info = route_info()
        self.emit({"t": "net", **info})
        if not info.get("me") or info["me"].startswith("127."):
            self.emit({"t": "scan_done", "secs": 0, "error": "not connected to a network"})
            return
        hosts, net = hosts_of(info)
        first = [ip for ip in [info.get("gw")] + arp_cache(info.get("ifindex")) if ip in set(hosts)]
        order = list(dict.fromkeys(first + hosts))
        total = len(order)
        self.emit({"t": "scan_start", "total": total, "net": net, "light": light})
        found = {}
        g = {}
        if not light:
            g["ssdp"] = threading.Thread(target=lambda: g.__setitem__("ssdp_r", _q(ssdp) or {}), daemon=True)
            g["mdns"] = threading.Thread(target=lambda: g.__setitem__("mdns_r", _q(mdns_browse) or {}), daemon=True)
            g["ssdp"].start()
            g["mdns"].start()
        ports_pool = ThreadPoolExecutor(48)
        detail_pool = ThreadPoolExecutor(16)
        details = []

        def detail(ip, mac):
            d = {"t": "dev", "ip": ip}
            pings = [p for p in (ping(ip, 700), ping(ip, 500), ping(ip, 500)) if p is not None]
            d["lat"] = min(pings) if pings else None
            names = {}
            th = [threading.Thread(target=lambda k=k, f=f: names.__setitem__(k, _q(f, ip)), daemon=True)
                  for k, f in (("host", rdns), ("nb", nbns), ("mdns", mdns_host))]
            for t in th:
                t.start()
            if not light:
                d["ports"] = probe_ports(ip, ports_pool)
            for t in th:
                t.join(3.0)
            d.update({k: v for k, v in names.items() if v})
            d["detail"] = True
            self.emit(d)

        done = 0
        # one thread per address: a missing device costs SendARP ~3 s of waiting,
        # and it waits without the interpreter lock, so 254 waits overlap
        with ThreadPoolExecutor(min(256, max(8, total))) as ex:
            futs = {ex.submit(arp, ip): ip for ip in order}
            from concurrent.futures import as_completed
            last = 0.0
            for f in as_completed(futs):
                ip = futs[f]
                done += 1
                r = f.result() if not f.exception() else None
                if r:
                    mac, ms = r
                    found[ip] = mac
                    dev = {"t": "dev", "ip": ip, "mac": mac, "arp": ms, "private": private_mac(mac)}
                    if self.oui_ready.is_set() and not dev["private"]:
                        dev["vendor"] = vendor_of(self.oui or {}, mac)
                    self.emit(dev)
                    if mac not in skip:
                        details.append(detail_pool.submit(detail, ip, mac))
                now = time.time()
                if now - last > 0.15 or done == total:
                    last = now
                    self.emit({"t": "scan_prog", "done": done, "total": total, "found": len(found)})
        if self.oui_ready.wait(20):
            for ip, mac in found.items():
                if not private_mac(mac):
                    v = vendor_of(self.oui or {}, mac)
                    if v:
                        self.emit({"t": "dev", "ip": ip, "vendor": v})
        for f in details:
            try:
                f.result(timeout=15)
            except Exception:
                pass
        if not light:
            for k in ("ssdp", "mdns"):
                g[k].join(4)
            for ip, d in (g.get("ssdp_r") or {}).items():
                if ip in found:
                    self.emit({"t": "dev", "ip": ip, "ssdp": d})
            for ip, d in (g.get("mdns_r") or {}).items():
                if ip in found:
                    self.emit({"t": "dev", "ip": ip, "svc": d})
        ports_pool.shutdown(wait=False)
        detail_pool.shutdown(wait=False)
        self.emit({"t": "scan_done", "secs": round(time.time() - t0, 1), "found": len(found),
                   "light": light})

    # speed test ───────────────────────────────────────────────────────────
    def speed(self):
        import http.client
        host = "speed.cloudflare.com"
        out = {"t": "speed", "phase": "ping"}
        self.emit(out)
        try:
            trace = urllib.request.urlopen(urllib.request.Request(
                f"https://{host}/cdn-cgi/trace", headers={"User-Agent": UA}), timeout=6).read().decode()
            colo = re.search(r"colo=(\w+)", trace)
            out["colo"] = colo.group(1) if colo else ""
        except Exception as e:
            self.emit({"t": "speed", "phase": "failed", "error": f"cannot reach the test server ({e})"})
            return
        idle = [p for p in (ping("1.1.1.1", 900) for _ in range(6)) if p is not None]
        out["ping"] = round(sorted(idle)[len(idle) // 2], 1) if idle else None
        out["jitter"] = (round(sum(abs(a - b) for a, b in zip(idle, idle[1:])) / (len(idle) - 1), 1)
                         if len(idle) > 1 else None)
        self.emit({**out, "phase": "ping"})

        def run(phase, worker, n, secs, cap):
            moved = [0]
            lock = threading.Lock()
            stop = threading.Event()
            loaded = []

            def add(k):
                with lock:
                    moved[0] += k
                    if moved[0] >= cap:
                        stop.set()

            def pinger():
                while not stop.is_set():
                    p = ping("1.1.1.1", 900)
                    if p is not None:
                        loaded.append(p)
                    stop.wait(0.4)
            ths = [threading.Thread(target=worker, args=(add, stop), daemon=True) for _ in range(n)]
            ths.append(threading.Thread(target=pinger, daemon=True))
            for t in ths:
                t.start()
            t0 = time.time()
            hist = [(t0, 0)]
            warm = None
            while time.time() - t0 < secs and not stop.is_set() and not self.stop.is_set():
                time.sleep(0.2)
                now = time.time()
                with lock:
                    b = moved[0]
                hist.append((now, b))
                if warm is None and now - t0 >= 1.0:
                    warm = (now, b)
                old = next((h for h in hist if now - h[0] <= 1.2), hist[0])
                rate = (b - old[1]) * 8 / max(0.05, now - old[0]) / 1e6
                if phase == "up" and now - t0 < 1.0:
                    rate = 0.0                       # the first second only fills socket buffers
                self.emit({"t": "speed", "phase": phase, "mbps": round(rate, 1),
                           "frac": min(1.0, (now - t0) / secs)})
            stop.set()
            end = time.time()
            with lock:
                b = moved[0]
            if warm and end - warm[0] > 0.5:
                mbps = (b - warm[1]) * 8 / (end - warm[0]) / 1e6
            else:
                mbps = b * 8 / max(0.1, end - t0) / 1e6
            lp = sorted(loaded)
            return round(mbps, 1), (round(lp[len(lp) // 2], 1) if lp else None)

        def down(add, stop):
            while not stop.is_set():
                try:
                    c = http.client.HTTPSConnection(host, timeout=8)
                    c.request("GET", "/__down?bytes=25000000", headers={"User-Agent": UA})
                    r = c.getresponse()
                    while not stop.is_set():
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        add(len(chunk))
                    c.close()
                except Exception:
                    if stop.wait(0.3):
                        return

        blob = os.urandom(65536)

        def up(add, stop):
            size = 8 * 1024 * 1024
            while not stop.is_set():
                try:
                    c = http.client.HTTPSConnection(host, timeout=8)
                    c.putrequest("POST", "/__up")
                    c.putheader("User-Agent", UA)
                    c.putheader("Content-Type", "application/octet-stream")
                    c.putheader("Content-Length", str(size))
                    c.endheaders()
                    sent = 0
                    while sent < size and not stop.is_set():
                        c.send(blob)
                        sent += len(blob)
                        add(len(blob))
                    if sent >= size:
                        c.getresponse().read()
                    c.close()
                except Exception:
                    if stop.wait(0.3):
                        return
        out["down"], out["ping_down"] = run("down", down, 4, 8.0, 400 * 1024 * 1024)
        self.emit({**out, "phase": "down_done"})
        out["up"], out["ping_up"] = run("up", up, 3, 7.0, 150 * 1024 * 1024)
        self.emit({**out, "phase": "done"})

    # commands ─────────────────────────────────────────────────────────────
    def _job(self, name, fn, *a):
        with self.lock:
            if name in self.busy:
                return
            self.busy.add(name)

        def wrap():
            try:
                if name == "speed":                  # never while a scan fills the line
                    while "scan" in self.busy and not self.stop.is_set():
                        time.sleep(0.2)
                fn(*a)
            except Exception as e:
                self.emit({"t": "error", "job": name, "error": f"{type(e).__name__}: {e}"})
                if name == "scan":
                    self.emit({"t": "scan_done", "secs": 0, "error": str(e)})
                elif name == "speed":
                    self.emit({"t": "speed", "phase": "failed", "error": str(e)})
            finally:
                with self.lock:
                    self.busy.discard(name)
        threading.Thread(target=wrap, daemon=True).start()

    def command(self, cmd):
        c = cmd.get("cmd")
        if c == "scan":
            self._job("scan", self.scan, bool(cmd.get("light")), cmd.get("skip") or ())
        elif c == "speed":
            self._job("speed", self.speed)
        elif c == "target":
            self.sel = cmd.get("ip") or None


def serve(readline, emit, memory_dir):
    a = Agent(emit, memory_dir)
    a.start()
    while True:
        line = readline()
        if not line:
            break
        try:
            cmd = json.loads(line)
        except Exception:
            continue
        if cmd.get("cmd") == "quit":
            break
        a.command(cmd)
    a.stop.set()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    lock = threading.Lock()

    def emit(o):
        try:
            with lock:
                sys.stdout.write(json.dumps(o, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except Exception:
            os._exit(0)                               # JARVIS is gone
    serve(sys.stdin.readline, emit, sys.argv[1])
    os._exit(0)


if __name__ == "__main__":
    main()
'''


# ─────────────────────────────────────────────────────────────────────────────
# What a device is
# ─────────────────────────────────────────────────────────────────────────────

TYPES = {                       # key: (label, colour)
    "router":  ("Router", None),               # the accent
    "pc":      ("Computer", (114, 160, 255)),
    "laptop":  ("Laptop", (114, 160, 255)),
    "phone":   ("Phone", (96, 224, 150)),
    "tablet":  ("Tablet", (96, 224, 150)),
    "tv":      ("TV / media", (255, 166, 72)),
    "speaker": ("Speaker", (186, 120, 255)),
    "printer": ("Printer", (250, 214, 90)),
    "console": ("Game console", (255, 86, 120)),
    "camera":  ("Camera", (255, 128, 96)),
    "iot":     ("Smart home", (64, 222, 196)),
    "unknown": ("Device", (150, 166, 186)),
}

_NAME_RULES = (
    ("tv", r"\btv\b|televizyon|bravia|chromecast|fire ?tv|firetv|roku|apple ?tv|shield|androidtv|"
           r"webos|tizen|vestel|smart ?tv|\bmibox|google ?tv"),
    ("printer", r"printer|yazici|yazıcı|laserjet|deskjet|officejet|envy|epson|canon|brother|mfc-|"
                r"ecotank|kyocera|xerox|lexmark|ricoh"),
    ("console", r"playstation|\bps[345]\b|xbox|nintendo|switch"),
    ("tablet", r"ipad|galaxy-?tab|\btab\b|tablet|matepad|mipad|xiaomi-?pad|redmi-?pad"),
    ("phone", r"iphone|android|galaxy|pixel|redmi|xiaomi|poco|oneplus|oppo|vivo|realme|huawei|honor|"
              r"phone|telefon|moto|nokia|infinix|tecno|\bmi-?\d"),
    ("laptop", r"macbook|laptop|notebook|thinkpad|ideapad|vivobook|zenbook|surface"),
    ("pc", r"desktop-|\bpc\b|imac|mac-?mini|mac-?studio|workstation|-pc\b|^pc-"),
    ("speaker", r"echo|alexa|homepod|sonos|speaker|hoparl|nest-?audio|google-?home|home-?mini|"
                r"nest-?mini|soundbar|jbl|bose"),
    ("camera", r"\bcam\b|camera|kamera|ipc|hikvision|dahua|ezviz|reolink|imou|tapo-?c|doorbell"),
    ("iot", r"plug|priz|bulb|lamp|light|switch-|sensor|thermostat|tuya|shelly|sonoff|tasmota|"
            r"esp[-_]|espressif|yeelight|hue|vacuum|roborock|dreame|airpurifier|robot"),
)
_VENDOR_RULES = (
    ("console", ("sony playstation", "nintendo")),
    ("printer", ("epson", "canon", "brother", "kyocera", "lexmark", "xerox", "ricoh", "konica")),
    ("camera", ("hikvision", "dahua", "ezviz", "reolink", "imou", "hangzhou")),
    ("iot", ("tuya", "espressif", "shelly", "allterco", "itead", "broadlink", "signify",
             "philips lighting", "yeelight", "lifx", "meross", "sonoff", "chuango", "roborock",
             "dreame", "ecovacs", "irobot", "tp-link kasa", "wiz")),
    ("tv", ("vestel", "tcl", "hisense", "roku", "tp vision", "skyworth", "arcelik", "arçelik",
            "beko", "grundig", "nvidia")),
    ("speaker", ("sonos", "bose", "harman")),
    ("phone", ("oppo", "vivo", "realme", "oneplus", "honor", "huawei device", "motorola mobility",
               "tecno", "infinix", "transsion", "general mobile", "hmd global")),
    ("pc", ("intel", "realtek", "msi", "asustek", "giga-byte", "gigabyte", "dell", "lenovo",
            "liteon", "lite-on", "azurewave", "foxconn", "quanta", "compal", "wistron",
            "pegatron", "rivet", "killer", "micro-star", "asrock", "elitegroup")),
)


def classify(d: dict) -> str:
    if d.get("self"):
        return "pc"
    if d.get("gw"):
        return "router"
    svc = set((d.get("svc") or {}).get("svc") or [])
    ports = set(d.get("ports") or [])
    sd = d.get("ssdp") or {}
    st = sd.get("type", "")
    words = " ".join(str(x or "") for x in (
        d.get("custom"), (d.get("svc") or {}).get("name"), (d.get("svc") or {}).get("model"),
        sd.get("name"), sd.get("model"), d.get("host"), d.get("nb"), d.get("mdns"))).lower()
    vendor = (d.get("vendor") or "").lower()
    if "googlecast" in svc or "androidtvremote2" in svc or "amzn-wplay" in svc or \
            "mediarenderer" in st or ":tv" in st or 8060 in ports:
        return "tv"
    if svc & {"ipp", "printer"} or ports & {9100, 631} or "printer" in st:
        return "printer"
    if 62078 in ports:
        return "tablet" if re.search(r"ipad", words) else "phone"
    for kind, pattern in _NAME_RULES:
        if words and re.search(pattern, words):
            return kind
    if svc & {"sonos", "spotify-connect"} or ("raop" in svc and "airplay" not in svc):
        return "speaker"
    if 554 in ports:
        return "camera"
    if svc & {"hap", "hue", "miio", "mqtt", "rdlink"} or 1883 in ports:
        return "iot"
    for kind, names in _VENDOR_RULES:
        if any(n in vendor for n in names):
            return kind
    if ports & {3389, 445} or d.get("nb"):
        return "pc"
    if d.get("private") and not vendor:
        return "phone"                                    # a randomised address: most often a phone
    return "unknown"


def _clean_host(h: str) -> str:
    h = str(h or "").strip().rstrip(".")
    if not h or re.fullmatch(r"[\d.\-]+", h):
        return ""
    first = h.split(".")[0]
    if re.fullmatch(r"(?:ip-?)?\d+[-.]\d+[-.]\d+[-.]\d+", first, re.I):
        return ""
    return first


def display_name(d: dict, kind: str) -> str:
    if d.get("custom"):
        return d["custom"]
    sd = d.get("ssdp") or {}
    if d.get("gw"):
        maker, model = sd.get("maker", ""), sd.get("model", "")
        if maker and model and model.lower() not in maker.lower():
            return f"{maker.split()[0]} {model}"[:30]
        if model:
            return model[:30]
    sv = (d.get("svc") or {}).get("name") or ""
    if sv:
        return sv[:30]
    name = sd.get("name") or ""
    if name and not re.search(r"(?i)mediashare|:\d+$|^upnp|^dlna|access point|^router$", name):
        return name[:30]
    for k in ("mdns", "nb", "host"):
        h = _clean_host(d.get(k) or "")
        if h and h.lower() not in ("localhost", "unknown", "hgw"):
            return h[:30]
    if d.get("self"):
        return socket.gethostname()[:30]
    label = TYPES[kind][0]
    vendor = d.get("vendor") or ""
    if d.get("gw"):
        return f"{vendor} router" if vendor else "Router"
    if vendor:
        if kind == "unknown":
            return f"{vendor} device"
        noun = label.lower()
        words = vendor.split()
        if len(words) > 1 and words[-1].lower() == noun.split()[0]:
            vendor = " ".join(words[:-1])             # "Tuya Smart" + "smart home"
        return f"{vendor} {noun}"
    tail = "·" + str(d.get("ip", "")).split(".")[-1]           # tells two such devices apart
    if d.get("private"):
        return f"Private device {tail}"
    return f"Unknown device {tail}"


def type_colour(kind: str, acc) -> tuple:
    return TYPES.get(kind, TYPES["unknown"])[1] or acc


_PORT_NAMES = {22: "SSH", 80: "Web", 443: "Web (https)", 445: "File sharing", 548: "Mac sharing",
               554: "Video stream", 631: "Printing", 1883: "MQTT", 3389: "Remote desktop",
               5000: "UPnP/NAS", 7000: "AirPlay", 8008: "Cast", 8009: "Cast", 8060: "Roku",
               8080: "Web", 9100: "Printing", 32400: "Plex", 62078: "iPhone sync"}


def services_of(d: dict) -> list:
    out = []
    for p in d.get("ports") or []:
        n = _PORT_NAMES.get(p)
        if n and n not in out:
            out.append(n)
    for s in (d.get("svc") or {}).get("svc") or []:
        n = {"googlecast": "Cast", "airplay": "AirPlay", "raop": "AirPlay audio", "ipp": "Printing",
             "spotify-connect": "Spotify Connect", "hap": "HomeKit", "sonos": "Sonos",
             "smb": "File sharing"}.get(s)
        if n and n not in out:
            out.append(n)
    return out


def fmt_ms(ms: Optional[float]) -> str:
    if ms is None:
        return "—"
    if ms < 1:
        return "<1 ms"
    return f"{ms:.0f} ms" if ms >= 10 else f"{ms:.1f} ms"


def fmt_mbps(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:.0f}" if v >= 100 else f"{v:.1f}"


def ago(t: float) -> str:
    s = max(0, time.time() - t)
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{s / 60:.0f} min ago"
    if s < 86400 * 2:
        return f"{s / 3600:.0f} h ago"
    return f"{s / 86400:.0f} days ago"


# ─────────────────────────────────────────────────────────────────────────────
# What is remembered
# ─────────────────────────────────────────────────────────────────────────────

class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data: dict = {"v": 1, "watch": False, "nets": {}}
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("nets"), dict):
                self.data = d
        except Exception:
            pass

    def save(self) -> None:
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, self.path)
            except Exception as exc:
                _once("network radar store", exc)

    def net(self, key: str) -> Optional[dict]:
        return self.data["nets"].get(key)

    @property
    def watch(self) -> bool:
        return bool(self.data.get("watch"))

    @watch.setter
    def watch(self, on: bool) -> None:
        with self.lock:
            self.data["watch"] = bool(on)
        self.save()


# ─────────────────────────────────────────────────────────────────────────────
# The live model: everything the helper reports, merged
# ─────────────────────────────────────────────────────────────────────────────

class Net:
    """Read by the board on the GUI thread and by the tool; written by the
    helper's reader thread. Everything under `lock`."""

    def __init__(self):
        self.lock = threading.RLock()
        self.store = Store(STORE_FILE)
        self.info: dict = {}
        self.isp: dict = {}
        self.devs: dict = {}                      # ip -> device dict
        self.scan: dict = {"state": "idle", "done": 0, "total": 0, "found": 0, "t0": 0.0,
                           "secs": 0.0, "light": False, "origin": "", "error": "", "t_end": 0.0}
        self.scan_done = threading.Event()
        self.hist = {"gw": deque(maxlen=90), "net": deque(maxlen=90), "sel": deque(maxlen=60)}
        self.sel_ip: Optional[str] = None
        self.speed: dict = {"state": "idle"}
        self.speed_done = threading.Event()
        self.netkey: Optional[str] = None
        self.fresh_net = False                    # this scan is the first of this network
        self.version = 0
        self.seen_scan = 0
        self.announce: list = []                  # new devices to tell the user about
        self.on_scan_done: list = []
        self.on_speed_done: list = []
        self._rows_cache: tuple = (-1, [])

    # ── messages from the helper ────────────────────────────────────────────
    def handle(self, m: dict) -> None:
        t = m.get("t")
        cbs: list = []
        told: list = []
        with self.lock:
            if t == "ping":
                self.hist["gw"].append(m.get("gw"))
                self.hist["net"].append(m.get("net"))
                sel = m.get("sel")
                if sel and sel[0] == self.sel_ip:
                    self.hist["sel"].append(sel[1])
                return
            self.version += 1
            if t == "net":
                info = {k: v for k, v in m.items() if k != "t"}
                old_gw = self.info.get("gw")
                new_gw = info.get("gw")
                if old_gw and new_gw != old_gw:
                    self.devs.clear()
                    self.netkey = None
                    for h in self.hist.values():
                        h.clear()
                self.info = info
                # Router identity is also checked against the gateway ARP MAC
                # in _dev(), because different LANs can reuse one gateway IP.
            elif t == "isp":
                self.isp = {k: v for k, v in m.items() if k != "t"}
            elif t == "scan_start":
                self.seen_scan += 1
                self.scan.update(state="running", done=0, total=m.get("total", 0), found=0,
                                 t0=time.time(), light=bool(m.get("light")), error="")
                self.scan_done.clear()
                self.fresh_net = False
            elif t == "scan_prog":
                self.scan.update(done=m.get("done", 0), total=m.get("total", 0),
                                 found=m.get("found", 0))
            elif t == "dev":
                self._dev(m)
            elif t == "scan_done":
                self._scan_done(m)
                cbs, self.on_scan_done = self.on_scan_done, []
                told, self.announce = self.announce, []
            elif t == "speed":
                self._speed(m)
                if self.speed.get("state") in ("done", "failed"):
                    cbs, self.on_speed_done = self.on_speed_done, []
            elif t == "error":
                _once(f"network radar {m.get('job')}", RuntimeError(m.get("error")))
        for cb in cbs:
            try:
                cb()
            except Exception as exc:
                _once("network radar callback", exc)
        if told and self.store.watch and ANNOUNCE is not None:
            try:
                ANNOUNCE(told)
            except Exception as exc:
                _once("network radar announce", exc)

    def _dev(self, m: dict) -> None:
        ip = m.get("ip")
        if not ip:
            return
        d = self.devs.get(ip)
        if d is None:
            if not m.get("mac"):
                return
            d = self.devs[ip] = {"ip": ip, "born": time.time()}
        if m.get("mac") and d.get("mac") not in (None, m["mac"]):
            d.clear()                                 # the address went to another device
            d.update(ip=ip, born=time.time())
        for k, v in m.items():
            if k not in ("t", "ip") and v not in (None, "", [], {}):
                d[k] = v
        d["scan"] = self.seen_scan
        d["self"] = ip == self.info.get("me")
        d["gw"] = ip == self.info.get("gw")
        if d.get("lat") is None and d.get("arp") is not None and not d["self"]:
            d["lat_guess"] = d["arp"]
        if d["gw"] and d.get("mac") and self.netkey and self.netkey.casefold() != str(d["mac"]).casefold():
            # Same gateway IP but a different router: clear the previous LAN.
            # Keep the current gateway record so the ordinary initialisation
            # below can establish this router as the new network identity.
            self.devs.clear()
            self.devs[ip] = d
            self.netkey = None
            for history in self.hist.values():
                history.clear()
        if d["gw"] and d.get("mac") and self.netkey is None:
            self.netkey = d["mac"]
            nets = self.store.data["nets"]
            if self.netkey not in nets:
                nets[self.netkey] = {"first": time.time(), "devs": {}, "speed": None}
                self.fresh_net = True
            for other in self.devs.values():
                self._register(other)
        elif self.netkey:
            self._register(d)

    def _register(self, d: dict) -> None:
        mac = d.get("mac")
        net = self.store.net(self.netkey) if self.netkey else None
        if not mac or net is None:
            return
        rec = net["devs"].get(mac)
        now = time.time()
        if rec is None:
            known = self.fresh_net or d.get("self") or d.get("gw")
            rec = net["devs"][mac] = {"first": now, "known": bool(known), "name": ""}
            if not known:
                d["fresh"] = True                     # never seen here before: say so
        rec["last"] = now
        rec["ip"] = d["ip"]
        if d.get("vendor"):
            rec["vendor"] = d["vendor"]
        if rec.get("name"):
            d["custom"] = rec["name"]
        d["known"] = bool(rec.get("known"))
        d["first"] = rec.get("first", now)

    def _scan_done(self, m: dict) -> None:
        light = self.scan.get("light")
        for ip, d in list(self.devs.items()):
            if d.get("scan") != self.seen_scan:
                d["miss"] = d.get("miss", 0) + 1
                if not light or d["miss"] >= 2:
                    del self.devs[ip]
            else:
                d["miss"] = 0
        net = self.store.net(self.netkey) if self.netkey else None
        if net is not None:
            for d in self.devs.values():
                rec = net["devs"].get(d.get("mac"))
                if rec is not None:
                    kind = classify(d)
                    rec["type"] = kind
                    rec["label"] = display_name(d, kind)
                if d.get("fresh") and not d.get("told"):
                    d["told"] = True
                    if self.scan.get("origin") == "auto":
                        self.announce.append(d)
            if self.info.get("wifi", {}).get("ssid"):
                net["ssid"] = self.info["wifi"]["ssid"]
        self.store.save()
        self.scan.update(state="failed" if m.get("error") else "done", error=m.get("error") or "",
                         secs=m.get("secs", 0), t_end=time.time())
        self.scan_done.set()

    def _speed(self, m: dict) -> None:
        ph = m.get("phase")
        s = self.speed
        if ph == "failed":
            s.update(state="failed", error=m.get("error", ""), phase=ph)
        else:
            s.update({k: v for k, v in m.items() if k not in ("t",)})
            s["state"] = "done" if ph == "done" else "running"
            if ph in ("down", "up"):
                s["live"] = m.get("mbps", 0.0)
        if ph == "done":
            s["t"] = time.time()
            net = self.store.net(self.netkey) if self.netkey else None
            if net is not None:
                net["speed"] = {k: s.get(k) for k in ("down", "up", "ping", "jitter", "ping_down",
                                                      "ping_up", "colo", "t")}
                self.store.save()
        if s["state"] in ("done", "failed"):
            self.speed_done.set()

    # ── what the board and the tool read ────────────────────────────────────
    def rows(self) -> list:
        """Online devices first (router, this PC, new ones, then by address),
        then known devices that are away. Cached until anything changes."""
        with self.lock:
            key = self.version
            if self._rows_cache[0] == key:
                return self._rows_cache[1]
            out = []
            for d in self.devs.values():
                kind = classify(d)
                out.append({**d, "kind": kind, "name": display_name(d, kind), "online": True,
                            "services": services_of(d)})
            net = self.store.net(self.netkey) if self.netkey else None
            here = {d.get("mac") for d in self.devs.values()}
            if net is not None and self.scan.get("state") != "running":
                for mac, rec in net["devs"].items():
                    if mac in here or time.time() - rec.get("last", 0) > 30 * 86400:
                        continue
                    kind = rec.get("type") or "unknown"
                    out.append({"ip": rec.get("ip", ""), "mac": mac, "kind": kind, "online": False,
                                "name": rec.get("name") or rec.get("label") or rec.get("vendor") or mac,
                                "vendor": rec.get("vendor", ""), "known": rec.get("known", False),
                                "last": rec.get("last", 0), "first": rec.get("first", 0),
                                "custom": rec.get("name", ""), "services": []})

            def order(r):
                ipn = 0
                try:
                    ipn = int(ipaddress.IPv4Address(r["ip"]))
                except Exception:
                    pass
                return (not r["online"], not r.get("gw"), not r.get("self"),
                        bool(r.get("known", True)) if r["online"] else 0, ipn)
            out.sort(key=order)
            self._rows_cache = (key, out)
            return out

    def is_new(self, r: dict) -> bool:
        return bool(r.get("online") and r.get("mac") and not r.get("known", True)
                    and not r.get("self") and not r.get("gw"))

    def last_speed(self) -> Optional[dict]:
        with self.lock:
            net = self.store.net(self.netkey) if self.netkey else None
            return (net or {}).get("speed")

    # ── changes the user asks for ───────────────────────────────────────────
    def set_name(self, mac: str, name: str) -> bool:
        with self.lock:
            net = self.store.net(self.netkey) if self.netkey else None
            if net is None or mac not in net["devs"]:
                return False
            net["devs"][mac]["name"] = name.strip()[:40]
            net["devs"][mac]["known"] = True
            for d in self.devs.values():
                if d.get("mac") == mac:
                    d["custom"] = name.strip()[:40]
                    d["known"] = True
            self.version += 1
        self.store.save()
        return True

    def trust(self, macs: list) -> int:
        n = 0
        with self.lock:
            net = self.store.net(self.netkey) if self.netkey else None
            if net is None:
                return 0
            for mac in macs:
                rec = net["devs"].get(mac)
                if rec is not None and not rec.get("known"):
                    rec["known"] = True
                    n += 1
            for d in self.devs.values():
                if d.get("mac") in macs:
                    d["known"] = True
            self.version += 1
        self.store.save()
        return n

    def forget(self, mac: str) -> bool:
        with self.lock:
            net = self.store.net(self.netkey) if self.netkey else None
            if net is None or mac not in net["devs"]:
                return False
            del net["devs"][mac]
            self.version += 1
        self.store.save()
        return True


ANNOUNCE: Optional[Callable[[list], None]] = None     # set by the plugin: tells the user
NET = Net()
_VISIBLE = False                                     # the radar is on screen (set on the GUI thread)


# ─────────────────────────────────────────────────────────────────────────────
# The helper's lifetime
# ─────────────────────────────────────────────────────────────────────────────

def _launcher() -> str:
    """The helper's code for `python -c`: compressed, because Windows limits a
    command line to 32 767 characters and the script is longer than that."""
    import base64
    blob = base64.b64encode(zlib.compress(_AGENT_SCRIPT.encode("utf-8"), 9)).decode("ascii")
    return f"import base64,zlib;exec(zlib.decompress(base64.b64decode('{blob}')).decode('utf-8'))"


class _Agent:
    """One helper process while the radar is open or the tool needs it. It
    stops itself a little after neither does."""

    IDLE_S = 45.0

    def __init__(self, net: Net):
        self.net = net
        self.lock = threading.Lock()
        self.child: Optional[subprocess.Popen] = None
        self.inproc: Optional[Any] = None             # the fallback's command queue
        self.last_use = time.time()
        self._reaper: Optional[threading.Thread] = None

    def alive(self) -> bool:
        if self.child is not None:
            return self.child.poll() is None
        return self.inproc is not None

    def ensure(self) -> None:
        with self.lock:
            self.last_use = time.time()
            if self.alive():
                return
            self.child = self.inproc = None
            try:
                exe = sys.executable
                if exe.lower().endswith("pythonw.exe"):
                    alt = exe[:-len("pythonw.exe")] + "python.exe"
                    exe = alt if Path(alt).exists() else exe
                flags = (0x08000000 | 0x00004000) if os.name == "nt" else 0
                self.child = subprocess.Popen(
                    [exe, "-c", _launcher(), str(MEMORY_DIR)],
                    stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=flags, text=True, encoding="utf-8", errors="replace", bufsize=1)
                threading.Thread(target=self._read, args=(self.child,), name="net-radar-read",
                                 daemon=True).start()
            except Exception as exc:
                _once("network radar helper", exc)
                self._start_here()
            if self._reaper is None or not self._reaper.is_alive():
                self._reaper = threading.Thread(target=self._reap, name="net-radar-reaper", daemon=True)
                self._reaper.start()

    def _start_here(self) -> None:
        import queue
        ns: dict = {"__name__": "network_radar_agent"}
        exec(compile(_AGENT_SCRIPT, "<network-radar-agent>", "exec"), ns)
        q: "queue.Queue[str]" = queue.Queue()
        self.inproc = q
        threading.Thread(target=ns["serve"], args=(q.get, self.net.handle, str(MEMORY_DIR)),
                         name="net-radar-agent", daemon=True).start()

    def _read(self, child: subprocess.Popen) -> None:
        try:
            for line in child.stdout:
                try:
                    self.net.handle(json.loads(line))
                except Exception:
                    continue
        except Exception:
            pass
        with self.net.lock:                       # the helper ended: nothing is running any more
            scanning = self.net.scan["state"] == "running"
            testing = self.net.speed.get("state") == "running"
        if scanning:
            self.net.handle({"t": "scan_done", "secs": 0, "error": "the scanner stopped"})
        if testing:
            self.net.handle({"t": "speed", "phase": "failed", "error": "the test stopped"})

    def send(self, cmd: dict) -> None:
        self.ensure()
        line = json.dumps(cmd) + "\n"
        try:
            if self.child is not None:
                self.child.stdin.write(line)
                self.child.stdin.flush()
            elif self.inproc is not None:
                self.inproc.put(line)
        except Exception as exc:
            _once("network radar send", exc)

    def stop(self) -> None:
        with self.lock:
            child, self.child = self.child, None
            q, self.inproc = self.inproc, None
        if child is not None:
            try:
                child.stdin.close()
            except Exception:
                pass
            try:
                child.wait(timeout=1.5)
            except Exception:
                try:
                    child.kill()
                except Exception:
                    pass
        if q is not None:
            q.put("")

    def busy(self) -> bool:
        with self.net.lock:
            return self.net.scan["state"] == "running" or self.net.speed.get("state") == "running"

    def _reap(self) -> None:
        while True:
            time.sleep(5)
            if not self.alive():
                return
            if visible_now() or self.busy():
                self.last_use = time.time()
                continue
            if time.time() - self.last_use > self.IDLE_S:
                self.stop()
                return


AGENT = _Agent(NET)


def start_scan(origin: str = "tool", light: bool = False) -> None:
    """origin: 'tool' (the answer reports it), 'board' or 'auto' (announced)."""
    with NET.lock:
        if NET.scan["state"] == "running":
            if origin == "tool":
                NET.scan["origin"] = "tool"
            return
        NET.scan.update(origin=origin, state="running", done=0, total=0, t0=time.time())
        NET.scan_done.clear()
        skip = [d["mac"] for d in NET.devs.values() if d.get("detail") and d.get("mac")] if light else []
    AGENT.send({"cmd": "scan", "light": light, "skip": skip})


def start_speed() -> None:
    with NET.lock:
        if NET.speed.get("state") == "running":
            return
        NET.speed = {"state": "running", "phase": "ping", "t0": time.time()}
        NET.speed_done.clear()
        NET.version += 1
    AGENT.send({"cmd": "speed"})


def select(ip: Optional[str]) -> None:
    with NET.lock:
        if NET.sel_ip != ip:
            NET.sel_ip = ip
            NET.hist["sel"].clear()
    if AGENT.alive():
        AGENT.send({"cmd": "target", "ip": ip})


# ─────────────────────────────────────────────────────────────────────────────
# Facts for the assistant to speak
# ─────────────────────────────────────────────────────────────────────────────

def _median(vals) -> Optional[float]:
    v = sorted(x for x in vals if x is not None)
    return v[len(v) // 2] if v else None


def describe_link() -> str:
    with NET.lock:
        info, isp = dict(NET.info), dict(NET.isp)
        gw_ms, net_ms = _median(list(NET.hist["gw"])[-10:]), _median(list(NET.hist["net"])[-10:])
    if not info.get("me"):
        return "This computer is not connected to a network."
    w = info.get("wifi") or {}
    if info.get("kind") == "wifi":
        link = (f"Wi-Fi '{w.get('ssid', '?')}'" + (f", signal {w['signal']}%" if "signal" in w else "")
                + (f", {w['band']}" if w.get("band") else "") + (f", {w['rx']} Mbps link" if w.get("rx") else ""))
    elif info.get("kind") == "ethernet":
        link = "Ethernet cable" + (f" ({info['speed']} Mbps link)" if info.get("speed") else "")
    else:
        link = f"adapter {info.get('iface', '?')}"
    s = f"Connected over {link}; network {info.get('net')}, router at {info.get('gw')}."
    if isp.get("isp"):
        s += f" Internet provider: {isp['isp']}" + (f" ({isp.get('city')}, {isp.get('country')})" if isp.get("city") else "") + "."
    if gw_ms is not None or net_ms is not None:
        s += f" Latency now: router {fmt_ms(gw_ms)}, internet {fmt_ms(net_ms)}."
    return s


def describe() -> str:
    rows = NET.rows()
    with NET.lock:
        scan = dict(NET.scan)
    online = [r for r in rows if r["online"]]
    away = [r for r in rows if not r["online"]]
    parts = [describe_link()]
    if scan.get("state") == "failed":
        parts.append(f"The scan failed: {scan.get('error')}.")
    parts.append(f"{len(online)} devices online (scan took {scan.get('secs', 0)} s):")
    items = []
    for r in online[:30]:
        bits = [r["name"], TYPES[r["kind"]][0].lower()]
        if r.get("vendor") and r["vendor"].lower() not in r["name"].lower():
            bits.append(r["vendor"])
        if r.get("private"):
            bits.append("private/randomised hardware address")
        bits.append(r["ip"])
        lat = r.get("lat")
        if lat is not None and not r.get("self"):
            bits.append(fmt_ms(lat))
        tag = " [THIS COMPUTER]" if r.get("self") else (" [ROUTER]" if r.get("gw") else "")
        if NET.is_new(r):
            tag += " [NEW — never seen on this network before]" if r.get("fresh") else " [NOT YET TRUSTED]"
        items.append(", ".join(bits) + tag)
    parts.append("; ".join(items) + ".")
    if away:
        parts.append("Also seen on this network before, not online now: " + "; ".join(
            f"{r['name']} (last seen {ago(r.get('last', 0))})" for r in away[:8]) + ".")
    with NET.lock:
        fresh = NET.fresh_net
    if fresh:
        parts.append("This is the first scan of this network: every device found is now remembered "
                     "as known, and anything that joins later will be flagged as new.")
    if NET.store.watch:
        parts.append("Guard mode is on: new devices are announced.")
    return " ".join(parts)


def describe_speed() -> str:
    with NET.lock:
        s = dict(NET.speed)
    if s.get("state") == "failed":
        return f"status=failed reason=the speed test failed: {s.get('error')}"
    out = (f"Speed test (Cloudflare, server {s.get('colo') or '?'}): download {fmt_mbps(s.get('down'))} Mbps, "
           f"upload {fmt_mbps(s.get('up'))} Mbps, ping {fmt_ms(s.get('ping'))}")
    if s.get("jitter") is not None:
        out += f", jitter {fmt_ms(s.get('jitter'))}"
    loaded = max([x for x in (s.get("ping_down"), s.get("ping_up")) if x is not None], default=None)
    if loaded is not None:
        out += f"; ping while the line is busy {fmt_ms(loaded)}"
        if s.get("ping") and loaded > max(4 * s["ping"], s["ping"] + 60):
            out += (" — much higher than idle (bufferbloat): calls and games lag while something "
                    "downloads; a router with SQM/QoS fixes it")
    return out + "."


# ─────────────────────────────────────────────────────────────────────────────
# Icons
# ─────────────────────────────────────────────────────────────────────────────

def glyph(kind: str) -> QPainterPath:
    """A line icon in a 40 × 40 box."""
    p = QPainterPath()
    if kind == "router":
        p.addRoundedRect(QRectF(6, 21, 28, 11), 3, 3)
        p.moveTo(12, 21); p.lineTo(9, 8)
        p.moveTo(28, 21); p.lineTo(31, 8)
        p.addEllipse(QPointF(12, 26.5), 1.4, 1.4)
        p.addEllipse(QPointF(17, 26.5), 1.4, 1.4)
    elif kind == "pc":
        p.addRoundedRect(QRectF(6, 8, 28, 19), 2, 2)
        p.moveTo(20, 27); p.lineTo(20, 32)
        p.moveTo(13, 32.5); p.lineTo(27, 32.5)
    elif kind == "laptop":
        p.addRoundedRect(QRectF(9, 10, 22, 15), 2, 2)
        p.moveTo(5, 29); p.lineTo(35, 29); p.lineTo(32, 32); p.lineTo(8, 32); p.closeSubpath()
    elif kind == "phone":
        p.addRoundedRect(QRectF(13, 6, 14, 28), 3, 3)
        p.moveTo(18, 30); p.lineTo(22, 30)
    elif kind == "tablet":
        p.addRoundedRect(QRectF(9, 7, 22, 27), 3, 3)
        p.moveTo(18, 30.5); p.lineTo(22, 30.5)
    elif kind == "tv":
        p.addRoundedRect(QRectF(5, 9, 30, 19), 2, 2)
        p.moveTo(14, 32); p.lineTo(26, 32)
        p.moveTo(20, 28); p.lineTo(20, 32)
    elif kind == "speaker":
        p.addRoundedRect(QRectF(12, 5, 16, 30), 4, 4)
        p.addEllipse(QPointF(20, 24), 5, 5)
        p.addEllipse(QPointF(20, 12), 2.2, 2.2)
    elif kind == "printer":
        p.addRect(QRectF(12, 6, 16, 8))
        p.addRoundedRect(QRectF(6, 14, 28, 13), 2, 2)
        p.addRect(QRectF(12, 23, 16, 10))
    elif kind == "console":
        p.addRoundedRect(QRectF(5, 13, 30, 15), 7, 7)
        p.moveTo(11, 20.5); p.lineTo(17, 20.5)
        p.moveTo(14, 17.5); p.lineTo(14, 23.5)
        p.addEllipse(QPointF(25, 19), 1.6, 1.6)
        p.addEllipse(QPointF(29, 22), 1.6, 1.6)
    elif kind == "camera":
        p.addRoundedRect(QRectF(5, 12, 22, 16), 3, 3)
        p.addEllipse(QPointF(16, 20), 4.5, 4.5)
        p.moveTo(27, 17); p.lineTo(35, 13); p.lineTo(35, 27); p.lineTo(27, 23)
    elif kind == "iot":
        p.moveTo(6, 20); p.lineTo(20, 8); p.lineTo(34, 20)
        p.moveTo(10, 17); p.lineTo(10, 32); p.lineTo(30, 32); p.lineTo(30, 17)
        p.addEllipse(QPointF(20, 24), 3, 3)
    else:
        for i in range(6):
            a = math.radians(60 * i - 90)
            pt = QPointF(20 + 13 * math.cos(a), 20 + 13 * math.sin(a))
            p.moveTo(pt) if i == 0 else p.lineTo(pt)
        p.closeSubpath()
        p.addEllipse(QPointF(20, 20), 2.5, 2.5)
    return p


_GLYPHS: dict = {}


def _glyph(kind: str) -> QPainterPath:
    g = _GLYPHS.get(kind)
    if g is None:
        g = _GLYPHS[kind] = glyph(kind)
    return g


# ─────────────────────────────────────────────────────────────────────────────
# The board
# ─────────────────────────────────────────────────────────────────────────────

def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def lat_radius(ms: Optional[float]) -> float:
    if ms is None:
        return RR * 0.89
    f = math.log10(1 + max(0.0, ms)) / math.log10(1 + LAT_MAX)
    return RR * (0.32 + 0.57 * max(0.0, min(1.0, f)))


def _bearing(mac: str) -> float:
    return (zlib.crc32((mac or "").encode()) % 3600) / 10.0


class RadarBoard(lb.Board):
    SWEEP_S = 3.2

    def __init__(self, acc, parent=None):
        super().__init__(acc, parent)
        self.idle_ms = 40                         # the sweep always moves
        self.sweep = 0.0
        self.blips: dict = {}                     # mac -> {"pos", "target", "ping", "born", "shown"}
        self._lay_ver = -1
        self._static: Optional[QPixmap] = None
        self._static_key: Any = None
        self._blip_pix: dict = {}
        self._fmc: dict = {}
        self.tab = "lat"
        self.scroll = 0
        self.hover_mac: Optional[str] = None
        self.flash = ("", 0.0)
        self.t_open = 0.0
        self.needle = 0.0
        self.auto_t = 0.0
        self.crowd = False
        self._lab: dict = {}                      # (name, sub) -> (elided name, width)

    # ── lifetime ─────────────────────────────────────────────────────────────
    def on_open(self) -> None:
        global _VISIBLE
        _VISIBLE = True
        self.t_open = time.time()
        self.auto_t = time.time()
        AGENT.ensure()
        if NET.sel_ip:
            AGENT.send({"cmd": "target", "ip": NET.sel_ip})

    def on_close(self) -> None:
        global _VISIBLE
        _VISIBLE = False                          # the helper stops itself when idle

    def animating(self) -> bool:
        return False

    # ── helpers ──────────────────────────────────────────────────────────────
    def fm(self, font) -> QFontMetricsF:
        m = self._fmc.get(id(font))
        if m is None:
            m = self._fmc[id(font)] = QFontMetricsF(font)
        return m

    def elide(self, s: str, font, width: float) -> str:
        if width <= 8:
            return ""
        return self.fm(font).elidedText(s, Qt.TextElideMode.ElideRight, width)

    def say_flash(self, msg: str) -> None:
        self.flash = (msg, time.time())

    def selected(self, rows: list) -> Optional[dict]:
        ip = NET.sel_ip
        if not ip:
            return None
        return next((r for r in rows if r["online"] and r["ip"] == ip), None)

    # ── title ────────────────────────────────────────────────────────────────
    def title(self):
        with NET.lock:
            info, scan = NET.info, NET.scan
            n = sum(1 for d in NET.devs.values())
            net, gw = info.get("net"), info.get("gw")
            state = scan["state"]
        if not info:
            return "Network radar", "LOCATING THE NETWORK…"
        if not info.get("me"):
            return "Network radar", "NOT CONNECTED"
        bits = [net or "", f"GATEWAY {gw}" if gw else "NO GATEWAY"]
        if state == "running":
            bits.append("SWEEPING")
        else:
            bits.append(f"{n} ONLINE")
            new = sum(1 for r in NET.rows() if NET.is_new(r))
            if new:
                bits.append(f"{new} NEW")
        if NET.store.watch:
            bits.append("GUARD ON")
        return "Network radar", "  ·  ".join(bits)

    # ── painting ─────────────────────────────────────────────────────────────
    def draw(self, p: QPainter, dt: float, now: float) -> None:
        prev = self.sweep
        self.sweep = (self.sweep + dt * 360.0 / self.SWEEP_S) % 360.0
        rows = NET.rows()
        with NET.lock:
            scan = dict(NET.scan)
            ver = NET.version
            testing = NET.speed.get("state") == "running"
        # 25 frames a second while someone is looking or something happens, 15 after
        lively = now - max(self.last_input, self.t_open) < 45 or scan["state"] == "running" or testing
        self.idle_ms = 40 if lively else 66
        # while open: a quiet sweep for newcomers every minute
        if scan["state"] != "running" and now - self.auto_t > 60 and now - self.t_open > 20:
            self.auto_t = now
            start_scan("auto", light=True)
        if scan["state"] != "running":
            self.auto_t = max(self.auto_t, scan.get("t_end", 0))
        self._layout(rows, ver, now)
        self._radar(p, now, prev, rows, scan)
        self._cards(p, now)
        self._list(p, now, rows, scan)
        self._bottom(p, now, rows)
        self._status(p, now, scan)

    # radar ───────────────────────────────────────────────────────────────
    def _layout(self, rows: list, ver: int, now: float) -> None:
        if ver == self._lay_ver:
            return
        self._lay_ver = ver
        pts = []
        for r in rows:
            if not r["online"] or r.get("self") or not r.get("mac"):
                continue
            lat = r.get("lat") if r.get("lat") is not None else r.get("lat_guess")
            pts.append([r["mac"], _bearing(r["mac"]), lat_radius(lat)])
        self.crowd = len(pts) > 12
        gap = 60.0 if self.crowd else 78.0
        for _ in range(60):                       # spread blips that would overlap
            moved = False
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    a, b = pts[i], pts[j]
                    ax, ay = a[2] * math.sin(math.radians(a[1])), -a[2] * math.cos(math.radians(a[1]))
                    bx, by = b[2] * math.sin(math.radians(b[1])), -b[2] * math.cos(math.radians(b[1]))
                    dist = math.hypot(ax - bx, ay - by)
                    if dist < gap:
                        r_avg = max(60.0, (a[2] + b[2]) / 2)
                        push = math.degrees((gap - dist) / r_avg) / 2 + 0.5
                        diff = (b[1] - a[1] + 540) % 360 - 180
                        sgn = 1 if diff >= 0 else -1
                        a[1] = (a[1] - sgn * push) % 360
                        b[1] = (b[1] + sgn * push) % 360
                        moved = True
            if not moved:
                break
        live = set()
        for mac, brg, rad in pts:
            live.add(mac)
            target = QPointF(RC.x() + rad * math.sin(math.radians(brg)),
                             RC.y() - rad * math.cos(math.radians(brg)))
            b = self.blips.get(mac)
            if b is None:
                self.blips[mac] = {"pos": QPointF(target), "target": target, "brg": brg, "ping": 0.0,
                                   "shown": False, "born": 0.0}
            else:
                b["target"], b["brg"] = target, brg
        for mac in list(self.blips):
            if mac not in live:
                del self.blips[mac]

    def _static_pix(self, p: QPainter) -> tuple:
        sc = p.transform().m11()
        dpr = max(1.0, self.devicePixelRatioF())
        box = QRectF(RC.x() - RR - 60, RC.y() - RR - 60, 2 * RR + 120, 2 * RR + 120)
        key = (round(sc, 4), dpr, self.acc)
        if self._static is not None and self._static_key == key:
            return self._static, box
        pix = QPixmap(max(1, math.ceil(box.width() * sc * dpr)), max(1, math.ceil(box.height() * sc * dpr)))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        qp = QPainter(pix)
        try:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            qp.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            qp.scale(sc, sc)
            qp.translate(-box.left(), -box.top())
            g = QRadialGradient(RC, RR)
            g.setColorAt(0.0, _q(_mix(BG, self.acc, 0.16), 230))
            g.setColorAt(0.75, _q(_mix(BG, self.acc, 0.07), 220))
            g.setColorAt(1.0, _q(_mix(BG, self.acc, 0.03), 210))
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(QBrush(g))
            qp.drawEllipse(RC, RR, RR)
            # cross hairs and diagonals
            qp.setPen(QPen(_q(self.acc, 38), 1))
            for a in range(0, 180, 45):
                rad = math.radians(a)
                dx, dy = RR * math.sin(rad), -RR * math.cos(rad)
                qp.drawLine(QPointF(RC.x() - dx, RC.y() - dy), QPointF(RC.x() + dx, RC.y() + dy))
            # latency rings
            for ms, label in ((1, "1 ms"), (10, "10 ms"), (100, "100 ms")):
                r = lat_radius(ms)
                qp.setPen(QPen(_q(self.acc, 70), 1, Qt.PenStyle.DashLine))
                qp.setBrush(Qt.BrushStyle.NoBrush)
                qp.drawEllipse(RC, r, r)
                a = math.radians(38)
                self.text(qp, RC.x() + r * math.sin(a) + 6, RC.y() - r * math.cos(a) - 2, label,
                          self.font("mono", 13), _mix(DIM, self.acc, 0.3), valign="mid")
            qp.setPen(QPen(_q(self.acc, 60), 1))
            qp.drawEllipse(RC, RR * 0.12, RR * 0.12)
            # rim and ticks
            qp.setPen(QPen(_q(self.acc, 200), 2))
            qp.drawEllipse(RC, RR, RR)
            qp.setPen(QPen(_q(self.acc, 60), 6))
            qp.drawEllipse(RC, RR + 4, RR + 4)
            minor, major = [], []
            for i in range(72):
                a = math.radians(i * 5)
                r1 = RR + (18 if i % 6 == 0 else 10)
                line = (QPointF(RC.x() + (RR + 4) * math.sin(a), RC.y() - (RR + 4) * math.cos(a)),
                        QPointF(RC.x() + r1 * math.sin(a), RC.y() - r1 * math.cos(a)))
                (major if i % 6 == 0 else minor).append(line)
            qp.setPen(QPen(_q(self.acc, 90), 1.2))
            for a_, b_ in minor:
                qp.drawLine(a_, b_)
            qp.setPen(QPen(_q(self.acc, 200), 2))
            for a_, b_ in major:
                qp.drawLine(a_, b_)
            for i in range(0, 360, 30):
                a = math.radians(i)
                self.text(qp, RC.x() + (RR + 36) * math.sin(a), RC.y() - (RR + 36) * math.cos(a),
                          f"{i:03d}", self.font("mono", 13), DIM, align="c", valign="mid")
        finally:
            qp.end()
        self._static, self._static_key = pix, key
        return pix, box

    def _blip_img(self, kind: str, col, state: str, p: QPainter) -> tuple:
        """A device's disc and icon, drawn once: frames only place it."""
        sc = p.transform().m11()
        dpr = max(1.0, self.devicePixelRatioF())
        size = 30.0 if kind == "router" else 24.0
        key = (kind, col, state, round(sc, 4), dpr)
        hit = self._blip_pix.get(key)
        if hit is not None:
            return hit
        box = size + 14
        pix = QPixmap(max(1, math.ceil(2 * box * sc * dpr)), max(1, math.ceil(2 * box * sc * dpr)))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        qp = QPainter(pix)
        try:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            qp.scale(sc, sc)
            c = QPointF(box, box)
            ring = WARN if state == "new" else col
            g = QRadialGradient(c, size + 12)
            g.setColorAt(0.0, _q(ring, 90))
            g.setColorAt(0.6, _q(ring, 30))
            g.setColorAt(1.0, _q(ring, 0))
            qp.setPen(Qt.PenStyle.NoPen)
            qp.setBrush(QBrush(g))
            qp.drawEllipse(c, size + 12, size + 12)
            qp.setBrush(_q(_mix(BG, col, 0.24)))
            qp.setPen(QPen(_q(ring), 2.2 if state != "plain" else 1.6))
            qp.drawEllipse(c, size, size)
            k = size * 1.25 / 40
            qp.translate(c.x() - 20 * k, c.y() - 20 * k)
            qp.scale(k, k)
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.setPen(QPen(_q(_mix(col, TEXT, 0.35)), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                           Qt.PenJoinStyle.RoundJoin))
            qp.drawPath(_glyph(kind))
        finally:
            qp.end()
        if len(self._blip_pix) > 80:
            self._blip_pix.clear()
        self._blip_pix[key] = (pix, box)
        return pix, box

    def _radar(self, p: QPainter, now: float, prev: float, rows: list, scan: dict) -> None:
        pix, box = self._static_pix(p)
        p.drawPixmap(box, pix, QRectF(0, 0, pix.width(), pix.height()))
        # the sweep
        qt_angle = 90.0 - self.sweep
        g = QConicalGradient(RC, qt_angle)
        g.setColorAt(0.0, _q(self.acc, 120))
        g.setColorAt(0.05, _q(self.acc, 60))
        g.setColorAt(0.20, _q(self.acc, 0))
        g.setColorAt(1.0, _q(self.acc, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        # only the wedge the trail lights: filling the whole disc every frame
        # costs several times more and paints mostly transparent pixels
        disc = QRectF(RC.x() - RR + 1, RC.y() - RR + 1, 2 * RR - 2, 2 * RR - 2)
        p.drawPie(disc, int(qt_angle * 16), int(0.2 * 360 * 16))
        a = math.radians(self.sweep)
        tip = QPointF(RC.x() + RR * math.sin(a), RC.y() - RR * math.cos(a))
        self.stroke(p, lambda: p.drawLine(RC, tip), self.acc, 2)
        # scan progress on the rim
        if scan["state"] == "running":
            frac = scan["done"] / scan["total"] if scan.get("total") else 0.0
            rim = QRectF(RC.x() - RR - 4, RC.y() - RR - 4, 2 * RR + 8, 2 * RR + 8)
            if frac > 0.002:
                self.stroke(p, lambda: p.drawArc(rim, 90 * 16, -int(frac * 360 * 16)), self.acc, 6,
                            cap=Qt.PenCapStyle.FlatCap)
        by_mac = {r["mac"]: r for r in rows if r["online"] and r.get("mac")}
        sel = NET.sel_ip
        hover = None
        labels: list = []                         # (priority, pos, row, colour, new, alpha)
        k_sz = 0.78 if self.crowd else 1.0
        # the blips: lit by the sweep, shown the first time it passes them
        for mac, b in self.blips.items():
            r = by_mac.get(mac)
            if r is None:
                continue
            brg = b["brg"]
            passed = ((brg - prev) % 360) <= ((self.sweep - prev) % 360)
            if passed:
                b["ping"] = now
                if not b["shown"]:
                    b["shown"], b["born"] = True, now
            if not b["shown"]:
                continue
            t = b["target"]
            pos = b["pos"]
            k = 0.15
            b["pos"] = pos = QPointF(pos.x() + (t.x() - pos.x()) * k, pos.y() + (t.y() - pos.y()) * k)
            kind = r["kind"]
            col = type_colour(kind, self.acc)
            new = NET.is_new(r)
            is_sel = r["ip"] == sel
            on = math.hypot(self.mouse.x() - pos.x(), self.mouse.y() - pos.y()) < 30 * k_sz
            if on:
                hover = mac
            if is_sel or on or mac == self.hover_mac:
                p.setPen(QPen(_q(col, 120), 1.2, Qt.PenStyle.DashLine))
                p.drawLine(RC, pos)
            since = now - b["ping"]
            bright = 0.5 + 0.5 * math.exp(-since / 1.4)
            pop = _ease((now - b["born"]) / 0.5)
            if since < 1.0:                       # the ping ripple
                rr = 26 + 44 * since
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_q(WARN if new else col, int(160 * (1 - since))), 2))
                p.drawEllipse(pos, rr, rr)
            if new:                               # NEW: a pulsing warning ring
                pulse = 0.5 + 0.5 * math.sin(now * 5)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_q(WARN, int(80 + 140 * pulse)), 2))
                p.drawEllipse(pos, (34 + 4 * pulse) * k_sz, (34 + 4 * pulse) * k_sz)
            img, half = self._blip_img(kind, col, "new" if new else ("sel" if is_sel or on else "plain"), p)
            p.save()
            p.setOpacity(p.opacity() * (bright if not (is_sel or on) else 1.0) * pop)
            sz = half * (0.6 + 0.4 * pop) * k_sz
            p.drawPixmap(QRectF(pos.x() - sz, pos.y() - sz, 2 * sz, 2 * sz), img,
                         QRectF(0, 0, img.width(), img.height()))
            p.restore()
            if is_sel:
                self.stroke(p, lambda pos=pos: p.drawEllipse(pos, 32 * k_sz, 32 * k_sz), self.acc, 2.5)
            self.hit(QRectF(pos.x() - 30 * k_sz, pos.y() - 30 * k_sz, 60 * k_sz, 60 * k_sz), f"dev:{r['ip']}")
            prio = 0 if (is_sel or on) else (1 if r.get("gw") else (2 if new else 3))
            labels.append((prio, pos, r, col, new, pop * (0.75 + 0.25 * bright)))
        self.hover_mac = hover
        self._labels(p, labels, k_sz)
        # this computer, in the middle
        me = next((r for r in rows if r.get("self")), None)
        img, half = self._blip_img("pc", type_colour("pc", self.acc), "sel", p)
        pulse = 0.5 + 0.5 * math.sin(now * 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_q(self.acc, int(50 + 60 * pulse)), 1.5))
        p.drawEllipse(RC, 36 + 6 * pulse, 36 + 6 * pulse)
        p.drawPixmap(QRectF(RC.x() - half, RC.y() - half, 2 * half, 2 * half), img,
                     QRectF(0, 0, img.width(), img.height()))
        with NET.lock:
            my_ip = NET.info.get("me") or ""
        self.text(p, RC.x(), RC.y() + 34, "THIS PC", self.font("mono", 13), TEXT, align="c")
        self.text(p, RC.x(), RC.y() + 52, my_ip, self.font("mono", 12), DIM, align="c")
        if me is not None:
            self.hit(QRectF(RC.x() - 30, RC.y() - 30, 60, 60), f"dev:{me['ip']}")
        if scan["state"] == "running" and not self.blips:
            pulse = 0.5 + 0.5 * math.sin(now * 3)
            self.text(p, RC.x(), RC.y() - 78, "SWEEPING", self.font("display", 30),
                      _mix(DIM, self.acc, pulse), align="c", valign="mid", glow=True)
        with NET.lock:
            connected = bool(NET.info.get("me")) or not NET.info
        if not connected:
            self.text(p, RC.x(), RC.y() - 90, "NO NETWORK", self.font("display", 40), HOT,
                      align="c", valign="mid", glow=True)

    def _labels(self, p: QPainter, labels: list, k_sz: float) -> None:
        """Names next to the blips, on the side away from the centre. Most
        important first (selected / under the mouse, the router, new devices);
        a label that would cover another one or a blip is left out — the list
        and the hover still name every device."""
        taken = [QRectF(pos.x() - 26 * k_sz, pos.y() - 26 * k_sz, 52 * k_sz, 52 * k_sz)
                 for _pr, pos, *_ in labels]
        taken.append(QRectF(RC.x() - 60, RC.y() - 34, 120, 96))         # THIS PC
        fn, fs = self.font("body", 15), self.font("mono", 12)
        placed: list = []
        off = 34 * k_sz
        for prio, pos, r, col, new, alpha in sorted(labels, key=lambda x: x[0]):
            dx, dy = pos.x() - RC.x(), pos.y() - RC.y()
            dist = max(1.0, math.hypot(dx, dy))
            ux, uy = dx / dist, dy / dist
            lat = r.get("lat")
            sub = ("NEW · " if new else "") + (fmt_ms(lat) if lat is not None else "." + r["ip"].split(".")[-1])
            key = (r["name"], sub)
            hit = self._lab.get(key)
            if hit is None:
                name = self.elide(r["name"], fn, 170)
                w = max(self.fm(fn).horizontalAdvance(name), self.fm(fs).horizontalAdvance(sub)) + 6
                if len(self._lab) > 300:
                    self._lab.clear()
                hit = self._lab[key] = (name, w)
            name, w = hit
            if abs(uy) >= 0.55:
                lx, ly, al = pos.x(), (pos.y() + off if uy > 0 else pos.y() - off - 40), "c"
                box = QRectF(lx - w / 2, ly, w, 38)
            else:
                lx, ly, al = pos.x() + (off + 4 if ux > 0 else -off - 4), pos.y() - 21, ("l" if ux > 0 else "r")
                box = QRectF(lx if ux > 0 else lx - w, ly, w, 38)
            own = QRectF(pos.x() - 26 * k_sz, pos.y() - 26 * k_sz, 52 * k_sz, 52 * k_sz)
            if prio > 0 and any(box.intersects(t) for t in placed + [t for t in taken if t != own]):
                continue
            placed.append(box)
            p.save()
            p.setOpacity(p.opacity() * alpha)
            if prio == 0:                             # readable over anything
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_q(BG, 200))
                p.drawRoundedRect(box.adjusted(-6, -3, 6, 3), 5, 5)
            self.text(p, lx, ly, name, fn, WARN if new else TEXT, align=al)
            self.text(p, lx, ly + 20, sub, fs, _mix(col, TEXT, 0.2), align=al)
            p.restore()

    # cards ───────────────────────────────────────────────────────────────
    def _cards(self, p: QPainter, now: float) -> None:
        with NET.lock:
            info, isp = dict(NET.info), dict(NET.isp)
            net_ms = next((v for v in reversed(NET.hist["net"]) if v is not None), None)
            gw_ms = next((v for v in reversed(NET.hist["gw"]) if v is not None), None)
        w = (RX1 - RX0 - 14) / 2
        a = QRectF(RX0, 186, w, 116)
        b = QRectF(RX0 + w + 14, 186, w, 116)
        self.panel(p, a, 0.10)
        self.panel(p, b, 0.10)
        # the link
        x, y = a.left() + 16, a.top() + 14
        wf = info.get("wifi") or {}
        if info.get("kind") == "wifi":
            self.text(p, x, y, "WI-FI", self.font("mono", 14), DIM)
            name = wf.get("ssid") or info.get("iface") or "Wi-Fi"
            self.text(p, x, y + 22, self.elide(name, self.font("display", 28), w - 110),
                      self.font("display", 28), TEXT, glow=True)
            bits = [b_ for b_ in (wf.get("band"), f"CH {wf['channel']}" if wf.get("channel") else "",
                                  wf.get("phy"), f"{wf['rx']} Mbps" if wf.get("rx") else "") if b_]
            self.text(p, x, y + 66, self.elide("  ·  ".join(bits) or info.get("desc", ""), self.font("mono", 14), w - 32),
                      self.font("mono", 14), DIM)
            sig = wf.get("signal")
            if sig is not None:
                lit = 4 if sig >= 75 else 3 if sig >= 50 else 2 if sig >= 25 else 1
                col = self.acc if sig >= 50 else (WARN if sig >= 25 else HOT)
                bx = a.right() - 74
                for i in range(4):
                    h = 10 + i * 8
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(_q(col if i < lit else _mix(BG, DIM, 0.4)))
                    p.drawRect(QRectF(bx + i * 13, a.top() + 54 - h, 9, h))
                self.text(p, a.right() - 16, a.top() + 62, f"{sig}%", self.font("mono", 14), col, align="r")
        elif info.get("kind") == "ethernet":
            self.text(p, x, y, "ETHERNET", self.font("mono", 14), DIM)
            sp = info.get("speed") or 0
            label = (f"{sp / 1000:g} Gbps" if sp >= 1000 else f"{sp} Mbps") if sp else "Wired"
            self.text(p, x, y + 22, label, self.font("display", 28), TEXT, glow=True)
            self.text(p, x, y + 66, self.elide(info.get("desc") or info.get("iface", ""), self.font("mono", 14), w - 32),
                      self.font("mono", 14), DIM)
        else:
            self.text(p, x, y, "LINK", self.font("mono", 14), DIM)
            self.text(p, x, y + 22, info.get("iface") or ("…" if not info else "Offline"),
                      self.font("display", 28), TEXT if info.get("me") else HOT)
        if gw_ms is not None and info.get("kind") != "wifi":
            self.text(p, a.right() - 16, y, f"ROUTER {fmt_ms(gw_ms)}", self.font("mono", 14),
                      lb._level(min(100, gw_ms * 4), self.acc), align="r")
        # the internet
        x, y = b.left() + 16, b.top() + 14
        self.text(p, x, y, "INTERNET", self.font("mono", 14), DIM)
        name = isp.get("isp") or ("…" if not isp else "Unknown provider")
        self.text(p, x, y + 22, self.elide(name, self.font("display", 28), w - 32),
                  self.font("display", 28), TEXT, glow=True)
        ip = isp.get("ip", "")
        masked = ""
        if ip.count(".") == 3:
            masked = ".".join(ip.split(".")[:2]) + ".•••.•••"
        elif ip:
            masked = ip.split(":")[0] + ":•••"
        place = ", ".join(x_ for x_ in (isp.get("city"), isp.get("country")) if x_)
        self.text(p, x, y + 66, self.elide("  ·  ".join(s for s in (place, masked) if s), self.font("mono", 14), w - 32),
                  self.font("mono", 14), DIM)
        col = self.acc if net_ms is not None and net_ms < 60 else (WARN if net_ms is not None and net_ms < 150 else HOT)
        self.text(p, b.right() - 16, y, f"PING {fmt_ms(net_ms)}", self.font("mono", 14),
                  col if net_ms is not None else DIM, align="r")

    # device list ─────────────────────────────────────────────────────────
    def _list(self, p: QPainter, now: float, rows: list, scan: dict) -> None:
        online = [r for r in rows if r["online"]]
        new = sum(1 for r in online if NET.is_new(r))
        y = 322
        self.text(p, RX0, y, "DEVICES", self.font("mono", 15), DIM, valign="mid")
        info = f"{len(online)} ONLINE" + (f"  ·  {new} NEW" if new else "")
        self.text(p, RX0 + 92, y, info, self.font("mono", 15), WARN if new else self.acc, valign="mid")
        # RESCAN
        r = QRectF(RX1 - 124, y - 16, 124, 32)
        busy = scan["state"] == "running"
        over = not busy and self.hit(r, "rescan")
        self.panel(p, r, 0.28 if over else 0.10, active=over)
        self.text(p, r.center().x(), r.center().y(), "SCANNING…" if busy else "↻ RESCAN",
                  self.font("mono", 14), TEXT if over else (self.acc if busy else _mix(DIM, TEXT, 0.4)),
                  align="c", valign="mid")
        maxs = max(0, len(rows) - ROWS)
        self.scroll = max(0, min(self.scroll, maxs))
        sel = NET.sel_ip
        for i, rr in enumerate(rows[self.scroll:self.scroll + ROWS]):
            ry = LIST_Y + i * ROW_H
            row = QRectF(RX0 - 6, ry - 3, RX1 - RX0 + 12, ROW_H - 4)
            key = f"dev:{rr['ip']}" if rr["online"] else f"off:{rr['mac']}"
            over = self.hit(row, key)
            is_sel = rr["online"] and rr["ip"] == sel
            hov = over or (rr.get("mac") and rr.get("mac") == self.hover_mac)
            col = type_colour(rr["kind"], self.acc)
            newr = NET.is_new(rr)
            if is_sel or hov:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_q(_mix(BG, self.acc, 0.24 if is_sel else 0.15)))
                p.drawRect(row)
                if is_sel:
                    p.fillRect(QRectF(row.left(), row.top(), 3, row.height()), _q(self.acc))
            alpha = 255 if rr["online"] else 120
            p.save()
            p.setOpacity(p.opacity() * alpha / 255)
            k = 0.78
            p.translate(RX0 + 2, ry + 1)
            p.scale(k, k)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_q(WARN if newr else col), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawPath(_glyph(rr["kind"]))
            p.restore()
            fn = self.font("body", 17)
            right_w = 150
            tag = ""
            if newr:
                tag = "NEW"
            elif not rr["online"]:
                tag = "OFFLINE"
            elif rr.get("self"):
                tag = "THIS PC"
            tw = self.fm(self.font("mono", 12)).horizontalAdvance(tag) + 14 if tag else 0
            name_w = RX1 - RX0 - 44 - right_w - tw - 8
            nm = self.elide(rr["name"], fn, name_w)
            x = RX0 + 42
            self.text(p, x, ry, nm, fn, TEXT if rr["online"] else DIM, alpha=alpha)
            if tag:
                tx = x + self.fm(fn).horizontalAdvance(nm) + 10
                tc = WARN if newr else (DIM if not rr["online"] else self.acc)
                tr = QRectF(tx, ry + 3, tw - 4, 18)
                p.setPen(QPen(_q(tc, 200), 1))
                p.setBrush(_q(_mix(BG, tc, 0.18)))
                p.drawRoundedRect(tr, 3, 3)
                self.text(p, tr.center().x(), tr.center().y(), tag, self.font("mono", 12), tc,
                          align="c", valign="mid")
            sub_bits = [TYPES[rr["kind"]][0]]
            if rr.get("vendor") and rr["vendor"].lower() not in rr["name"].lower():
                sub_bits.append(rr["vendor"])
            elif rr.get("private") and not rr.get("vendor"):
                sub_bits.append("private address")
            if not rr["online"] and rr.get("last"):
                sub_bits.append("seen " + ago(rr["last"]))
            self.text(p, x, ry + 22, self.elide("  ·  ".join(sub_bits), self.font("mono", 13), name_w + tw),
                      self.font("mono", 13), DIM, alpha=alpha)
            # right: address and latency
            self.text(p, RX1, ry + 1, rr.get("ip", ""), self.font("mono", 15),
                      _mix(DIM, TEXT, 0.5), align="r", alpha=alpha)
            lat = rr.get("lat")
            if rr["online"] and not rr.get("self"):
                frac = 0.0 if lat is None else min(1.0, math.log10(1 + lat) / math.log10(1 + LAT_MAX))
                bw = 70
                bx = RX1 - bw
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_q(_mix(BG, col, 0.25)))
                p.drawRect(QRectF(bx, ry + 28, bw, 4))
                if lat is not None:
                    p.setBrush(_q(lb._level(frac * 100, col)))
                    p.drawRect(QRectF(bx, ry + 28, max(3.0, bw * frac), 4))
                self.text(p, bx - 8, ry + 23, fmt_ms(lat), self.font("mono", 12), DIM, align="r")
        if maxs:                                  # scroll bar
            track = QRectF(RX1 + 10, LIST_Y, 3, ROWS * ROW_H - 8)
            p.fillRect(track, _q(_mix(BG, self.acc, 0.2)))
            hgt = track.height() * ROWS / len(rows)
            top = track.top() + (track.height() - hgt) * self.scroll / maxs
            p.fillRect(QRectF(track.left(), top, 3, hgt), _q(self.acc))
        if not rows:
            msg = "SWEEPING THE NETWORK…" if scan["state"] == "running" else "NO DEVICES YET"
            self.text(p, (RX0 + RX1) / 2, LIST_Y + 120, msg, self.font("display", 24), DIM,
                      align="c", valign="mid")

    # bottom: latency / speed / device ────────────────────────────────────
    def _bottom(self, p: QPainter, now: float, rows: list) -> None:
        sel = self.selected(rows)
        with NET.lock:
            sp = dict(NET.speed)
        if sel is None and self.tab == "dev":
            self.tab = "lat"
        tabs = [("lat", "LATENCY"), ("speed", "SPEED TEST")] + ([("dev", "DEVICE")] if sel else [])
        x = RX0
        for key, label in tabs:
            f = self.font("mono", 15)
            tw = self.fm(f).horizontalAdvance(label) + 28
            r = QRectF(x, TAB_Y, tw, 32)
            on = self.tab == key
            over = self.hit(r, f"tab:{key}")
            if on or over:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_q(_mix(BG, self.acc, 0.24 if on else 0.12)))
                p.drawRect(r)
            if on:
                p.fillRect(QRectF(r.left(), r.bottom() - 3, r.width(), 3), _q(self.acc))
            self.text(p, r.center().x(), r.center().y(), label, f, TEXT if on else DIM,
                      align="c", valign="mid")
            x += tw + 6
        p.setPen(QPen(_q(_mix(BG, self.acc, 0.35)), 1))
        p.drawLine(QPointF(RX0, TAB_Y + 32), QPointF(RX1, TAB_Y + 32))
        if self.tab == "speed":
            self._speed(p, now, sp)
        elif self.tab == "dev" and sel is not None:
            self._device(p, now, sel)
        else:
            self._latency(p, now)

    def _latency(self, p: QPainter, now: float) -> None:
        with NET.lock:
            gw, net = list(NET.hist["gw"]), list(NET.hist["net"])
        top = TAB_Y + 50
        cur_gw = next((v for v in reversed(gw) if v is not None), None)
        cur_net = next((v for v in reversed(net) if v is not None), None)
        x = RX0
        for label, val, col in (("ROUTER", cur_gw, self.acc), ("INTERNET", cur_net, UP_COL)):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_q(col))
            p.drawEllipse(QPointF(x + 6, top + 10), 5, 5)
            x += 18
            x += self.text(p, x, top, label, self.font("mono", 14), DIM) + 10
            x += self.text(p, x, top - 4, fmt_ms(val), self.font("display", 22), TEXT) + 30
        vals = [v for v in net[-60:] if v is not None]
        lost = sum(1 for v in net[-60:] if v is None)
        n = len(net[-60:])
        loss = lost / n * 100 if n else 0
        jit = (sum(abs(a - b) for a, b in zip(vals, vals[1:])) / (len(vals) - 1)) if len(vals) > 1 else None
        self.text(p, RX1, top + 2, f"LOSS {loss:.0f}%   JITTER {fmt_ms(jit)}", self.font("mono", 14),
                  HOT if loss >= 5 else DIM, align="r")
        g = QRectF(RX0 + 8, top + 44, RX1 - RX0 - 16, 120)
        allv = [v for v in gw[-60:] + net[-60:] if v is not None]
        hi = max(5.0, max(allv) * 1.25 if allv else 10.0)
        step = next(s for s in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 5000) if hi / s <= 4)
        hi = math.ceil(hi / step) * step
        p.setPen(QPen(_q(self.acc, 30), 1))
        v = 0.0
        while v <= hi + 1e-6:
            yy = g.bottom() - g.height() * v / hi
            p.drawLine(QPointF(g.left(), yy), QPointF(g.right(), yy))
            self.text(p, g.left() - 6, yy, f"{v:g}", self.font("mono", 11), DIM, align="r", valign="mid")
            v += step
        for series, col in ((net, UP_COL), (gw, self.acc)):
            s = series[-60:]
            if len(s) < 2:
                continue
            path = QPainterPath()
            started = False
            for i, val in enumerate(s):
                px = g.left() + g.width() * (i + 60 - len(s)) / 59
                if val is None:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(_q(HOT, 160))
                    p.drawRect(QRectF(px - 1.5, g.top(), 3, g.height()))
                    started = False
                    continue
                py = g.bottom() - g.height() * min(val, hi) / hi
                if started:
                    path.lineTo(px, py)
                else:
                    path.moveTo(px, py)
                    started = True
            self.stroke(p, lambda path=path: p.drawPath(path), col, 2.2)
        self.text(p, g.left(), g.bottom() + 10, "LAST 60 S", self.font("mono", 12), DIM)
        last = NET.last_speed()
        if last and last.get("down") is not None:
            self.text(p, g.right(), g.bottom() + 10,
                      f"LAST SPEED TEST  ↓ {fmt_mbps(last['down'])}  ↑ {fmt_mbps(last.get('up'))} Mbps  ·  {ago(last.get('t', 0)).upper()}",
                      self.font("mono", 12), DIM, align="r")

    def _speed(self, p: QPainter, now: float, sp: dict) -> None:
        state = sp.get("state", "idle")
        last = NET.last_speed() if state == "idle" else None
        if last:
            sp = {**last, "state": "done", "phase": "done"}
            state = "done"
        phase = sp.get("phase", "")
        live = sp.get("live", 0.0) if state == "running" and phase in ("down", "up") else (
            sp.get("down") if phase == "down_done" else 0.0)
        if state == "done":
            live = sp.get("down") or 0.0              # the result stays on the dial
        target = math.log10(1 + max(0.0, live or 0.0)) / math.log10(1001)
        self.needle += (target - self.needle) * min(1.0, 0.18)
        col = UP_COL if phase == "up" else self.acc
        cx, cy, r = RX0 + 150, TAB_Y + 214, 128.0
        box = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_q(_mix(BG, self.acc, 0.2)), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawArc(box, 180 * 16, -180 * 16)
        if self.needle > 0.002:
            self.stroke(p, lambda: p.drawArc(box, 180 * 16, -int(180 * 16 * self.needle)), col, 14,
                        cap=Qt.PenCapStyle.FlatCap)
        if state == "done" and sp.get("up"):        # upload: the inner arc
            fu = math.log10(1 + sp["up"]) / math.log10(1001)
            inner = box.adjusted(22, 22, -22, -22)
            p.setPen(QPen(_q(_mix(BG, UP_COL, 0.2)), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(inner, 180 * 16, -180 * 16)
            self.stroke(p, lambda: p.drawArc(inner, 180 * 16, -int(180 * 16 * fu)), UP_COL, 6,
                        cap=Qt.PenCapStyle.FlatCap)
        for v in (0, 1, 5, 10, 50, 100, 250, 500, 1000):
            f = math.log10(1 + v) / math.log10(1001)
            a = math.radians(180 - 180 * f)
            p.setPen(QPen(_q(DIM), 2))
            p.drawLine(QPointF(cx + (r + 10) * math.cos(a), cy - (r + 10) * math.sin(a)),
                       QPointF(cx + (r + 18) * math.cos(a), cy - (r + 18) * math.sin(a)))
            self.text(p, cx + (r + 32) * math.cos(a), cy - (r + 30) * math.sin(a),
                      f"{v:g}" if v < 1000 else "1G", self.font("mono", 11), DIM, align="c", valign="mid")
        a = math.radians(180 - 180 * self.needle)
        tip = QPointF(cx + (r - 22) * math.cos(a), cy - (r - 22) * math.sin(a))
        self.stroke(p, lambda: p.drawLine(QPointF(cx, cy), tip), TEXT, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_q(col))
        p.drawEllipse(QPointF(cx, cy), 7, 7)
        if state == "done" and sp.get("down") is not None:
            self.text(p, cx, cy - 64, fmt_mbps(sp["down"]), self.font("display", 40), TEXT, align="c",
                      valign="mid", glow=True)
            self.text(p, cx, cy - 32, "↓ DOWNLOAD Mbps", self.font("mono", 12), self.acc, align="c", valign="mid")
        elif state == "running":
            big = fmt_mbps(live) if phase in ("down", "up") else "…"
            lab = {"down": "DOWNLOAD Mbps", "up": "UPLOAD Mbps", "down_done": "UPLOAD Mbps"}.get(phase, "MEASURING PING")
            self.text(p, cx, cy - 58, big, self.font("display", 40), TEXT, align="c", valign="mid", glow=True)
            self.text(p, cx, cy - 26, lab, self.font("mono", 12), col, align="c", valign="mid")
        elif state == "failed":
            self.text(p, cx, cy - 50, "FAILED", self.font("display", 30), HOT, align="c", valign="mid")
        # the numbers
        x = RX0 + 330
        y = TAB_Y + 48
        for label, key, unit, c in (("DOWNLOAD", "down", "Mbps", self.acc), ("UPLOAD", "up", "Mbps", UP_COL)):
            self.text(p, x, y, label, self.font("mono", 13), DIM)
            v = sp.get(key)
            running_here = state == "running" and ((key == "down" and phase == "down") or (key == "up" and phase == "up"))
            txt = fmt_mbps(live) if running_here else fmt_mbps(v)
            w = self.text(p, x, y + 16, txt, self.font("display", 36), TEXT if v is not None or running_here else DIM,
                          glow=v is not None)
            self.text(p, x + w + 8, y + 34, unit, self.font("mono", 13), c)
            y += 72
        ping, jit = sp.get("ping"), sp.get("jitter")
        loaded = max([v for v in (sp.get("ping_down"), sp.get("ping_up")) if v is not None], default=None)
        line = f"PING {fmt_ms(ping)}" + (f"  ·  JITTER {fmt_ms(jit)}" if jit is not None else "")
        self.text(p, x, y, line, self.font("mono", 14), TEXT)
        if loaded is not None:
            bloat = ping is not None and loaded > max(4 * ping, ping + 60)
            self.text(p, x, y + 22, f"UNDER LOAD {fmt_ms(loaded)}" + ("  ·  BUFFERBLOAT" if bloat else ""),
                      self.font("mono", 14), HOT if bloat else DIM)
        # the button
        r = QRectF(RX1 - 170, TAB_Y + 48, 170, 40)
        busy = state == "running"
        over = not busy and self.hit(r, "speed")
        self.panel(p, r, 0.3 if over else (0.18 if not busy else 0.08), active=over or not sp.get("down"))
        label = "TESTING…" if busy else ("▶ RUN AGAIN" if sp.get("down") is not None else "▶ START TEST")
        self.text(p, r.center().x(), r.center().y(), label, self.font("mono", 15),
                  TEXT if over or not busy else self.acc, align="c", valign="mid")
        when = ""
        if state == "done" and sp.get("t"):
            when = ago(sp["t"]).upper()
        self.text(p, RX1, TAB_Y + 100, "  ·  ".join(s for s in (f"SERVER {sp['colo']}" if sp.get("colo") else "", when) if s),
                  self.font("mono", 12), DIM, align="r")

    def _device(self, p: QPainter, now: float, r: dict) -> None:
        col = type_colour(r["kind"], self.acc)
        new = NET.is_new(r)
        top = TAB_Y + 48
        p.save()
        p.translate(RX0, top - 2)
        p.scale(1.15, 1.15)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_q(WARN if new else col), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPath(_glyph(r["kind"]))
        p.restore()
        x = RX0 + 56
        self.text(p, x, top - 4, self.elide(r["name"], self.font("display", 28), 420),
                  self.font("display", 28), WARN if new else TEXT, glow=True)
        sub = TYPES[r["kind"]][0] + (f"  ·  {r['vendor']}" if r.get("vendor") else "")
        if new:
            sub += "  ·  NEW ON THIS NETWORK" if r.get("fresh") else "  ·  NOT TRUSTED YET"
        self.text(p, x, top + 32, self.elide(sub.upper(), self.font("mono", 13), 440), self.font("mono", 13),
                  WARN if new else col)
        # facts
        mac = r.get("mac", "")
        host = _clean_host(r.get("mdns") or r.get("nb") or r.get("host") or "")
        facts = [("IP", r["ip"]),
                 ("MAC", mac + ("  (private)" if r.get("private") else "")),
                 ("HOST", host or "—"),
                 ("FIRST SEEN", ago(r["first"]) if r.get("first") else "—"),
                 ("SERVICES", ", ".join(r.get("services") or []) or "—")]
        y = top + 64
        for i, (k, v) in enumerate(facts):
            cx = RX0 + (i % 2) * 330
            cy = y + (i // 2) * 26
            self.text(p, cx, cy, k, self.font("mono", 12), DIM)
            self.text(p, cx + 92, cy - 2, self.elide(v, self.font("mono", 14), 230 if i < 4 else 560),
                      self.font("mono", 14), TEXT)
        # live latency of this device
        with NET.lock:
            hs = list(NET.hist["sel"])
        cur = next((v for v in reversed(hs) if v is not None), r.get("lat"))
        self.text(p, RX1, top - 4, fmt_ms(cur), self.font("display", 28), TEXT, align="r", glow=True)
        self.text(p, RX1, top + 32, "LIVE PING", self.font("mono", 12), DIM, align="r")
        g = QRectF(RX1 - 160, top + 52, 160, 34)
        vals = [v for v in hs if v is not None]
        if len(hs) >= 2 and vals:
            hi = max(1.0, max(vals) * 1.2)
            path = QPainterPath()
            first = True
            for i, v in enumerate(hs):
                if v is None:
                    continue
                px = g.left() + g.width() * i / max(1, len(hs) - 1)
                py = g.bottom() - g.height() * v / hi
                if first:
                    path.moveTo(px, py)
                    first = False
                else:
                    path.lineTo(px, py)
            self.stroke(p, lambda: p.drawPath(path), col, 2)
        # buttons
        btns = []
        if new:
            btns.append(("trust", "✓ TRUST"))
        if set(r.get("ports") or []) & {80, 443, 8080}:
            btns.append(("web", "OPEN WEB"))
        btns.append(("copy", "COPY IP"))
        bx = RX1
        for key, label in reversed(btns):
            f = self.font("mono", 14)
            w = self.fm(f).horizontalAdvance(label) + 30
            rr = QRectF(bx - w, TAB_Y + 200, w, 36)
            over = self.hit(rr, f"btn:{key}")
            self.panel(p, rr, 0.3 if over else 0.12, active=over or key == "trust")
            self.text(p, rr.center().x(), rr.center().y(), label, f,
                      TEXT if over else (WARN if key == "trust" else _mix(DIM, TEXT, 0.5)), align="c", valign="mid")
            bx -= w + 10

    def _status(self, p: QPainter, now: float, scan: dict) -> None:
        f = self.font("mono", 15)
        y = 954
        msg, t0 = self.flash
        if msg and now - t0 < 4:
            self.text(p, 70, y, self.elide(msg, f, 1460), f, self.acc, valign="mid")
            return
        if scan["state"] == "running":
            with NET.lock:
                net = NET.info.get("net") or ""
            txt = (f"SWEEPING {net}  ·  {scan.get('done', 0)} / {scan.get('total', 0)} ADDRESSES  ·  "
                   f"{scan.get('found', 0)} ANSWERED")
            self.text(p, 70, y, txt, f, self.acc, valign="mid")
            return
        if scan["state"] == "failed":
            self.text(p, 70, y, f"SCAN FAILED: {scan.get('error')}".upper(), f, HOT, valign="mid")
            return
        bits = []
        if scan.get("secs"):
            bits.append(f"SWEPT IN {scan['secs']} S")
        bits += ["CLOSER TO THE CENTRE = FASTER TO ANSWER", "CLICK A DEVICE FOR DETAILS",
                 "RIGHT-CLICK: CLEAR"]
        self.text(p, 70, y, self.elide("   ·   ".join(bits), f, 1460), f, DIM, valign="mid")

    # ── input ────────────────────────────────────────────────────────────────
    def clicked(self, key: str) -> None:
        rows = NET.rows()
        if key == "rescan":
            start_scan("board")
            self.say_flash("Sweeping the network again…")
        elif key == "speed":
            self.tab = "speed"
            start_speed()
        elif key.startswith("tab:"):
            self.tab = key[4:]
        elif key.startswith("dev:"):
            ip = key[4:]
            if NET.sel_ip == ip and self.tab == "dev":
                select(None)
                self.tab = "lat"
            else:
                select(ip)
                self.tab = "dev"
        elif key.startswith("off:"):
            r = next((x for x in rows if not x["online"] and x["mac"] == key[4:]), None)
            if r:
                self.say_flash(f"{r['name']} is not on the network now — last seen {ago(r.get('last', 0))}.")
        elif key.startswith("btn:"):
            r = self.selected(rows)
            if r is None:
                return
            what = key[4:]
            if what == "trust":
                NET.trust([r["mac"]])
                self.say_flash(f"{r['name']} is now a trusted device.")
            elif what == "web":
                ports = set(r.get("ports") or [])
                url = f"https://{r['ip']}" if 443 in ports and 80 not in ports else (
                    f"http://{r['ip']}:8080" if 8080 in ports and 80 not in ports else f"http://{r['ip']}")
                try:
                    webbrowser.open(url)
                    self.say_flash(f"Opened {url} in the browser.")
                except Exception as exc:
                    self.say_flash(f"Could not open {url}: {exc}")
            elif what == "copy":
                try:
                    QGuiApplication.clipboard().setText(r["ip"])
                    self.say_flash(f"Copied {r['ip']}.")
                except Exception as exc:
                    self.say_flash(f"Could not copy: {exc}")

    def mousePressEvent(self, e) -> None:
        try:
            if e.button() == Qt.MouseButton.RightButton:
                self.last_input = time.time()
                select(None)
                if self.tab == "dev":
                    self.tab = "lat"
                return
        except Exception as exc:
            _once("radar right click", exc)
        super().mousePressEvent(e)

    def wheelEvent(self, e) -> None:
        try:
            pt = self._to_canvas(e.position())
            if pt.x() >= RX0 - 10 and LIST_Y - 10 <= pt.y() <= LIST_Y + ROWS * ROW_H:
                self.scroll += -1 if e.angleDelta().y() > 0 else 1
                self.last_input = time.time()
                self.update()
        except Exception as exc:
            _once("radar wheel", exc)

    def keyPressEvent(self, e) -> None:
        try:
            if e.key() == Qt.Key.Key_Escape:
                if NET.sel_ip:
                    select(None)
                    self.tab = "lat"
                else:
                    self.close_view()
            elif e.key() == Qt.Key.Key_F5:
                start_scan("board")
            self.last_input = time.time()
            self.update()
        except Exception as exc:
            _once("radar key", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Entry points (safe from any thread)
# ─────────────────────────────────────────────────────────────────────────────

def board() -> Optional[RadarBoard]:
    b = lb._boards.get("network")
    if b is None or sip.isdeleted(b):
        return None
    return b


def visible_now() -> bool:
    """From any thread, without waiting on the GUI thread."""
    return _VISIBLE


def show(player, tab: Optional[str] = None) -> bool:
    def setup(b: RadarBoard) -> None:
        if tab:
            b.tab = tab
    return bool(lb._gui(lambda: lb._open(player, "network", RadarBoard, setup)))


def set_tab(tab: str) -> None:
    lb._gui(lambda: setattr(board(), "tab", tab) if board() else None, timeout=3.0)
