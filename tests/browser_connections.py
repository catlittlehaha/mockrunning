"""Headless connection-form smoke test; never touches a physical device."""
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright, expect

from ios_location_controller.web import Handler
from ios_location_controller.motion import Settings
from dataclasses import asdict


class Controller:
    def __init__(self):
        self.calls = []
        self.route = {'name':'', 'points':[]}

    def status(self):
        return dict(state='idle', connected=False, udid=None, devices=[],
                    discovery_error=None, error=None, wda_error=None,
                    route=self.route, settings=asdict(Settings()),
                    real_current=None, current=None, speed_kmh=0,
                    total_m=0, distance_m=0, elapsed_s=0, laps=0)

    def call(self, action, data):
        self.calls.append((action, data))
        if action == 'route':
            self.route = {'name':data['name'], 'points':data['points']}
        elif action == 'clear-route':
            self.route = {'name':'', 'points':[]}
        return self.status()


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.controller = Controller()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width':1440, 'height':1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('https://*.tile.openstreetmap.org/**', lambda route: route.abort())
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.locator('#tab-player').click()
            expect(page.locator('#connect')).to_be_disabled()
            assert page.evaluate("""async () => {
              const response = await fetch('/api/route', {method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({name:'browser',points:[
                  {lat:31.23,lng:121.47},{lat:31.24,lng:121.48}]})});
              return response.ok;
            }""")
            expect(page.locator('#clear-loaded-route')).to_be_enabled()
            page.locator('#clear-loaded-route').click()
            expect(page.locator('#clear-loaded-route')).to_be_disabled()
            expect(page.locator('#loaded-route')).to_have_text('未载入路线')
            assert server.controller.calls[-1] == ('clear-route', {})
            page.locator('#transport').select_option('wireless')
            expect(page.locator('#ios-wireless')).to_be_visible()
            expect(page.locator('#connect')).to_be_enabled()
            page.locator('#rsd-host').fill('fd00::1')
            page.locator('#rsd-port').fill('1234')
            page.locator('#connect').click()
            expect(page.locator('#connect')).to_be_enabled()
            assert server.controller.calls[-1] == ('connect', {
                'platform':'ios', 'rsd_host':'fd00::1', 'rsd_port':1234})
            page.locator('#platform').select_option('android')
            expect(page.locator('#android-wireless')).to_be_visible()
            expect(page.locator('#ios-wireless')).to_be_hidden()
            expect(page.locator('#android-test-options')).to_be_visible()
            page.locator('#android-provider').select_option('both')
            page.locator('#gps-accuracy').fill('7')
            page.locator('#network-accuracy').fill('80')
            page.locator('#adb-address').fill('192.168.1.2:5555')
            page.locator('#android-wireless summary').click()
            page.locator('#pair-address').fill('192.168.1.2:12345')
            page.locator('#pair-code').fill('123456')
            page.locator('#pair-device').click()
            expect(page.locator('#pair-device')).to_be_enabled()
            expect(page.locator('#pair-code')).to_have_value('')
            assert server.controller.calls[-1] == ('pair', {
                'address':'192.168.1.2:12345', 'code':'123456'})
            page.locator('#connect').click()
            expect(page.locator('#connect')).to_be_enabled()
            assert server.controller.calls[-1] == ('connect', {
                'platform':'android', 'address':'192.168.1.2:5555',
                'android_options':{'provider':'both', 'gps_accuracy':7,
                                   'network_accuracy':80, 'network_interval':5}})
            page.set_viewport_size({'width':390, 'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            Path('artifacts').mkdir(exist_ok=True)
            page.screenshot(path='artifacts/wireless-mobile.png', full_page=True)
            assert not errors, errors
            browser.close()
        print('PASS: route clearing, iOS RSD, Android pairing/connect, code clearing, mobile layout')
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    main()
