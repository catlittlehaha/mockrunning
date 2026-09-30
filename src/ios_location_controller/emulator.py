"""Transparent Android AVD test inputs; never used by physical-device playback."""
import asyncio
import math
import re

from .android import adb


SENSORS = ("acceleration", "gyroscope", "magnetic-field")


def number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError(f"{name} is out of range") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


class Emulator:
    def __init__(self, serial):
        if not isinstance(serial, str) or not re.fullmatch(r"emulator-[0-9]{1,5}", serial):
            raise ValueError("An explicit Android AVD serial (emulator-PORT) is required")
        port = int(serial.split("-")[1])
        if not 1 <= port <= 65535:
            raise ValueError("Invalid emulator port")
        self.serial = serial

    async def command(self, *args):
        output = await adb("-s", self.serial, "emu", *args)
        if any(line.lstrip().startswith("KO") for line in output.splitlines()):
            raise RuntimeError("Android emulator rejected the command: " + output[:500])
        return output

    async def connect(self):
        if await adb("-s", self.serial, "get-state") != "device":
            raise RuntimeError("Android emulator is offline or unauthorized")
        # Verify the console really belongs to a running AVD, not just a matching serial.
        output = await self.command("avd", "name")
        names = [line.strip() for line in output.splitlines() if line.strip() not in ("", "OK")]
        if not names:
            raise RuntimeError("Cannot verify the Android AVD")

    async def fix(self, latitude, longitude, altitude=0, satellites=8, speed_kmh=0):
        lat = number(latitude, "latitude", -90, 90)
        lon = number(longitude, "longitude", -180, 180)
        altitude = number(altitude, "altitude", -1000, 100000)
        speed = number(speed_kmh, "speed_kmh", 0, 300)
        if isinstance(satellites, bool) or not isinstance(satellites, int) or not 1 <= satellites <= 12:
            raise ValueError("satellites must be an integer between 1 and 12")
        await self.connect()
        # The console uses longitude first and velocity in knots, not km/h.
        await self.command("geo", "fix", str(lon), str(lat), str(altitude),
                           str(satellites), str(speed / 1.852))

    async def sensor(self, name, x, y, z, duration=1):
        if name not in SENSORS:
            raise ValueError("Unsupported emulator sensor")
        values = [number(v, "sensor value", -100000, 100000) for v in (x, y, z)]
        duration = number(duration, "duration", 0.1, 60)
        await self.connect()
        output = await self.command("sensor", "get", name)
        match = re.search(r"^" + re.escape(name) + r"\s*=\s*([^\r\n]+)", output, re.MULTILINE)
        if not match:
            raise RuntimeError("Cannot read the original emulator sensor value; no injection performed")
        components = match[1].strip().split(":")
        try:
            if len(components) != 3:
                raise ValueError()
            original = [number(float(v), "original sensor value", -100000, 100000) for v in components]
        except ValueError:
            raise RuntimeError("Invalid original emulator sensor value; no injection performed") from None
        # Restore even when the injection command times out after changing the AVD.
        try:
            await self.command("sensor", "set", name, ":".join(map(str, values)))
            await asyncio.sleep(duration)
        finally:
            try:
                await self.command("sensor", "set", name, ":".join(map(str, original)))
            except Exception as exc:
                raise RuntimeError("Emulator sensor restoration failed; test values may remain active") from exc
