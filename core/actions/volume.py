from pycaw.pycaw import AudioUtilities


def _get_volume_interface():
    device = AudioUtilities.GetSpeakers()
    volume = device.EndpointVolume
    return volume


def set_volume(value: float):
    """
    value: 0.0 -> 1.0
    """

    volume = _get_volume_interface()

    # clamp safety
    value = max(0.0, min(1.0, value))

    volume.SetMute(0, None)  # auto unmute

    volume.SetMasterVolumeLevelScalar(value, None)

    print(f"🔊 Volume set to {int(value * 100)}%")


def set_volume_max():
    set_volume(1.0)


def set_volume_mid():
    set_volume(0.5)


def set_volume_min():
    set_volume(0.0)
