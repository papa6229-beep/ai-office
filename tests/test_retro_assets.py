import threading
import unittest
import urllib.error
import urllib.request

import app


class RetroAssetTests(unittest.TestCase):
    def setUp(self):
        self.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_local_theme_and_fonts_have_correct_content_types(self):
        assets = [('/retro.css', 'text/css', b'/*'),
                  ('/assets/fonts/PretendardVariable.woff2', 'font/woff2', b'wOF2'),
                  ('/assets/fonts/BarlowCondensed-Bold.ttf', 'font/ttf', b'\x00\x01\x00\x00'),
                  ('/assets/fonts/BarlowCondensed-ExtraBoldItalic.ttf', 'font/ttf', b'\x00\x01\x00\x00')]
        for path, mime, prefix in assets:
            with self.subTest(path=path), urllib.request.urlopen(self.base+path, timeout=5) as response:
                self.assertEqual(response.headers.get_content_type(), mime)
                self.assertTrue(response.read().startswith(prefix))

    def test_report_uses_shared_local_design(self):
        with urllib.request.urlopen(self.base+'/first-release.html', timeout=5) as response:
            html = response.read().decode('utf-8')
        self.assertIn('href="/retro.css"', html)
        self.assertIn('class="ao-report"', html)

    def test_aemeath_assets_have_correct_content_types_and_bytes(self):
        for state in ('answer', 'idle', 'agree', 'error', 'typing', 'working'):
            for extension, mime, prefix in [('gif', 'image/gif', b'GIF'), ('png', 'image/png', b'\x89PNG')]:
                path = f'/assets/aemeath/{state}.{extension}'
                with self.subTest(path=path), urllib.request.urlopen(self.base+path, timeout=5) as response:
                    self.assertEqual(response.headers.get_content_type(), mime)
                    self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')
                    content = response.read()
                    self.assertTrue(content.startswith(prefix))
                    self.assertEqual(content, (app.ROOT/path.lstrip('/')).read_bytes())

    def test_aemeath_route_exposes_only_known_images(self):
        paths = ['/assets/aemeath/SOURCES.json', '/assets/aemeath/settings.json',
                 '/assets/aemeath/../fonts/PretendardVariable.woff2',
                 '/assets/aemeath/%2e%2e/%2e%2e/settings.json',
                 '/assets/aemeath/unknown.gif', '/assets/aemeath/idle.svg']
        for path in paths:
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base+path, timeout=5)
            self.assertEqual(error.exception.code, 404)

    def test_font_route_does_not_allow_arbitrary_workspace_files(self):
        for path in ['/assets/fonts/settings.json', '/assets/fonts/../../settings.json',
                     '/assets/fonts/%2e%2e/%2e%2e/settings.json']:
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base+path, timeout=5)
            self.assertEqual(error.exception.code, 404)
