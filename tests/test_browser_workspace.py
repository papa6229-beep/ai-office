import base64
import pathlib
import threading
import unittest
from unittest.mock import Mock, patch

import browser_workspace as bw


class BrowserWorkspaceTests(unittest.TestCase):
    def test_url_policy_and_public_url_scrubbing(self):
        for url in ['file:///secret', 'https://user:pass@example.org', 'http://169.254.169.254/latest',
                    'http://127.0.0.1:8765/api/work', 'http://localhost:11434/api/chat',
                    'http://2130706433:8765', 'http://127.1:8765', 'http://metadata.google.internal']:
            with self.subTest(url=url), self.assertRaises(bw.BrowserWorkspaceError):
                bw.validate_url(url)
        self.assertEqual(bw.validate_url('http://localhost:8767/page'), 'http://localhost:8767/page')
        self.assertEqual(bw.public_url('https://example.org/a?token=secret#private'), 'https://example.org/a')

    def test_queue_owns_all_operations_and_closes(self):
        class Workspace(bw.BrowserWorkspace):
            def _execute(self, method, *args):
                return threading.get_ident(), method, args
            def _cleanup(self):
                self.cleaned = threading.get_ident()
        workspace = Workspace()
        owner, method, args = workspace.open('https://example.org')
        self.assertNotEqual(owner, threading.get_ident())
        self.assertEqual(workspace.tabs()[0], owner)
        self.assertEqual((method, args), ('open', ('https://example.org',)))
        workspace.close()
        self.assertEqual(workspace.cleaned, owner)
        with self.assertRaises(bw.BrowserWorkspaceError):
            workspace.tabs()

    def test_query_finds_content_beyond_preview_and_bounds_output(self):
        data = bw.read_excerpt('prefix ' * 2000 + 'TARGET evidence\n' + 'tail ' * 10000, 'target', 0)
        self.assertEqual(data['match_count'], 1)
        self.assertIn('TARGET evidence', data['text'])
        self.assertLessEqual(len(data['text']), 12000)
        self.assertTrue(data['truncated'])

    def test_unicode_search_uses_original_offsets(self):
        text = 'ß' * 1000 + 'TARGET'
        result = bw.read_excerpt(text, 'target')
        self.assertEqual(result['match_count'], 1)
        self.assertIn('TARGET', result['text'])
        self.assertEqual(result['offset'], 400)
        result = bw.read_excerpt(text + ' TARGET', 'target', 1006)
        self.assertEqual(result['offset'], 407)

    def test_read_issues_only_safe_observed_links_and_stale_references_fail(self):
        workspace = bw.BrowserWorkspace()
        try:
            page = Mock()
            page.url = 'http://localhost:8767/start?token=secret'
            page.is_closed.return_value = False
            page.title.return_value = 'Test'
            page.frames = [object(), object()]
            page.evaluate.return_value = {'text': 'visible text', 'links': [
                {'text': 'Next', 'url': 'http://localhost:8767/next?private=1'},
                {'text': 'blocked', 'url': 'javascript:alert(1)'}], 'node_limit': False}
            workspace._pages['tab'] = page
            result = workspace._dispatch('read', 'tab', '', 0)
            self.assertEqual(result['url'], 'http://localhost:8767/start')
            self.assertEqual(len(result['links']), 1)
            self.assertNotIn('private', result['links'][0]['url'])
            self.assertEqual(result['unread_iframes'], 1)
            page.url = 'http://localhost:8767/changed'
            with self.assertRaises(bw.BrowserWorkspaceError):
                workspace.follow('tab', result['links'][0]['id'], result['snapshot_id'])
            page.goto.assert_not_called()
        finally:
            workspace.close()

    def test_screenshot_masks_inputs_and_is_bounded(self):
        workspace = bw.BrowserWorkspace()
        try:
            page = Mock()
            page.url = 'https://example.org'
            page.title.return_value = 'Test'
            page.is_closed.return_value = False
            page.screenshot.return_value = b'png'
            page.frames = [object(), object(), object()]
            workspace._pages['tab'] = page
            result = workspace.view('tab')
            self.assertEqual(base64.b64decode(result['image']), b'png')
            self.assertFalse(page.screenshot.call_args.kwargs['full_page'])
            self.assertIn('input', page.locator.call_args.args[0])
            self.assertIn('iframe', page.locator.call_args.args[0])
            self.assertIn('frame', page.locator.call_args.args[0].split(','))
            self.assertTrue(result['masked_iframes'])
            self.assertEqual(result['unread_iframes'], 2)
        finally:
            workspace.close()

    def test_unknown_tab_direction_and_query_are_rejected(self):
        workspace = bw.BrowserWorkspace()
        try:
            with self.assertRaises(bw.BrowserWorkspaceError):
                workspace.read('arbitrary')
            with self.assertRaises(bw.BrowserWorkspaceError):
                workspace.scroll('arbitrary', 'evaluate(secret)')
            with self.assertRaises(bw.BrowserWorkspaceError):
                bw.read_excerpt('text', 'a' * 501)
            with self.assertRaises(bw.BrowserWorkspaceError):
                bw.read_excerpt('text', '', -1)
        finally:
            workspace.close()

    def test_route_blocks_control_endpoints_without_leaking_error(self):
        workspace = bw.BrowserWorkspace()
        try:
            route = Mock()
            route.request.url = 'http://localhost:8765/api/work?token=secret'
            workspace._route(route)
            route.abort.assert_called_once()
            route.continue_.assert_not_called()
        finally:
            workspace.close()

    def test_navigation_error_is_generic_and_new_tab_is_closed(self):
        workspace = bw.BrowserWorkspace()
        try:
            page = Mock()
            page.goto.side_effect = RuntimeError('private-token-and-url')
            context = Mock()
            context.pages = []
            context.new_page.return_value = page
            workspace._context = context
            with self.assertRaises(bw.BrowserWorkspaceError) as caught:
                workspace.open('https://example.org/?secret=1')
            self.assertNotIn('secret', str(caught.exception))
            self.assertNotIn('private', str(caught.exception))
            page.close.assert_called_once()
        finally:
            workspace.close()

    def test_tabs_adopt_only_dedicated_context_pages(self):
        workspace = bw.BrowserWorkspace()
        try:
            page = Mock()
            page.url = 'https://example.org/site?key=secret'
            page.title.return_value = 'Manual tab'
            page.is_closed.return_value = False
            context = Mock()
            context.pages = [page]
            workspace._context = context
            result = workspace.tabs()
            self.assertEqual(len(result), 1)
            self.assertRegex(result[0]['tab_id'], '^[a-f0-9]{32}$')
            self.assertEqual(result[0]['url'], 'https://example.org/site')
            self.assertEqual(workspace.tabs()[0]['tab_id'], result[0]['tab_id'])
        finally:
            workspace.close()

    def test_secret_text_is_redacted_before_excerpt_title_and_link_output(self):
        text = 'api_key="private123"\n<password>hidden</password>\nBearer bearer-secret\n-----BEGIN PRIVATE KEY-----\nkey-secret\n-----END PRIVATE KEY-----'
        result = bw.read_excerpt(text)
        for secret in ['private123', 'hidden', 'bearer-secret', 'key-secret']:
            self.assertNotIn(secret, result['text'])
        self.assertIn('[가림]', result['text'])

    def test_default_user_chrome_profile_is_rejected(self):
        with patch.dict('os.environ', {'LOCALAPPDATA': str(pathlib.Path.cwd() / 'fixture-local')}):
            profile = pathlib.Path.cwd() / 'fixture-local' / 'Google' / 'Chrome' / 'User Data' / 'Default'
            with self.assertRaises(bw.BrowserWorkspaceError):
                bw._profile_path(profile)

    def test_manual_browser_close_forgets_live_handles(self):
        workspace = bw.BrowserWorkspace()
        try:
            workspace._pages['old'] = Mock()
            workspace._snapshots['old'] = {'private': 'snapshot'}
            workspace._on_context_closed()
            self.assertEqual(workspace._pages, {})
            self.assertEqual(workspace._snapshots, {})
            self.assertIsNone(workspace._context)
        finally:
            workspace.close()

    def test_external_idle_close_is_recovered_before_first_navigation(self):
        workspace = bw.BrowserWorkspace()
        try:
            stale = Mock()
            stale.pages = []
            fresh = Mock()
            fresh.pages = []
            page = Mock()
            page.url = 'https://example.org/next'
            page.is_closed.return_value = False
            page.title.return_value = 'Next site'
            fresh.new_page.return_value = page
            def delayed_close():
                # Sync Playwright delivers the external close callback only
                # when the next channel call pumps events, not while Queue.get
                # waits for the next user request.
                workspace._on_context_closed(stale)
                raise RuntimeError('closed before navigation')
            stale.new_page.side_effect = delayed_close
            workspace._context = stale
            def ensure():
                if workspace._context is None:
                    workspace._context = fresh
            with patch.object(workspace, '_ensure', side_effect=ensure):
                result = workspace.open('https://example.org/next')
            self.assertEqual(result['url'], 'https://example.org/next')
            stale.new_page.assert_called_once()
            fresh.new_page.assert_called_once()
            page.goto.assert_called_once_with('https://example.org/next', wait_until='domcontentloaded')
        finally:
            workspace.close()

    def test_old_context_close_callback_does_not_forget_replacement(self):
        workspace = bw.BrowserWorkspace()
        try:
            old, replacement = Mock(), Mock()
            workspace._context = replacement
            workspace._pages['live'] = Mock()
            workspace._on_context_closed(old)
            self.assertIs(workspace._context, replacement)
            self.assertIn('live', workspace._pages)
        finally:
            workspace.close()

    def test_two_consecutive_opens_do_not_recreate_live_context(self):
        workspace = bw.BrowserWorkspace()
        try:
            context = Mock()
            context.pages = []
            pages = []
            for url in ('https://example.org/one', 'https://example.org/two'):
                page = Mock()
                page.url = url
                page.title.return_value = url
                page.is_closed.return_value = False
                pages.append(page)
            context.new_page.side_effect = pages
            workspace._context = context
            workspace.open(pages[0].url)
            workspace.open(pages[1].url)
            self.assertIs(workspace._context, context)
            self.assertEqual(context.new_page.call_count, 2)
            for page in pages:
                page.goto.assert_called_once()
        finally:
            workspace.close()

    def test_failure_after_navigation_dispatch_is_never_replayed(self):
        workspace = bw.BrowserWorkspace()
        try:
            context = Mock()
            context.pages = []
            page = Mock()
            page.is_closed.return_value = False
            def fail_after_dispatch(*args, **kwargs):
                workspace._on_context_closed(context)
                raise RuntimeError('closed after navigation was dispatched')
            page.goto.side_effect = fail_after_dispatch
            context.new_page.return_value = page
            workspace._context = context
            with self.assertRaises(bw.BrowserWorkspaceError):
                workspace.open('https://example.org/effect')
            page.goto.assert_called_once()
            context.new_page.assert_called_once()
        finally:
            workspace.close()

    def test_unknown_new_page_error_is_not_retried(self):
        workspace = bw.BrowserWorkspace()
        try:
            context = Mock()
            context.pages = []
            context.new_page.side_effect = RuntimeError('private failure before navigation')
            workspace._context = context
            with patch.object(workspace, '_ensure') as ensure, self.assertRaises(bw.BrowserWorkspaceError) as caught:
                workspace.open('https://example.org')
            self.assertNotIn('private', str(caught.exception))
            self.assertIs(workspace._context, context)
            ensure.assert_called_once()
            context.new_page.assert_called_once()
        finally:
            workspace.close()

    def test_repeated_pre_navigation_close_is_bounded(self):
        workspace = bw.BrowserWorkspace()
        contexts = []
        try:
            def ensure():
                context = Mock()
                context.pages = []
                def close_before_navigation():
                    workspace._on_context_closed(context)
                    raise RuntimeError('closed')
                context.new_page.side_effect = close_before_navigation
                contexts.append(context)
                workspace._context = context
            with patch.object(workspace, '_ensure', side_effect=ensure), self.assertRaises(bw.BrowserWorkspaceError):
                workspace.open('https://example.org')
            self.assertEqual(len(contexts), 2)
            self.assertTrue(all(context.new_page.call_count == 1 for context in contexts))
        finally:
            workspace.close()

    def test_excessive_redirect_chain_is_blocked(self):
        workspace = bw.BrowserWorkspace()
        try:
            route = Mock()
            route.request.url = 'https://example.org'
            prior = Mock()
            prior.url = 'https://example.org'
            prior.redirected_from = prior
            route.request.redirected_from = prior
            workspace._route(route)
            route.abort.assert_called_once()
            route.continue_.assert_not_called()
        finally:
            workspace.close()


if __name__ == '__main__':
    unittest.main()
