import base64
import io
import json
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import local_image_review


class LocalImageReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / 'sample.png'
        from PIL import Image
        Image.new('RGB', (40, 30), 'red').save(self.path)
        self.files = [{'path': str(self.path), 'name': 'sample.png', 'extension': '.png'}]
        self.settings = {'gemma_url': 'http://127.0.0.1:11434', 'gemma_model': 'gemma4:26b'}

    def psd_item(self, width=40, height=30, pixels=True):
        path = self.path.with_suffix('.psd')
        # PSD v1, RGB 8-bit, no color/resource/layer sections, raw composite.
        header = b'8BPS' + struct.pack('>H6sHIIHH', 1, b'\0' * 6, 3, height, width, 8, 3)
        composite = b'\xff' * (width * height) + b'\0' * (width * height * 2) if pixels else b''
        path.write_bytes(header + b'\0' * 12 + b'\0\0' + composite)
        return {'path': str(path), 'name': path.name, 'extension': '.psd'}

    def test_psd_composite_is_jpeg_and_source_remains_unchanged(self):
        item = self.psd_item(1800, 2)
        path = pathlib.Path(item['path'])
        before = path.read_bytes()
        payloads = []
        result = local_image_review.analyze([item], self.settings,
            lambda _, data: payloads.append(data) or '{"confidence":0.8}')
        from PIL import Image
        payload = base64.b64decode(payloads[0])
        with Image.open(io.BytesIO(payload)) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertLessEqual(max(image.size), 1600)
            self.assertFalse(image.getexif())
            self.assertNotIn('icc_profile', image.info)
            self.assertGreater(image.getpixel((0, 0))[0], 200)
        self.assertLessEqual(len(payload), local_image_review.MAX_ENCODED_BYTES)
        self.assertEqual(result['items'][0]['state'], '검토 후보')
        self.assertEqual(path.read_bytes(), before)

    def test_psd_header_bounds_are_rejected_before_pillow_decoding(self):
        for width, height in [(0, 30), (8193, 1), (5000, 5000)]:
            item = self.psd_item(width, height, pixels=False)
            with self.subTest(size=(width, height)), patch('PIL.Image.open') as decoder, self.assertRaisesRegex(ValueError, '해상도'):
                local_image_review.analyze([item], self.settings, lambda *args: self.fail('model called'))
            decoder.assert_not_called()

    def test_psd_type_source_and_encoded_size_limits(self):
        item = self.psd_item()
        with patch.object(local_image_review, 'MAX_BYTES', 10), self.assertRaisesRegex(ValueError, '8MB'):
            local_image_review.analyze([item], self.settings, lambda *args: self.fail('model called'))
        with patch.object(local_image_review, 'MAX_ENCODED_BYTES', 10), self.assertRaisesRegex(ValueError, '전송 크기'):
            local_image_review.analyze([item], self.settings, lambda *args: self.fail('model called'))
        pathlib.Path(item['path']).write_bytes(self.path.read_bytes())
        with self.assertRaises(ValueError):
            local_image_review.analyze([item], self.settings, lambda *args: self.fail('model called'))
        # PSB v2 must not be treated as the supported PSD v1 container.
        item = self.psd_item()
        path = pathlib.Path(item['path'])
        path.write_bytes(path.read_bytes()[:4] + b'\x00\x02' + path.read_bytes()[6:])
        with self.assertRaises(ValueError):
            local_image_review.analyze([item], self.settings, lambda *args: self.fail('model called'))

    def test_missing_pillow_psd_is_actionable_and_never_calls_model(self):
        item = self.psd_item()
        with patch.dict(sys.modules, {'PIL': None}), self.assertRaisesRegex(ValueError, 'PSD.*Pillow.*pip install Pillow'):
            local_image_review.analyze([item], self.settings, lambda *args: self.fail('model called'))

    def test_payload_is_bounded_raster_without_metadata(self):
        calls = []
        def model(prompt, image_b64):
            calls.append((prompt, image_b64))
            return json.dumps({'project': 'Design', 'task': 'Review artwork', 'confidence': .8, 'reason': 'Red artwork'})
        result = local_image_review.analyze(self.files, self.settings, model)
        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(calls[0][1]))) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (40, 30))
            self.assertFalse(image.getexif())
        self.assertNotIn(str(self.path.parent), calls[0][0])
        self.assertEqual(result['items'][0]['state'], '검토 후보')
        self.assertEqual(result['items'][0]['name'], 'sample.png')

    def test_ai_uses_temporary_illustrator_composite_and_preserves_source(self):
        from unittest.mock import patch
        from PIL import Image
        import adobe_preview
        ai = self.path.with_suffix('.ai')
        original = b'illustrator source bytes'
        ai.write_bytes(original)
        fixture = io.BytesIO()
        Image.new('RGB', (16, 12), 'purple').save(fixture, format='JPEG')
        encoded = []
        item = {'path': str(ai), 'name': ai.name, 'extension': '.ai'}
        with patch.object(adobe_preview, 'render_ai_preview', return_value=fixture.getvalue()) as export:
            result = local_image_review.analyze([item], self.settings, lambda prompt, image: encoded.append(image) or json.dumps({'confidence': .2}))
        export.assert_called_once_with(ai.resolve())
        self.assertEqual(base64.b64decode(encoded[0]), fixture.getvalue())
        self.assertEqual(ai.read_bytes(), original)
        self.assertEqual(result['items'][0]['state'], '보류')

    def test_unsafe_endpoint_rejected_before_model_or_file_access(self):
        for url in ['http://server:invalid', 'file:///x', 'http://127.0.0.1@example.com', 'http://localhost@example.com']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                local_image_review.analyze(self.files, dict(self.settings, gemma_url=url), lambda *args: self.fail('model called'))

    def test_configured_lan_server_receives_selected_image_without_changes(self):
        before = self.path.read_bytes()
        calls = []
        result = local_image_review.analyze(self.files, dict(self.settings, gemma_url='http://192.168.0.40:11434'),
            lambda prompt, image: calls.append(image) or '{"confidence":0.8}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['items'][0]['state'], '검토 후보')
        self.assertEqual(self.path.read_bytes(), before)

    def test_non_vision_models_rejected(self):
        for name in ['gemma2:27b', 'gemma3:1b', 'llama3', 'gemma3:270m', 'gemma4:cloud', 'gemma4:26b-cloud']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                local_image_review.analyze(self.files, dict(self.settings, gemma_model=name), lambda *args: self.fail('model called'))

    def test_vision_override_accepts_text_primary_and_trims_override(self):
        calls = []
        result = local_image_review.analyze(self.files,
            dict(self.settings, gemma_model='hf.co/example/SuperGemma:Q4_K_M', vision_model=' gemma3:12b '),
            lambda *args: calls.append(args) or '{"confidence":0.8}')
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['items'][0]['state'], '검토 후보')

    def test_invalid_override_is_rejected_and_blank_uses_primary(self):
        for name in ['gemma3:1b', 'gemma4:26b-cloud', 'llama3']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                local_image_review.analyze(self.files, dict(self.settings, vision_model=name),
                    lambda *args: self.fail('model called'))
        local_image_review.analyze(self.files, dict(self.settings, vision_model='  '), lambda *args: '{}')

    def test_type_protected_and_size_limits(self):
        for item in [dict(self.files[0], extension='.svg'), dict(self.files[0], protected=True)]:
            with self.assertRaises(ValueError):
                local_image_review.analyze([item], self.settings, lambda *args: self.fail('model called'))
        with patch.object(local_image_review, 'MAX_BYTES', 10), self.assertRaises(ValueError):
            local_image_review.analyze(self.files, self.settings, lambda *args: self.fail('model called'))
        with patch.object(local_image_review, 'MAX_PIXELS', 10), self.assertRaises(ValueError):
            local_image_review.analyze(self.files, self.settings, lambda *args: self.fail('model called'))
        for files in [[], self.files * (local_image_review.MAX_FILES + 1)]:
            with self.assertRaises(ValueError):
                local_image_review.analyze(files, self.settings, lambda *args: self.fail('model called'))

    def test_low_confidence_invalid_and_nonfinite_predictions_are_deferred(self):
        for raw in ['broken', '{}', '{"confidence":0.2}', '{"confidence":NaN}', '{"confidence":true}', '{"confidence":Infinity}']:
            result = local_image_review.analyze(self.files, self.settings, lambda *args: raw)
            self.assertEqual(len(result['items']), 1)
            self.assertEqual(result['items'][0]['state'], '보류')

    def test_output_fields_are_bounded_and_model_cannot_replace_source(self):
        raw = json.dumps({'name':'outside.png','path':'outside','project':'p'*1000,'task':'t'*1000,'confidence':9,'reason':'r'*1000})
        item = local_image_review.analyze(self.files, self.settings, lambda *args: raw)['items'][0]
        self.assertEqual(item['name'], 'sample.png')
        self.assertEqual(item['confidence'], 1)
        self.assertLessEqual(len(item['project']), 120)
        self.assertLessEqual(len(item['task']), 120)
        self.assertLessEqual(len(item['reason']), 300)
        self.assertNotIn('path', item)

    def test_missing_pillow_uses_bounded_header_fallback(self):
        payloads = []
        with patch.dict(sys.modules, {'PIL': None}):
            result = local_image_review.analyze(self.files, self.settings, lambda _, data: payloads.append(data) or '{}')
        self.assertEqual(base64.b64decode(payloads[0]), self.path.read_bytes())
        self.assertEqual(result['items'][0]['state'], '보류')
        with patch.dict(sys.modules, {'PIL': None}), patch.object(local_image_review, 'MAX_INPUT_EDGE', 10), self.assertRaisesRegex(ValueError, 'Pillow'):
            local_image_review.analyze(self.files, self.settings, lambda *args: self.fail('model called'))

    def test_header_fallback_supports_jpeg_webp_and_rejects_broken_files(self):
        from PIL import Image
        for ext, fmt in [('.jpg', 'JPEG'), ('.webp', 'WEBP')]:
            path = self.path.with_suffix(ext)
            Image.new('RGB', (40, 30), 'red').save(path, format=fmt)
            item = {'path': str(path), 'name': path.name, 'extension': ext}
            with patch.dict(sys.modules, {'PIL': None}):
                local_image_review.analyze([item], self.settings, lambda *args: '{}')
        self.path.write_bytes(b'not a PNG')
        with patch.dict(sys.modules, {'PIL': None}), self.assertRaises(ValueError):
            local_image_review.analyze(self.files, self.settings, lambda *args: self.fail('model called'))

    def test_animated_images_rejected_without_pillow(self):
        from PIL import Image
        for ext, fmt in [('.png', 'PNG'), ('.webp', 'WEBP')]:
            path = self.path.with_suffix(ext)
            Image.new('RGB', (40, 30), 'red').save(path, format=fmt, save_all=True,
                append_images=[Image.new('RGB', (40, 30), 'blue')], duration=100, loop=0)
            with patch.dict(sys.modules, {'PIL': None}), self.assertRaises(ValueError):
                local_image_review.analyze([{'path':str(path), 'extension':ext}], self.settings, lambda *args: self.fail('model called'))


if __name__ == '__main__':
    unittest.main()
