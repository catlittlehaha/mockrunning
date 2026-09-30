import asyncio
from unittest.mock import AsyncMock

import pytest

from ios_location_controller import emulator
from ios_location_controller.cli import build_parser, run


@pytest.mark.parametrize("serial", [None, "device123", "192.168.1.1:5555", "emulator-0",
                                   "emulator-65536", "emulator-5554;id"])
def test_requires_emulator_serial(serial):
    with pytest.raises(ValueError):
        emulator.Emulator(serial)


def test_gps_fix_order_and_units(monkeypatch):
    command = AsyncMock(side_effect=['device', 'Test_AVD\nOK', 'OK'])
    monkeypatch.setattr(emulator, 'adb', command)
    asyncio.run(emulator.Emulator('emulator-5554').fix(31, 121, 20, 9, 18.52))
    assert command.await_args.args == ('-s', 'emulator-5554', 'emu', 'geo', 'fix',
                                      '121.0', '31.0', '20.0', '9', '10.0')


@pytest.mark.parametrize('options', [{'latitude':float('nan')}, {'longitude':181},
    {'altitude':float('inf')}, {'satellites':0}, {'satellites':13}, {'satellites':True},
    {'speed_kmh':-1}, {'latitude':10 ** 400}])
def test_invalid_fix_does_not_touch_device(monkeypatch, options):
    command = AsyncMock()
    monkeypatch.setattr(emulator, 'adb', command)
    with pytest.raises(ValueError):
        asyncio.run(emulator.Emulator('emulator-5554').fix(**({'latitude':31,'longitude':121} | options)))
    command.assert_not_awaited()


@pytest.mark.parametrize('response', ['KO: unknown sensor', 'OK', 'gyroscope = 1:2',
                                    'gyroscope = nan:0:0'])
def test_sensor_requires_readable_original_values(monkeypatch, response):
    command = AsyncMock(side_effect=['device', 'Test_AVD\nOK', response])
    monkeypatch.setattr(emulator, 'adb', command)
    with pytest.raises(RuntimeError):
        asyncio.run(emulator.Emulator('emulator-5554').sensor('gyroscope', 1, 2, 3))
    assert not any('set' in c.args for c in command.await_args_list)


def test_sensor_restored_after_normal_completion(monkeypatch):
    command = AsyncMock(side_effect=['device', 'Test_AVD\nOK', 'gyroscope = 0:1e-3:-0.1\nOK', 'OK', 'OK'])
    monkeypatch.setattr(emulator, 'adb', command)
    asyncio.run(emulator.Emulator('emulator-5554').sensor('gyroscope', .1, .2, .3, .1))
    assert command.await_args_list[-2].args[-2:] == ('gyroscope', '0.1:0.2:0.3')
    assert command.await_args.args[-2:] == ('gyroscope', '0.0:0.001:-0.1')


def test_sensor_restored_on_cancellation(monkeypatch):
    injected = asyncio.Event()
    calls = []

    async def command(*args):
        calls.append(args)
        if args[-1] == 'get-state':
            return 'device'
        if args[-2:] == ('avd', 'name'):
            return 'Test_AVD\nOK'
        if 'get' in args:
            return 'acceleration = 0:0:9.8\nOK'
        injected.set()
        return 'OK'

    monkeypatch.setattr(emulator, 'adb', command)

    async def scenario():
        task = asyncio.create_task(emulator.Emulator('emulator-5554').sensor('acceleration', 1, 2, 3, 60))
        await injected.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls[-1][-2:] == ('acceleration', '0.0:0.0:9.8')
    asyncio.run(scenario())


def test_failed_sensor_write_attempts_restoration(monkeypatch):
    command = AsyncMock(side_effect=['device', 'Test_AVD\nOK', 'gyroscope = 0:0:0\nOK',
                                    RuntimeError('timeout'), 'OK'])
    monkeypatch.setattr(emulator, 'adb', command)
    with pytest.raises(RuntimeError, match='timeout'):
        asyncio.run(emulator.Emulator('emulator-5554').sensor('gyroscope', 1, 2, 3))
    assert command.await_args.args[-1] == '0.0:0.0:0.0'


def test_cli_emulator_fix(monkeypatch, capsys):
    command = AsyncMock(side_effect=['device', 'Test_AVD\nOK', 'OK'])
    monkeypatch.setattr(emulator, 'adb', command)
    args = build_parser().parse_args(['emulator', '--serial', 'emulator-5554', 'fix',
                                     '--latitude', '31', '--longitude', '121', '--satellites', '9'])
    asyncio.run(run(args))
    assert 'not raw GNSS' in capsys.readouterr().out


def test_offline_emulator_cannot_inject(monkeypatch):
    command = AsyncMock(return_value='offline')
    monkeypatch.setattr(emulator, 'adb', command)
    with pytest.raises(RuntimeError, match='offline'):
        asyncio.run(emulator.Emulator('emulator-5554').fix(31, 121))
    assert command.await_count == 1


def test_restore_failure_is_not_reported_as_success(monkeypatch):
    command = AsyncMock(side_effect=['device', 'Test_AVD\nOK', 'gyroscope = 0:0:0\nOK',
                                    'OK', RuntimeError('offline')])
    monkeypatch.setattr(emulator, 'adb', command)
    with pytest.raises(RuntimeError, match='restoration failed'):
        asyncio.run(emulator.Emulator('emulator-5554').sensor('gyroscope', 1, 2, 3, .1))


@pytest.mark.parametrize('options', [{'name':'unknown'}, {'x':float('nan')}, {'duration':0},
                                   {'duration':61}, {'z':True}])
def test_sensor_validation_happens_before_device_io(monkeypatch, options):
    command = AsyncMock()
    monkeypatch.setattr(emulator, 'adb', command)
    values = {'name':'gyroscope', 'x':1, 'y':2, 'z':3} | options
    with pytest.raises(ValueError):
        asyncio.run(emulator.Emulator('emulator-5554').sensor(**values))
    command.assert_not_awaited()
