"""ADB transport for Android's system test providers (no root or APK)."""
import asyncio
import ipaddress
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass

from .device import LocationDevice


@dataclass(frozen=True)
class AndroidOptions:
    """Explicit test conditions, not a model of real radio or sensor readings."""

    provider: str = "gps"
    gps_accuracy: float = 5.0
    network_accuracy: float = 50.0
    network_interval: float = 5.0

    @classmethod
    def parse(cls, value=None):
        if value is None:
            return cls()
        if not isinstance(value, dict) or value.keys() - cls.__dataclass_fields__.keys():
            raise ValueError("Invalid Android test options")
        options = asdict(cls()) | value
        if options["provider"] not in ("gps", "network", "both"):
            raise ValueError("Android provider must be gps, network or both")
        for name, maximum in (("gps_accuracy", 10000), ("network_accuracy", 10000),
                              ("network_interval", 60)):
            number = options[name]
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError(f"{name} must be a number")
            try:
                number = float(number)
            except OverflowError:
                raise ValueError(f"{name} is out of range") from None
            if not math.isfinite(number) or not 0.1 <= number <= maximum:
                raise ValueError(f"{name} must be between 0.1 and {maximum}")
            options[name] = number
        return cls(**options)


def endpoint(value):
    if not isinstance(value, str):
        raise ValueError("Expected an IP address and port")
    host, sep, port = value.rpartition(":")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
        if not sep or not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError()
        if address.is_unspecified or address.is_multicast:
            raise ValueError()
    except ValueError:
        raise ValueError("Expected IP:port (IPv6: [address]:port)") from None
    return f"[{address}]:{int(port)}" if address.version == 6 else f"{address}:{int(port)}"


async def adb(*args, input_text=None):
    executable = os.environ.get("ADB_PATH") or shutil.which("adb")
    if not executable:
        raise RuntimeError("Android requires Android SDK Platform-Tools: adb on PATH or ADB_PATH")
    process = await asyncio.create_subprocess_exec(
        executable, *args, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        out, err = await asyncio.wait_for(
            process.communicate(input_text.encode() if input_text else None), 15)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    text = (out + err).decode("utf-8", errors="replace").strip()
    if process.returncode or any(s in text.lower() for s in
            ("error:", "exception", "failed", "cannot connect", "unknown command")):
        # Pairing codes must not be reflected into status or logs.
        raise RuntimeError("ADB pairing failed" if input_text else text[:500] or "ADB command failed")
    return text


async def discover():
    output = await adb("devices", "-l")
    return [{"udid": row[0], "platform": "android", "type": "ADB / " + row[1]}
            for line in output.splitlines()
            if len(row := line.split()) >= 2 and row[1] in ("device", "offline", "unauthorized")]


async def pair(address, code):
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        raise ValueError("Pairing code must contain six digits")
    await adb("pair", endpoint(address), input_text=code + "\n")


class AndroidDevice(LocationDevice):
    # Reuse the CLI route player; all actual device I/O is overridden here.
    def __init__(self, udid=None, address=None, options=None):
        self.udid = udid
        self.address = endpoint(address) if address else None
        self.providers = []
        self.original_mode = None
        self.options = AndroidOptions.parse(options)
        self.last_updates = {}
        self.injections = {}

    def diagnostics(self):
        return {"horizontal_accuracy": True, "sensors": False, "raw_gnss": False,
                "mock": True, "options": asdict(self.options),
                "injections": {name: dict(value) for name, value in self.injections.items()}}

    async def shell(self, *args):
        if not self.udid:
            raise RuntimeError("No Android device selected")
        return await adb("-s", self.udid, "shell", *args)

    async def connect(self):
        if self.address:
            await adb("connect", self.address)
            self.udid = self.address
        if not self.udid:
            devices = [d for d in await discover() if d["type"] == "ADB / device"]
            if len(devices) != 1:
                raise ValueError("Select exactly one authorized Android device")
            self.udid = devices[0]["udid"]
        if await adb("-s", self.udid, "get-state") != "device":
            raise RuntimeError("Android device is offline or unauthorized")
        help_text = await self.shell("cmd", "location", "help")
        if "set-test-provider-location" not in help_text:
            raise RuntimeError("This Android ROM lacks ADB test-provider support (use Android 12+)")
        if await self.shell("cmd", "location", "is-location-enabled") != "true":
            raise RuntimeError("Android system location is disabled")
        mode = await self.shell("appops", "get", "com.android.shell", "android:mock_location")
        match = re.search(r"(?:MOCK_LOCATION|mock_location):\s*(allow|ignore|deny|default|foreground)", mode)
        if not match and "No operations" not in mode:
            raise RuntimeError("Cannot determine original mock-location permission")
        self.original_mode = match[1] if match else "default"
        await self.shell("appops", "set", "com.android.shell", "android:mock_location", "allow")

    async def set_route_point(self, point):
        return await self.set_point(point, force=False)

    async def set_point(self, point, force=True):
        lat, lon = point.latitude, point.longitude
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Invalid coordinates")
        if self.original_mode is None:
            raise RuntimeError("Android is not connected")
        now = time.monotonic()
        selected = ("gps", "network") if self.options.provider == "both" else (self.options.provider,)
        sent = False
        for provider in selected:
            # The network test source has its own cadence; never backfill missed updates.
            if (not force and provider == "network" and provider in self.last_updates
                    and now - self.last_updates[provider] < self.options.network_interval):
                continue
            if provider not in self.providers:
                await self.shell("cmd", "location", "providers", "add-test-provider", provider)
                self.providers.append(provider)
                await self.shell("cmd", "location", "providers", "set-test-provider-enabled", provider, "true")
            accuracy = getattr(self.options, provider + "_accuracy")
            await self.shell("cmd", "location", "providers", "set-test-provider-location", provider,
                             "--location", f"{lat:.8f},{lon:.8f}", "--accuracy", str(accuracy))
            self.last_updates[provider] = time.monotonic()
            self.injections[provider] = {"lat": lat, "lng": lon, "accuracy_m": accuracy}
            sent = True
        return sent

    async def clear(self, existing=False):
        if existing:
            # Explicit CLI recovery also clears providers left by a previous run.
            self.providers = list(dict.fromkeys(self.providers + ["gps", "network"]))
        errors = []
        for provider in list(self.providers):
            try:
                await self.shell("cmd", "location", "providers", "remove-test-provider", provider)
                self.providers.remove(provider)
                self.last_updates.pop(provider, None)
                self.injections.pop(provider, None)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("Android cleanup failed; reconnect to restore location: " + "; ".join(errors))

    async def close(self):
        await self.clear()
        if self.original_mode is not None:
            await self.shell("appops", "set", "com.android.shell", "android:mock_location", self.original_mode)
            self.original_mode = None

    async def read_location(self):
        raise RuntimeError("Android displays sent coordinates only, not real GPS readback")
