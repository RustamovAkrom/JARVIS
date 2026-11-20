"""
core/cameras.py — which cameras this computer has, by name, and the one the
user chose (the devices panel, beside the microphone and speakers).

    list_cameras()     [{"index", "name"}] in the order OpenCV numbers them
    chosen()           the chosen camera's index now, or None (automatic)
    save_choice(i, n)  remember a choice (None: automatic)
    open_index(i)      a cv2.VideoCapture opened the way the list numbers it
    open_fast(i)       the same device opened the way that gives the most frames
                       (Windows: Media Foundation, by name), or None
    films(cap)         is that camera filming (not a placeholder, colour, shutter)
    Camera(i)          the camera to use, chosen and kept honest while it is used
    open_live(cams)    the first camera that films
    in_use_by()        the program holding a camera now ("" when none is known)

WHY BY NAME
    OpenCV knows cameras by number only, and the numbers are not stable: a
    virtual camera installed, a webcam plugged into another port, and "camera
    1" is something else. And the same number means different devices through
    Windows' two camera APIs (DirectShow and Media Foundation) - "camera 0"
    was a real webcam through one and DroidCam's orange idle picture through
    the other. So the choice is kept as the device's NAME, and turned into a
    number each time from the same list the number belongs to. The number is
    also saved as camera_index, which every camera feature already reads.

WHERE THE NAMES COME FROM
    Windows   DirectShow's own list of video input devices (ICreateDevEnum,
              through comtypes - the product has it for the volume control):
              exactly the list OpenCV's DirectShow backend numbers.
    Linux     /sys/class/video4linux/videoN/name - N is OpenCV's number. A
              device's extra nodes (metadata, "index" other than 0) cannot film
              and are left out.
    Else      "Camera 1", "Camera 2"... - numbers that open, without names.

FRAMES A SECOND (open_fast)
    Through DirectShow many webcams ignore the request for MJPG and send raw
    YUY2, which USB carries at 720p only ~12 times a second - measured
    2026-10-28 on a "USB Camera": 12.6 a second through DirectShow, 30 through
    Media Foundation, same device, same 1280x720. Hand tracking lives on frames
    (a fast clap is 3 frames at 12, 8 at 30), so it opens the camera through
    Media Foundation, found by its NAME in Media Foundation's own list
    (MFEnumDeviceSources, the list OpenCV's MSMF backend numbers - virtual
    DirectShow-only cameras are not in it). Anything else keeps DirectShow.
    Two things it needs: OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS=0 before
    OpenCV loads (main.py sets it; with them on, opening took 10 s), and no
    format asked of it (MJPG asked without the transforms hands over the
    compressed bytes) - it picks the fast one itself.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

# For a run that does not start in main.py (tools, tests): see FRAMES A SECOND.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

_lock = threading.Lock()


# ── Windows: DirectShow's list ───────────────────────────────────────────────

def _directshow_names() -> list[str] | None:
    """Video input devices in DirectShow's order, or None when it cannot be read."""
    try:
        import comtypes
        import comtypes.client
        from comtypes import GUID, COMMETHOD, HRESULT, IUnknown
        from ctypes import POINTER, c_ulong, c_void_p, byref
        from comtypes.automation import VARIANT
        from comtypes.persist import IPropertyBag
    except Exception:
        return None

    class IMoniker(IUnknown):
        # IPersist, IPersistStream, then IMoniker; only BindToStorage is called,
        # the others are here to keep the table in its order.
        _iid_ = GUID("{0000000F-0000-0000-C000-000000000046}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetClassID", (["out"], POINTER(GUID), "pClassID")),
            COMMETHOD([], HRESULT, "IsDirty"),
            COMMETHOD([], HRESULT, "Load", (["in"], c_void_p, "pStm")),
            COMMETHOD([], HRESULT, "Save", (["in"], c_void_p, "pStm"), (["in"], c_ulong, "fClearDirty")),
            COMMETHOD([], HRESULT, "GetSizeMax", (["out"], c_void_p, "pcbSize")),
            COMMETHOD([], HRESULT, "BindToObject", (["in"], c_void_p, "pbc"), (["in"], c_void_p, "pmkToLeft"),
                      (["in"], POINTER(GUID), "riidResult"), (["out"], POINTER(c_void_p), "ppvResult")),
            COMMETHOD([], HRESULT, "BindToStorage", (["in"], c_void_p, "pbc"), (["in"], c_void_p, "pmkToLeft"),
                      (["in"], POINTER(GUID), "riid"), (["out"], POINTER(POINTER(IPropertyBag)), "ppvObj")),
        ]

    class IEnumMoniker(IUnknown):
        _iid_ = GUID("{00000102-0000-0000-C000-000000000046}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Next", (["in"], c_ulong, "celt"),
                      (["out"], POINTER(POINTER(IMoniker)), "rgelt"),
                      (["out"], POINTER(c_ulong), "pceltFetched")),
        ]

    class ICreateDevEnum(IUnknown):
        _iid_ = GUID("{29840822-5B84-11D0-BD3B-00A0C911CE86}")
        _methods_ = [
            COMMETHOD([], HRESULT, "CreateClassEnumerator", (["in"], POINTER(GUID), "clsidDeviceClass"),
                      (["out"], POINTER(POINTER(IEnumMoniker)), "ppEnumMoniker"),
                      (["in"], c_ulong, "dwFlags")),
        ]

    names: list[str] = []
    try:
        comtypes.CoInitialize()
    except OSError:
        pass                                   # already initialised on this thread
    try:
        dev_enum = comtypes.client.CreateObject(
            GUID("{62BE5D10-60EB-11d0-BD3B-00A0C911CE86}"), interface=ICreateDevEnum)
        video = GUID("{860BB310-5D01-11d0-BD3B-00A0C911CE86}")
        enum = dev_enum.CreateClassEnumerator(byref(video), 0)
        if not enum:                           # S_FALSE: no device in the category
            return []
        while True:
            try:
                moniker, fetched = enum.Next(1)
            except Exception:
                break
            if not fetched or not moniker:
                break
            name = ""
            try:
                bag = moniker.BindToStorage(None, None, byref(IPropertyBag._iid_))
                # pVar is [in, out]: comtypes hands back what it holds.
                name = str(bag.Read("FriendlyName", VARIANT(), None) or "")
            except Exception:
                pass
            names.append(name.strip())
        return names
    except Exception as e:
        print(f"[Cameras] DirectShow's list could not be read: {type(e).__name__}: {e}")
        return None


# ── Windows: Media Foundation's list ─────────────────────────────────────────

def _media_foundation_names() -> list[str] | None:
    """Video capture devices in Media Foundation's order (OpenCV's MSMF
    numbering), or None when it cannot be read. Plain ctypes: MFStartup,
    MFCreateAttributes + SetGUID(source type = video capture),
    MFEnumDeviceSources, IMFActivate::GetAllocatedString(friendly name)."""
    if sys.platform != "win32":
        return None
    import ctypes
    import uuid
    from ctypes import POINTER, WINFUNCTYPE, HRESULT, byref, c_uint32, c_void_p, c_wchar_p

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    def guid(text):
        u, g = uuid.UUID(text), _GUID()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4[:] = list(u.bytes[8:])
        return g

    def method(obj, index, *argtypes):
        table = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
        return WINFUNCTYPE(HRESULT, c_void_p, *argtypes)(table[index])

    # IMFAttributes: Release 2, GetAllocatedString 13, SetGUID 24
    SOURCE_TYPE = "c60ac5fe-252a-478f-a0ef-bc8fa5f7cad3"
    VIDEO_CAPTURE = "8ac3587a-4ae7-42d8-99e0-0a6013eef90f"
    FRIENDLY_NAME = "60d0e559-52f8-4fa2-bbce-acdb34a8ec01"
    try:
        mfplat, mf, ole = ctypes.WinDLL("mfplat"), ctypes.WinDLL("mf"), ctypes.WinDLL("ole32")
        if mfplat.MFStartup(0x00020070, 0) < 0:
            return None
        attrs = c_void_p()
        if mfplat.MFCreateAttributes(byref(attrs), 1) < 0 or not attrs:
            return None
        names: list[str] = []
        try:
            method(attrs, 24, POINTER(_GUID), POINTER(_GUID))(attrs, byref(guid(SOURCE_TYPE)),
                                                             byref(guid(VIDEO_CAPTURE)))
            devices, count = POINTER(c_void_p)(), c_uint32()
            if mf.MFEnumDeviceSources(attrs, byref(devices), byref(count)) < 0:
                return None
            for i in range(count.value):
                dev = devices[i]
                text, n = c_wchar_p(), c_uint32()
                try:
                    method(dev, 13, POINTER(_GUID), POINTER(c_wchar_p), POINTER(c_uint32))(
                        dev, byref(guid(FRIENDLY_NAME)), byref(text), byref(n))
                    names.append(str(text.value or "").strip())
                    ole.CoTaskMemFree(text)
                except OSError:
                    names.append("")
                method(dev, 2)(dev)
            ole.CoTaskMemFree(devices)
        finally:
            method(attrs, 2)(attrs)
        return names
    except OSError as e:
        print(f"[Cameras] Media Foundation's list could not be read: {e}")
        return None


# ── Linux: video4linux ───────────────────────────────────────────────────────

def _v4l_cameras(root: Path = Path("/sys/class/video4linux")) -> list[dict] | None:
    if not root.is_dir():
        return None
    out = []
    for node in root.glob("video*"):
        try:
            n = int(node.name[5:])
        except ValueError:
            continue
        try:
            if (node / "index").read_text().strip() not in ("", "0"):
                continue                       # a second node of one device: metadata
        except OSError:
            pass
        try:
            name = (node / "name").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            name = ""
        out.append({"index": n, "name": name or f"Camera {n + 1}"})
    return sorted(out, key=lambda c: c["index"])


# ── everywhere ───────────────────────────────────────────────────────────────

def backend() -> int:
    """The OpenCV backend the list belongs to."""
    import cv2
    if sys.platform == "win32":
        return cv2.CAP_DSHOW
    if sys.platform.startswith("linux"):
        return getattr(cv2, "CAP_V4L2", cv2.CAP_ANY)
    return cv2.CAP_ANY


def open_index(index: int):
    """The camera at `index` of list_cameras(), opened the same way, or None."""
    import cv2
    cap = cv2.VideoCapture(int(index), backend())
    if not cap.isOpened():
        cap.release()
        return None
    return cap


def open_fast(index: int, want: tuple[int, int, int] | None = None):
    """The camera at `index` of list_cameras() opened the way that gives the
    most frames a second, or None (then open_index is the way). Windows: the
    device of the same name (the same n-th of that name) in Media
    Foundation's list. Elsewhere: None - one way to open it.

    `want`: (width, height, frames a second) - asked AT the opening: once its
    stream runs, Media Foundation answers a new size by sending frames OpenCV
    cannot take (measured: 0 good frames of 76)."""
    if sys.platform != "win32":
        return None
    import cv2
    try:
        cams = list_cameras()
        name = next(c["name"] for c in cams if c["index"] == int(index))
    except (StopIteration, Exception):
        return None
    mf = _media_foundation_names()
    if not mf:
        return None
    base, nth = name, 1                       # list_cameras() writes a second "X" as "X (2)"
    if name.endswith(")") and " (" in name:
        head, _, tail = name.rpartition(" (")
        if tail[:-1].isdigit():
            base, nth = head, int(tail[:-1])
    hits = [i for i, n in enumerate(mf) if n == base]
    if len(hits) < nth:
        return None
    params = []
    if want:
        params = [cv2.CAP_PROP_FRAME_WIDTH, int(want[0]), cv2.CAP_PROP_FRAME_HEIGHT, int(want[1]),
                  cv2.CAP_PROP_FPS, int(want[2])]
    cap = cv2.VideoCapture(hits[nth - 1], cv2.CAP_MSMF, params)
    if not cap.isOpened():
        cap.release()
        return None
    # Media Foundation starts the stream after opening: its first reads fail
    # for a moment (~0.5 s measured) - a camera warming up, not one that fails.
    deadline = time.monotonic() + WARM_UP
    while time.monotonic() < deadline:
        try:
            ok, _frame = cap.read()
        except Exception:
            ok = False
        if ok:
            return cap
        time.sleep(0.05)
    cap.release()
    return None


def _numbered(count: int = 6) -> list[dict]:
    out = []
    for i in range(count):
        cap = open_index(i)
        if cap is not None:
            cap.release()
            out.append({"index": i, "name": f"Camera {i + 1}"})
    return out


def list_cameras() -> list[dict]:
    """[{"index", "name"}] - every camera, in OpenCV's numbering. Names are the
    system's own; two devices of one name get "(2)" after the second."""
    with _lock:
        if sys.platform == "win32":
            names = _directshow_names()
            cams = ([{"index": i, "name": n or f"Camera {i + 1}"} for i, n in enumerate(names)]
                    if names is not None else _numbered())
        else:
            cams = _v4l_cameras()
            if cams is None:
                cams = _numbered()
    seen: dict[str, int] = {}
    for c in cams:
        seen[c["name"]] = seen.get(c["name"], 0) + 1
        if seen[c["name"]] > 1:
            c["name"] = f"{c['name']} ({seen[c['name']]})"
    return cams


def chosen(cams: list[dict] | None = None) -> int | None:
    """The chosen camera's index now: found by its name, or the saved number
    when names are not known here. None: automatic, or the chosen camera is
    not connected."""
    try:
        from memory.config_manager import load_api_keys
        cfg = load_api_keys()
    except Exception:
        return None
    name = str(cfg.get("camera_name") or "").strip()
    if name:
        for c in (cams if cams is not None else list_cameras()):
            if c["name"] == name:
                return int(c["index"])
        return None
    idx = cfg.get("camera_index")
    try:
        return None if idx is None else int(idx)
    except (TypeError, ValueError):
        return None


def save_choice(index: int | None, name: str = "") -> None:
    """Remember the user's camera. None: automatic - the first camera that
    films, found in the background and saved as camera_index, the number the
    other camera features read (they must never read an empty one)."""
    from memory.config_manager import update_config
    if index is not None:
        update_config(camera_name=str(name or ""), camera_index=int(index))
        return
    update_config(camera_name="")

    def _find():
        cam = Camera(None)
        if cam.open():
            update_config(camera_index=int(cam.index))
            cam.release()
    threading.Thread(target=_find, daemon=True, name="camera-auto").start()


# ── is it filming ────────────────────────────────────────────────────────────

def _crop(frame: np.ndarray) -> np.ndarray:
    """The middle of the picture, pixels as they are: not scaled, so a real
    sensor's noise stays in it."""
    h, w = frame.shape[:2]
    return frame[h // 2 - h // 6:h // 2 + h // 6, w // 2 - w // 6:w // 2 + w // 6].astype(np.int16)


def _changes(a: np.ndarray, b: np.ndarray) -> bool:
    """Two frames from a camera filming something always differ a little (a
    real sensor has noise); a still picture repeats exactly."""
    return a.shape == b.shape and float(np.abs(a - b).mean()) > STILL_DIFF


def films(cap) -> bool:
    """A camera filming something: detail in the picture AND frame after frame
    that changes. Devices that call themselves cameras also send one flat
    colour or one still picture - a virtual camera whose app is not running
    (NVIDIA Broadcast's placeholder), an IR emitter, a closed shutter - and a
    single pair of frames can differ while one of them starts up."""
    prev, moving = None, 0
    for _ in range(16):
        ok, frame = cap.read()
        if ok and frame is not None:
            cur = _crop(frame)
            if prev is not None and float(frame.std()) > 12.0 and _changes(prev, cur):
                moving += 1
                if moving >= 4:
                    return True
            prev = cur
        time.sleep(0.03)
    return False


STILL_DIFF = 0.05            # mean pixel change between frames of a live camera
STILL_SECONDS = 2.5          # a picture unchanged this long: the camera froze
SKIP_SECONDS = 30.0          # a camera found frozen or empty is passed over this long
PATIENCE = 4.0               # the chosen camera may be busy this long (a restart)
WARM_UP = 3.0                # s Media Foundation may take to send its first frame (open_fast)


class Camera:
    """The camera to watch, chosen and kept honest - on any computer.

    The one chosen in the devices panel (chosen(): by name) comes first and gets a few seconds when it is busy: right after a
    restart the previous run may still hold it. Then every other camera, in
    order. Only a live picture counts (films). While it is used it is watched:
    a picture that stops changing means the camera stopped filming - another
    program took the device, a virtual camera's app closed - and the next one
    is found, the chosen camera first again."""

    def __init__(self, preferred: int | None = None, count: int = 6, fast: bool = False,
                 want: tuple[int, int, int] | None = None):
        self.preferred = preferred
        self.count = count
        self.fast = fast                       # open_fast first (hand tracking: frames matter)
        self.want = want                       # (width, height, fps) for open_fast
        self.how = ""                          # "fast" | "" - how the open camera was opened
        self._known: list[int] | None = None
        self.cap = None
        self.index: int | None = None
        self._skip: dict[int, float] = {}
        self._prev = None
        self._still_since: float | None = None

    def _order(self) -> list[int]:
        now = time.monotonic()
        first = [self.preferred] if self.preferred is not None else []
        if self._known is None:
            try:
                self._known = [c["index"] for c in list_cameras()]
            except Exception:
                self._known = list(range(self.count))
        return [i for i in dict.fromkeys(first + self._known)
                if self._skip.get(i, 0.0) <= now]

    def _open(self, index: int):
        """A camera that films, opened the fast way when asked and possible."""
        if self.fast:
            cap = open_fast(index, self.want)
            if cap is not None:
                if films(cap):
                    self.how = "fast"
                    return cap
                cap.release()
        cap = open_index(index)
        if cap is not None and films(cap):
            self.how = ""
            return cap
        if cap is not None:
            cap.release()
        return None

    def _try(self, index: int) -> bool:
        cap = self._open(index)
        if cap is None:
            self._skip[index] = time.monotonic() + SKIP_SECONDS
            return False
        self.cap, self.index = cap, index
        self._prev, self._still_since = None, None
        if index != self.preferred:
            print(f"[Cameras] using camera {index}"
                  + (f" (camera {self.preferred} shows no live picture now)"
                     if self.preferred is not None else ""), flush=True)
        return True

    def open(self) -> bool:
        if self.cap is not None:
            return True
        if self.preferred is not None and self._skip.get(self.preferred, 0.0) <= time.monotonic():
            deadline = time.monotonic() + PATIENCE
            while True:
                cap = self._open(self.preferred)
                if cap is not None:
                    self.cap, self.index = cap, self.preferred
                    self._prev, self._still_since = None, None
                    return True
                if time.monotonic() >= deadline:
                    self._skip[self.preferred] = time.monotonic() + SKIP_SECONDS / 3
                    break
                time.sleep(0.5)
        return any(self._try(i) for i in self._order() if i != self.preferred)

    def read(self) -> np.ndarray | None:
        """The next frame; None while no camera films anything."""
        if not self.open():
            return None
        try:
            ok, frame = self.cap.read()
        except Exception as e:                 # a driver handing over a frame OpenCV cannot take
            ok, frame = False, None
            print(f"[Cameras] camera {self.index}: {type(e).__name__}: {str(e)[:120]}")
        if not ok or frame is None:
            self._drop(f"camera {self.index} stopped sending pictures")
            return None
        cur = _crop(frame)
        now = time.monotonic()
        if self._prev is not None and not _changes(self._prev, cur):
            self._still_since = self._still_since or now
            if now - self._still_since > STILL_SECONDS:
                self._drop(f"camera {self.index}'s picture froze")
                return None
        else:
            self._still_since = None
        self._prev = cur
        return frame

    def _drop(self, why: str) -> None:
        print(f"[Cameras] {why} - looking for a camera that films", flush=True)
        if self.index is not None:
            self._skip[self.index] = time.monotonic() + SKIP_SECONDS
        self.release()

    def release(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap, self.index = None, None


def open_live(cams: list[dict] | None = None):
    """The first camera that films (the chosen one first), opened - or None."""
    cam = Camera(chosen(cams))
    return cam.cap if cam.open() else None


# ── who holds it ─────────────────────────────────────────────────────────────

def _friendly(app_id: str) -> str:
    """"Microsoft.WindowsCamera_8wekyb3d8bbwe" -> "WindowsCamera";
    "C:#Program Files#obs-studio#bin#64bit#obs64.exe" -> "obs64"."""
    app_id = str(app_id or "")
    if "#" in app_id or app_id.lower().endswith(".exe"):
        name = app_id.replace("#", "\\").split("\\")[-1]
        return name[:-4] if name.lower().endswith(".exe") else name
    return app_id.split("_")[0].split(".")[-1]


def _windows_holders() -> list[str]:
    """Windows keeps, per program, when it last started and stopped using the
    camera (Settings > Privacy > Camera reads the same): a stop of 0 means
    using it now."""
    import winreg
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
    own = {Path(sys.executable).name.lower()}
    out: list[str] = []

    def walk(path: str) -> None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                if sub == "NonPackaged":
                    walk(path + "\\" + sub)
                    continue
                try:
                    with winreg.OpenKey(key, sub) as k:
                        start = winreg.QueryValueEx(k, "LastUsedTimeStart")[0]
                        stop = winreg.QueryValueEx(k, "LastUsedTimeStop")[0]
                except OSError:
                    continue
                if start and not stop:
                    exe = sub.replace("#", "\\").split("\\")[-1].lower()
                    if exe not in own:
                        out.append(_friendly(sub))
    walk(base)
    return out


def _linux_holders() -> list[str]:
    """Programs with a /dev/video device open (their own /proc entries)."""
    import os
    out: list[str] = []
    me = os.getpid()
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit() or int(pid_dir.name) == me:
            continue
        try:
            for fd in (pid_dir / "fd").iterdir():
                if os.readlink(fd).startswith("/dev/video"):
                    out.append((pid_dir / "comm").read_text().strip())
                    break
        except OSError:
            continue
    return out


def in_use_by() -> str:
    """The program holding a camera right now, as a person reads its name -
    "" when none is known (or this system does not say)."""
    try:
        if sys.platform == "win32":
            found = _windows_holders()
        elif sys.platform.startswith("linux"):
            found = _linux_holders()
        else:
            found = []
    except Exception:
        found = []
    return found[0] if found else ""
