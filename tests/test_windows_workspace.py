import threading
import time
import unittest
from types import SimpleNamespace

import windows_workspace as ww


class FakeBackend:
    def __init__(self):
        self.owner = []
        self.window = {'title': 'UIA fixture', 'process': 'fixture.exe', 'pid': 42, 'handle': 123, 'elevated': False}
        self.items = [
            {'runtime_id': (1, 2), 'name': 'Customer', 'control_type': 'Edit', 'enabled': True, 'patterns': ['set_value'], 'value': 'before'},
            {'runtime_id': (1, 3), 'name': 'Save', 'control_type': 'Button', 'enabled': True, 'patterns': ['invoke']},
            {'runtime_id': (1, 4), 'name': 'Password', 'control_type': 'Edit', 'enabled': True, 'password': True, 'patterns': ['set_value'], 'value': 'never-reveal'},
        ]
        self.performed = []

    def windows(self):
        self.owner.append(threading.get_ident())
        return [self.window]

    def describe_window(self, window):
        return dict(window)

    def controls(self, window, max_controls, max_depth):
        return self.items[:max_controls]

    def describe_control(self, control):
        return dict(control)

    def perform(self, control, operation, value):
        self.owner.append(threading.get_ident())
        self.performed.append((control['runtime_id'], operation, value))
        if operation == 'set_value':
            control['value'] = value


class WindowsWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.workspace = ww.WindowsWorkspace(backend=self.backend)

    def tearDown(self):
        self.workspace.close()

    def observe(self):
        window = self.workspace.windows()[0]
        return self.workspace.inspect(window['window_id'])

    def test_opaque_ids_redaction_password_and_single_owner(self):
        observation = self.observe()
        self.assertTrue(observation['window_id'].startswith('win_'))
        self.assertTrue(observation['snapshot_id'].startswith('snap_'))
        self.assertEqual(len(observation['controls']), 3)
        password = observation['controls'][2]
        self.assertTrue(password['password'])
        self.assertEqual(password['patterns'], [])
        self.assertNotIn('never-reveal', str(observation))
        self.assertNotIn('runtime_id', str(observation))
        self.assertNotIn('handle', observation)
        self.assertTrue(all(owner != threading.get_ident() for owner in self.backend.owner))
        self.assertEqual(len(set(self.backend.owner)), 1)

    def test_set_value_verifies_readback_and_consumes_snapshot(self):
        observed = self.observe()
        control = observed['controls'][0]
        result = self.workspace.act(observed['window_id'], control['id'], observed['snapshot_id'], 'set_value', 'after')
        self.assertTrue(result['action_dispatched'])
        self.assertTrue(result['verification']['goal_verified'])
        self.assertEqual(result['observation']['controls'][0]['value'], 'after')
        with self.assertRaises(ww.WindowsWorkspaceError):
            self.workspace.act(observed['window_id'], control['id'], observed['snapshot_id'], 'set_value', 'again')
        self.assertEqual(len(self.backend.performed), 1)

    def test_dispatch_does_not_claim_unverified_goal(self):
        observed = self.observe()
        result = self.workspace.act(observed['window_id'], observed['controls'][1]['id'], observed['snapshot_id'], 'invoke')
        self.assertFalse(result['verification']['goal_verified'])
        self.assertEqual(result['verification']['status'], 'observed_after_dispatch')

    def test_replaced_control_pid_and_window_security_fail_closed(self):
        for mutate in [lambda: self.backend.items[0].update(runtime_id=(9, 9)),
                       lambda: self.backend.items[0].update(value='changed while awaiting approval'),
                       lambda: self.backend.items[0].update(process_id=999),
                       lambda: self.backend.window.update(pid=999),
                       lambda: self.backend.window.update(elevated=True),
                       lambda: self.backend.window.update(process='powershell.exe')]:
            self.backend = FakeBackend()
            self.workspace.close()
            self.workspace = ww.WindowsWorkspace(backend=self.backend)
            observed = self.observe()
            mutate()
            with self.assertRaises(ww.WindowsWorkspaceError):
                self.workspace.act(observed['window_id'], observed['controls'][0]['id'], observed['snapshot_id'], 'set_value', 'x')
            self.assertEqual(self.backend.performed, [])

    def test_unsupported_operation_password_foreign_refs_and_stale_snapshot(self):
        observed = self.observe()
        for cid, op, val in [(observed['controls'][0]['id'], 'invoke', None),
                             (observed['controls'][2]['id'], 'set_value', 'secret'),
                             ('ctl_forged', 'invoke', None),
                             (observed['controls'][0]['id'], 'send_keys', 'x')]:
            with self.assertRaises(ww.WindowsWorkspaceError):
                self.workspace.act(observed['window_id'], cid, observed['snapshot_id'], op, val)
        self.assertEqual(self.backend.performed, [])
        newer = self.workspace.inspect(observed['window_id'])
        with self.assertRaises(ww.WindowsWorkspaceError):
            self.workspace.act(observed['window_id'], observed['controls'][0]['id'], observed['snapshot_id'], 'set_value', 'x')
        self.assertNotEqual(newer['snapshot_id'], observed['snapshot_id'])

    def test_expiry_redaction_limits_and_no_raw_exception(self):
        self.backend.items[0]['name'] = 'api_key=fixture-secret-value'
        self.backend.items[0]['value'] = 'password=fixture-secret-value'
        self.backend.items.extend([dict(self.backend.items[1], runtime_id=(1, n), name='a' * 500) for n in range(5, 300)])
        observed = self.observe()
        self.assertLessEqual(len(observed['controls']), 180)
        self.assertLessEqual(len(observed['text']), 12000)
        self.assertTrue(observed['truncated'])
        self.assertNotIn('fixture-secret-value', str(observed))
        self.workspace._snapshot_ttl = -1
        with self.assertRaises(ww.WindowsWorkspaceError) as caught:
            self.workspace.act(observed['window_id'], observed['controls'][0]['id'], observed['snapshot_id'], 'set_value', 'x')
        self.assertEqual(caught.exception.code, 'stale')

    def test_sensitive_labeled_value_is_masked_and_cannot_be_written(self):
        self.backend.items[0].update(name='API key', value='unlabeled-sensitive-string')
        observed = self.observe()
        self.assertNotIn('unlabeled-sensitive-string', str(observed))
        self.assertEqual(observed['controls'][0]['patterns'], [])
        with self.assertRaises(ww.WindowsWorkspaceError):
            self.workspace.act(observed['window_id'], observed['controls'][0]['id'], observed['snapshot_id'], 'set_value', 'new')

    def test_native_failure_is_sanitized_and_snapshot_consumed(self):
        observed = self.observe()
        def failing(*args):
            raise RuntimeError('api_key=never-include-this C:/private/user/path')
        self.backend.perform = failing
        with self.assertRaises(ww.WindowsWorkspaceError) as caught:
            self.workspace.act(observed['window_id'], observed['controls'][1]['id'], observed['snapshot_id'], 'invoke')
        self.assertEqual(caught.exception.code, 'uncertain')
        self.assertNotIn('never-include', str(caught.exception))
        with self.assertRaises(ww.WindowsWorkspaceError) as caught:
            self.workspace.act(observed['window_id'], observed['controls'][1]['id'], observed['snapshot_id'], 'invoke')
        self.assertEqual(caught.exception.code, 'stale')

    def test_changed_document_context_blocks_unchanged_save_button(self):
        mutations = [lambda: self.backend.window.update(title='Different document'),
                     lambda: self.backend.items[0].update(value='Another account'),
                     lambda: self.backend.items[0].update(text='Different document body'),
                     lambda: self.backend.items.append({'runtime_id': (9, 9), 'name': 'New document',
                                                       'control_type': 'Document', 'enabled': True, 'patterns': []})]
        for mutate in mutations:
            self.workspace.close()
            self.backend = FakeBackend()
            self.workspace = ww.WindowsWorkspace(backend=self.backend)
            observed = self.observe()
            mutate()
            with self.assertRaises(ww.WindowsWorkspaceError) as caught:
                self.workspace.act(observed['window_id'], observed['controls'][1]['id'], observed['snapshot_id'], 'invoke')
            self.assertEqual(caught.exception.code, 'stale')
            self.assertEqual(self.backend.performed, [])

    def test_run_and_protected_windows_are_omitted(self):
        for title in ('Run', '실행', 'Windows Security', '사용자 계정 컨트롤', 'Account sign in'):
            self.backend.window['title'] = title
            self.assertEqual(self.workspace.windows(), [])

    def test_native_text_pattern_reads_document_without_value_pattern(self):
        calls = []
        class TextRange:
            def GetText(self, count):
                calls.append(count)
                return 'Document contents api_key=fixture-document-secret'
        info = SimpleNamespace(runtime_id=(5, 1), process_id=42, name='Fixture document',
                               control_type='Document', enabled=True,
                               element=SimpleNamespace(CurrentIsPassword=False, CurrentIsKeyboardFocusable=False))
        control = SimpleNamespace(element_info=info, iface_text=SimpleNamespace(DocumentRange=TextRange()))
        backend = ww._PywinautoBackend.__new__(ww._PywinautoBackend)
        described = backend.describe_control(control)
        self.assertEqual(calls, [ww.MAX_TEXT])
        self.backend.items = [described]
        observation = self.observe()
        self.assertIn('Document contents', observation['text'])
        self.assertNotIn('fixture-document-secret', str(observation))

    def test_timeout_cancels_queued_job(self):
        started = threading.Event()
        release = threading.Event()
        original = self.backend.windows
        def blocked():
            started.set()
            release.wait(2)
            return original()
        self.backend.windows = blocked
        self.workspace._timeout = .04
        errors = []
        thread = threading.Thread(target=lambda: self._capture_error(errors, self.workspace.windows))
        thread.start()
        started.wait(1)
        with self.assertRaises(ww.WindowsWorkspaceError):
            self.workspace.windows()
        thread.join(1)
        release.set()
        time.sleep(.08)
        self.assertEqual(len(self.backend.owner), 1)

    @staticmethod
    def _capture_error(errors, operation):
        try:
            operation()
        except ww.WindowsWorkspaceError as error:
            errors.append(error.code)


if __name__ == '__main__':
    unittest.main()
