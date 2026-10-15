import json
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import windows_app_catalog as apps


class WindowsCatalogTests(unittest.TestCase):
    def setUp(self):
        apps.clear_cache()

    def test_codex_package_has_correct_name_and_preserves_windows_label(self):
        with patch.object(apps, '_read_apps', return_value=[{'Name': 'ChatGPT', 'AppID': 'OpenAI.Codex_123!App'}]):
            item = apps.catalog()['items'][0]
            self.assertEqual(item['title'], 'Codex')
            self.assertIn('ChatGPT', item['aliases'])
            self.assertIn('ChatGPT', item['detail'])
            self.assertEqual(apps.resolve(item['target']), 'OpenAI.Codex_123!App')

    def test_full_start_catalog_deduplicates_identity_not_title(self):
        rows = [{'Name': f'App {i}', 'AppID': f'App.{i}'} for i in range(80)]
        rows += [{'Name': 'ChatGPT', 'AppID': 'OpenAI.ChatGPT_abc!App'}, rows[0],
                 {'Name': 'App 0', 'AppID': 'OtherEdition'}]
        with patch.object(apps, '_read_apps', return_value=rows) as read:
            result = apps.catalog()
            self.assertEqual(result['total'], 82)
            self.assertTrue(result['available'])
            chat = next(i for i in result['items'] if i['title'] == 'ChatGPT')
            self.assertRegex(chat['target'], r'^startapp:[0-9a-f]{64}$')
            self.assertEqual(apps.resolve(chat['target']), 'OpenAI.ChatGPT_abc!App')
            apps.catalog()
            read.assert_called_once()

    def test_failed_provider_and_unknown_reference(self):
        with patch.object(apps, '_read_apps', side_effect=OSError('private path')):
            result = apps.catalog()
            self.assertFalse(result['available'])
            self.assertNotIn('private path', json.dumps(result))
        with self.assertRaises(ValueError):
            apps.resolve('startapp:' + '0' * 64)

    def test_launch_refreshes_and_passes_only_trusted_id_as_argument(self):
        with patch.object(apps, '_read_apps', return_value=[{'Name': 'ChatGPT', 'AppID': 'OpenAI.ChatGPT!App'}]), \
                patch.object(apps, '_explorer', return_value=r'C:\Windows\explorer.exe'), \
                patch.object(apps.subprocess, 'Popen') as launch:
            target = apps.catalog()['items'][0]['target']
            apps.launch(target)
            self.assertEqual(launch.call_args.args[0], [r'C:\Windows\explorer.exe', r'shell:AppsFolder\OpenAI.ChatGPT!App'])
            self.assertIs(launch.call_args.kwargs['shell'], False)
        with patch.object(apps, '_read_apps', return_value=[]), patch.object(apps.subprocess, 'Popen') as launch:
            with self.assertRaises(ValueError):
                apps.launch(target)
            launch.assert_not_called()

    def test_rejects_control_shell_namespace_or_oversize_provider_values(self):
        rows = [{'Name': 'bad', 'AppID': value} for value in ['bad\napp', 'shell:AppsFolder\\bad', '../bad', 'x' * 2049]]
        with patch.object(apps, '_read_apps', return_value=rows):
            self.assertEqual(apps.catalog()['items'], [])

    def test_known_folder_win32_app_ids_are_included_but_external_urls_excluded(self):
        app_id = r'{6D809377-6AF0-444B-8957-A3773F02200E}\Adobe\Adobe Photoshop 2026\Photoshop.exe'
        with patch.object(apps, '_read_apps', return_value=[{'Name': 'Photoshop', 'AppID': app_id}, {'Name': 'Report download', 'AppID': 'https://download.example.com/report.exe'}]):
            result = apps.catalog()
            self.assertEqual(result['total'], 1)
            self.assertEqual(apps.resolve(result['items'][0]['target']), app_id)
            self.assertEqual(result['filtered'], 1)
            self.assertTrue(result['warnings'])
        for bad in [r'{6D809377-6AF0-444B-8957-A3773F02200E}\..\bad.exe', r'\\server\share\bad.exe']:
            self.assertFalse(apps._valid_app_id(bad))
