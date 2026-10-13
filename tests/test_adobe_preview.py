import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import adobe_preview


class IllustratorPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = pathlib.Path(self.temp.name) / "업무 '; $(echo private).ai"
        self.original = b'%PDF-1.7\nAI fixture'
        self.source.write_bytes(self.original)
        self.folders = []

    def run_export(self, command, **kwargs):
        script = pathlib.Path(command[command.index('-File') + 1])
        source = pathlib.Path(command[command.index('-SourcePath') + 1])
        output = pathlib.Path(command[command.index('-OutputPath') + 1] + '.jpg')
        self.folders.append(script.parent)
        self.assertNotEqual(source, self.source)
        self.assertEqual(source.read_bytes(), self.original)
        self.assertEqual(kwargs['timeout'], adobe_preview.TIMEOUT_SECONDS)
        self.assertNotIn('shell', kwargs)
        self.assertNotIn(str(self.source), script.read_text(encoding='utf-8-sig'))
        self.assertIn('DONOTSAVECHANGES', script.read_text(encoding='utf-8-sig'))
        from PIL import Image
        Image.new('RGB', (100, 40), 'white').save(output, format='JPEG')
        return subprocess.CompletedProcess(command, 0)

    def invoke(self, runner):
        with patch.object(adobe_preview.os, 'name', 'nt'), patch.object(adobe_preview.subprocess, 'run', side_effect=runner):
            return adobe_preview.render_ai_preview(self.source)

    def assert_clean(self):
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertTrue(all(not folder.exists() for folder in self.folders))

    def test_export_is_arguments_only_and_source_unchanged(self):
        result = self.invoke(self.run_export)
        self.assertTrue(result.startswith(b'\xff\xd8'))
        self.assert_clean()

    def test_failure_cleans_script_copy_and_output(self):
        def fail(command, **kwargs):
            self.run_export(command, **kwargs)
            return subprocess.CompletedProcess(command, 1, stderr=b'private details')
        with self.assertRaisesRegex(ValueError, 'Illustrator.*실패'):
            self.invoke(fail)
        self.assert_clean()

    def test_timeout_cleans_temporary_files(self):
        def timeout(command, **kwargs):
            self.run_export(command, **kwargs)
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        with self.assertRaisesRegex(ValueError, '60초'):
            self.invoke(timeout)
        self.assert_clean()

    def test_missing_output_is_actionable(self):
        def missing(command, **kwargs):
            self.folders.append(pathlib.Path(command[command.index('-File') + 1]).parent)
            return subprocess.CompletedProcess(command, 0)
        with self.assertRaisesRegex(ValueError, 'Illustrator.*설치'):
            self.invoke(missing)
        self.assert_clean()

    def test_oversized_output_is_rejected(self):
        def large(command, **kwargs):
            result = self.run_export(command, **kwargs)
            pathlib.Path(command[command.index('-OutputPath') + 1] + '.jpg').write_bytes(b'x' * (adobe_preview.MAX_OUTPUT_BYTES + 1))
            return result
        with self.assertRaisesRegex(ValueError, '2MB'):
            self.invoke(large)
        self.assert_clean()

    def test_oversized_dimensions_are_rejected(self):
        def large(command, **kwargs):
            result = self.run_export(command, **kwargs)
            from PIL import Image
            Image.new('RGB', (1601, 1), 'white').save(command[command.index('-OutputPath') + 1] + '.jpg', format='JPEG')
            return result
        with self.assertRaisesRegex(ValueError, '해상도'):
            self.invoke(large)
        self.assert_clean()

    def test_size_limit_prevents_process_launch(self):
        with patch.object(adobe_preview, 'MAX_SOURCE_BYTES', 2), patch.object(adobe_preview.subprocess, 'run') as runner:
            with self.assertRaisesRegex(ValueError, '64MB'):
                adobe_preview.render_ai_preview(self.source)
            runner.assert_not_called()

    def test_corrupt_jpeg_is_rejected_and_cleaned(self):
        def corrupt(command, **kwargs):
            result = self.run_export(command, **kwargs)
            pathlib.Path(command[command.index('-OutputPath') + 1] + '.jpg').write_bytes(b'not a jpeg')
            return result
        with self.assertRaisesRegex(ValueError, 'JPEG'):
            self.invoke(corrupt)
        self.assert_clean()


if __name__ == '__main__':
    unittest.main()
