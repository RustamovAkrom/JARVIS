from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume


def get_volume_interface():
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def set_volume(level: float):
    volume = get_volume_interface()
    volume.SetMasterVolumeLevelScalar(level, None)


def set_volume_max():
    set_volume(1.0)


def set_volume_mid():
    set_volume(0.5)


def set_volume_min():
    set_volume(0.15)


def mute_volume():
    volume = get_volume_interface()
    volume.SetMute(1, None)


def unmute_volume():
    volume = get_volume_interface()
    volume.SetMute(0, None)
