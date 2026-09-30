"""Shared device selection for CLI and local web API."""
from .android import AndroidDevice, endpoint
from .device import LocationDevice


def create_device(platform="ios", udid=None, rsd_host=None, rsd_port=None, address=None, android_options=None):
    if platform not in ("ios", "android"):
        raise ValueError("Platform must be ios or android")
    if udid is not None and (not isinstance(udid, str) or not udid or len(udid) > 256):
        raise ValueError("Invalid device identifier")
    if platform == "android":
        if rsd_host is not None or rsd_port is not None:
            raise ValueError("RSD is only supported for iOS")
        return AndroidDevice(udid, address, android_options)
    if android_options is not None:
        raise ValueError("Android test options are only supported for Android")
    if address:
        raise ValueError("ADB address is only supported for Android")
    if (rsd_host is None) != (rsd_port is None):
        raise ValueError("RSD host and port must be provided together")
    if rsd_host is not None:
        endpoint(f"{rsd_host}:{rsd_port}")
        rsd_host = rsd_host.strip("[]")
        rsd_port = int(rsd_port)
    return LocationDevice(udid, rsd_host, rsd_port)
