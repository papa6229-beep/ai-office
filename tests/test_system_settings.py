import unittest
from unittest import mock

import system_settings


class Key:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Registry:
    HKEY_CURRENT_USER = 'user'
    HKEY_LOCAL_MACHINE = 'machine'
    KEY_READ = 1
    KEY_WOW64_64KEY = 2
    KEY_WOW64_32KEY = 4
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(self):
        self.keys = {
            ('user', r'Software\RegisteredApplications'): {
                'Chrome & Test?': r'Software\Chrome\Capabilities',
                'Unsafe\nName': 'unused',
                'Not an application': 42,
            },
            ('machine', r'Software\RegisteredApplications'): {
                'Google Chrome': r'Software\Clients\StartMenuInternet\Chrome\Capabilities',
            },
            ('user', r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice'): {
                'ProgId': 'ChromeHTML',
            },
            ('user', r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice'): {
                'ProgId': 'MSEdgeHTM',
            },
        }
        self.accesses = []

    def OpenKey(self, root, path, reserved=0, access=1):
        self.accesses.append(access)
        if (root, path) not in self.keys:
            raise FileNotFoundError()
        return Key(self.keys[root, path])

    def EnumValue(self, key, index):
        try:
            name, value = list(key.values.items())[index]
        except IndexError:
            raise OSError(259, 'No more data')
        return name, value, self.REG_SZ

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError()
        return key.values[name], self.REG_SZ


class SystemSettingsTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()

    def test_catalog_is_bounded_read_only_deduplicated_and_has_stable_ids(self):
        apps = system_settings.registered_apps(registry=self.registry)
        self.assertEqual(len(apps), 2)
        self.assertEqual({a['name'] for a in apps}, {'Google Chrome', 'Chrome & Test?'})
        self.assertEqual(apps, system_settings.registered_apps(registry=self.registry))
        self.assertTrue(all(set(a) == {'id', 'name', 'scope'} for a in apps))
        self.assertTrue(all(access & self.registry.KEY_READ for access in self.registry.accesses))

    def test_status_labels_userchoice_as_configured_not_effective(self):
        result = system_settings.status(registry=self.registry)
        self.assertTrue(result['available'])
        self.assertIn('default_apps', result['sections'])
        self.assertEqual(result['web_associations']['http']['prog_id'], 'ChromeHTML')
        self.assertEqual(result['web_associations']['https']['prog_id'], 'MSEdgeHTM')
        self.assertEqual(result['web_associations']['http']['source'], 'current_user_userchoice')
        self.assertFalse(result['effective_default_verified'])

    def test_missing_association_is_unknown_not_default_chrome(self):
        del self.registry.keys['user', r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice']
        value = system_settings.status(registry=self.registry)['web_associations']['https']
        self.assertIsNone(value['prog_id'])
        self.assertFalse(value['known'])

    def test_only_fixed_section_uri_can_be_dispatched(self):
        opened = []
        result = system_settings.open_section('display', registry=self.registry, opener=opened.append)
        self.assertEqual(opened, ['ms-settings:display'])
        self.assertTrue(result['dispatched'])
        self.assertFalse(result['setting_changed'])
        self.assertTrue(result['requires_ui_verification'])
        for section in ['ms-settings:display', 'display?x=y', '../x', '', None, [], 'shell:AppsFolder']:
            with self.subTest(section=section), self.assertRaises(ValueError):
                system_settings.open_section(section, registry=self.registry, opener=opened.append)
        self.assertEqual(len(opened), 1)

    def test_registered_application_target_is_revalidated_and_uri_escaped(self):
        apps = system_settings.registered_apps(registry=self.registry)
        user = next(a for a in apps if a['scope'] == 'user')
        machine = next(a for a in apps if a['scope'] == 'machine')
        opened = []
        result = system_settings.open_section('default_apps', user['id'], registry=self.registry, opener=opened.append)
        self.assertEqual(opened[-1], 'ms-settings:defaultapps?registeredAppUser=Chrome%20%26%20Test%3F')
        self.assertEqual(result['application'], user)
        system_settings.open_section('default_apps', machine['id'], registry=self.registry, opener=opened.append)
        self.assertEqual(opened[-1], 'ms-settings:defaultapps?registeredAppMachine=Google%20Chrome')
        del self.registry.keys['user', r'Software\RegisteredApplications']['Chrome & Test?']
        with self.assertRaises(ValueError):
            system_settings.open_section('default_apps', user['id'], registry=self.registry, opener=opened.append)
        for app_id in ['Google Chrome', 'ms-settings:x', '', [], 42]:
            with self.subTest(app_id=app_id), self.assertRaises(ValueError):
                system_settings.open_section('default_apps', app_id, registry=self.registry, opener=opened.append)
        with self.assertRaises(ValueError):
            system_settings.open_section('sound', machine['id'], registry=self.registry, opener=opened.append)
        self.assertEqual(len(opened), 2)

    def test_launch_failure_is_not_reported_as_success_or_leaked(self):
        def fail(uri):
            raise OSError('secret local path')
        with self.assertRaisesRegex(RuntimeError, 'Windows') as caught:
            system_settings.open_section('default_apps', registry=self.registry, opener=fail)
        self.assertNotIn('secret', str(caught.exception))

    def test_false_launch_return_does_not_claim_dispatched(self):
        with self.assertRaises(RuntimeError):
            system_settings.open_section('default_apps', registry=self.registry, opener=lambda uri: False)

    def test_registry_failure_does_not_expose_details(self):
        class Broken(Registry):
            def OpenKey(self, *args):
                raise PermissionError('secret path')
        result = system_settings.status(registry=Broken())
        self.assertEqual(result['registered_apps'], [])
        self.assertFalse(result['web_associations']['http']['known'])
        self.assertNotIn('secret', str(result))

    def test_unavailable_platform_is_explicit_and_never_dispatches(self):
        with mock.patch.object(system_settings.os, 'name', 'posix'):
            result = system_settings.status()
            self.assertFalse(result['available'])
            self.assertEqual(result['registered_apps'], [])
            self.assertFalse(result['web_associations']['http']['known'])
            with self.assertRaises(RuntimeError):
                system_settings.open_section('default_apps')

    def test_endless_registry_provider_is_bounded(self):
        class Endless(Registry):
            def EnumValue(self, key, index):
                return 'app' + str(index), 'Capabilities', self.REG_SZ
        self.assertEqual(len(system_settings.registered_apps(registry=Endless())), 1024)

    def test_associations_reject_non_string_registry_values(self):
        self.registry.keys['user', r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice']['ProgId'] = 7
        self.assertFalse(system_settings.status(registry=self.registry)['web_associations']['http']['known'])


if __name__ == '__main__':
    unittest.main()
