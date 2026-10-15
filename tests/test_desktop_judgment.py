import json
import hashlib
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import desktop_judgment as judge
import desktop_workspace as workspace


class JudgmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.desktop = self.root / 'Desktop'
        self.desktop.mkdir()
        for target, key, value in [(judge, 'STATE', self.root / 'ai.json')]:
            p = patch.object(target, key, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(workspace, '_desktop_roots', return_value=[self.desktop])
        p.start(); self.addCleanup(p.stop)
        judge._reset_runtime()
        self.addCleanup(judge.stop)
        self.calls = []
        self.settings = {'gemma_url': 'http://127.0.0.1:11434', 'gemma_model': 'gemma4:26b', 'api_key': 'private-test-key'}
        judge.configure(lambda: dict(self.settings), self.model, self.vision)

    def add(self, name, value='hello'):
        path = self.desktop / name
        path.write_text(value, encoding='utf-8')
        return path

    def model(self, prompt, settings=None):
        self.calls.append(prompt)
        if '"groups"' in prompt:
            return '{"groups":[]}'
        return json.dumps({'summary': '행사 업무 메모', 'purpose': '행사 준비', 'project': '행사', 'visual_role': 'unknown', 'disposition': 'keep', 'confidence': .9, 'reason': '본문의 일정과 준비 목록'})

    def vision(self, prompt, payload, settings=None):
        self.calls.append(prompt)
        return json.dumps({'summary': '행사 홍보 시안', 'purpose': '홍보', 'project': '행사', 'visual_role': 'meaningful_design', 'disposition': 'review_delete', 'confidence': .95, 'reason': '시각 분석 의견'})

    def run_scan(self, force=False):
        judge.start(force)
        deadline = time.monotonic() + 5
        while judge.status()['running'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(judge.status()['running'])
        return judge.status()

    def test_empty_facts_cache_retention_and_edits(self):
        path = self.add('memo.txt', ' \n\t')
        result = self.run_scan()
        self.assertEqual(result['empty_count'], 1)
        item = result['candidates'][0]
        self.assertEqual(item['evidence_scope'], 'full')
        self.assertEqual(self.calls, [])
        self.assertEqual(len(judge.inspect_candidates([item['id']])), 1)
        judge.retain([item['id']])
        self.assertEqual(judge.status()['candidates'], [])
        self.run_scan()
        self.assertEqual(judge.status()['items'][0]['status'], 'retained')
        path.write_text('meaningful changed content', encoding='utf-8')
        self.assertEqual(judge.inspect_candidates([item['id']]), [])
        result = self.run_scan()
        self.assertEqual(result['items'][0]['status'], 'analyzed')
        self.assertGreater(len(self.calls), 0)
        before = len(self.calls)
        self.run_scan()
        self.assertEqual(len(self.calls), before)

    def test_secret_outside_preview_is_not_sent_or_stored(self):
        self.add('memo.txt', 'ordinary\n' * 1000 + 'api_key=sk-very-secret-secret-value')
        result = self.run_scan()
        self.assertEqual(result['items'][0]['status'], 'protected')
        self.assertEqual(self.calls, [])
        saved = judge.STATE.read_text(encoding='utf-8')
        self.assertNotIn('private-test-key', saved)
        self.assertNotIn('sk-very-secret', saved)

    def test_usage_only_explicit_open_and_no_paths_in_public_state(self):
        path = self.add('memo.txt')
        result = self.run_scan()
        item = result['items'][0]
        self.assertIsNone(item['usage'])
        judge.record_open(item['id'])
        self.assertEqual(judge.status()['items'][0]['usage']['opens'], 1)
        self.assertNotIn(str(path), json.dumps(judge.status()))

    def test_image_model_suggestion_not_factual_empty(self):
        path = self.desktop / 'image.png'
        from PIL import Image
        Image.new('RGB', (20, 20), 'red').save(path)
        result = self.run_scan()
        self.assertEqual(result['image_count'], 1)
        item = result['candidates'][0]
        self.assertIn('model_suggestion', item['flags'])
        self.assertNotIn('empty_verified', item['flags'])

    def test_stopped_response_not_committed(self):
        self.add('memo.txt')
        entered, release = threading.Event(), threading.Event()
        def slow(prompt, settings=None):
            entered.set(); release.wait(3)
            return self.model(prompt, settings)
        judge.configure(lambda: self.settings, slow, self.vision)
        judge.start()
        self.assertTrue(entered.wait(2))
        judge.stop(); release.set()
        time.sleep(.05)
        self.assertEqual(judge.status()['items'], [])

    def test_corrupt_state_preserved(self):
        judge.STATE.write_text('{broken', encoding='utf-8')
        result = judge.start()
        self.assertTrue(result['last_error'])
        self.assertEqual(judge.STATE.read_text(encoding='utf-8'), '{broken')

    def test_groups_reject_unknown_ids_and_noncontent_evidence(self):
        self.add('memo.txt'); self.add('other.txt', 'other event brief')
        def grouping(prompt, settings=None):
            if '"groups"' not in prompt:
                return self.model(prompt, settings)
            ids = [i['id'] for i in json.loads(prompt.split('\n', 1)[1])]
            return json.dumps({'groups': [
                {'label': 'bad', 'item_ids': ids + ['0'*64], 'confidence': .99, 'evidence': 'shared contents'},
                {'label': '행사 준비', 'item_ids': ids, 'confidence': .9, 'evidence': '본문 일정과 행사 준비 내용이 일치', 'basis': 'content'}]})
        judge.configure(lambda: self.settings, grouping, self.vision)
        result = self.run_scan()
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual(result['groups'][0]['label'], '행사 준비')

    def test_file_changed_during_model_is_deferred(self):
        path = self.add('memo.txt')
        def mutate(prompt, settings=None):
            path.write_text('changed', encoding='utf-8')
            return self.model(prompt, settings)
        judge.configure(lambda: self.settings, mutate, self.vision)
        result = self.run_scan()
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['groups'], [])

    def test_retention_reset_restores_candidate_and_stable_revision(self):
        self.add('memo.txt', '')
        first = self.run_scan()
        item_id = first['candidates'][0]['id']
        revision = first['revision']
        self.assertEqual(len(revision), 32)
        self.run_scan()
        self.assertEqual(judge.status()['revision'], revision)
        judge.record_open(item_id)
        self.assertEqual(judge.status()['revision'], revision)
        judge.retain([item_id]); self.run_scan()
        self.assertNotEqual(judge.status()['revision'], revision)
        judge.reset_retention([item_id])
        self.assertEqual(len(judge.status()['candidates']), 1)
        self.assertEqual(judge.status()['revision'], revision)

    def test_configuration_snapshot_and_unknown_state_preservation(self):
        self.add('memo.txt', 'event'); self.add('other.txt', 'event description')
        data = judge._blank(); data['future_feature'] = {'keep': True}; judge._save(data)
        seen = []
        def model(prompt, settings=None):
            seen.append(settings['gemma_url'])
            self.settings['gemma_url'] = 'http://another-server:11434'
            return self.model(prompt, settings)
        judge.configure(lambda: dict(self.settings), model, self.vision)
        self.run_scan()
        self.assertEqual(set(seen), {'http://127.0.0.1:11434'})
        saved = json.loads(judge.STATE.read_text(encoding='utf-8'))
        self.assertEqual(saved['future_feature'], {'keep': True})
        self.assertNotIn('private-test-key', judge.STATE.read_text(encoding='utf-8'))

    def test_empty_results_published_before_slow_model(self):
        self.add('a.txt', 'actual content'); self.add('z.txt', '')
        entered, release = threading.Event(), threading.Event()
        def slow(prompt, settings=None):
            entered.set(); release.wait(3)
            return self.model(prompt, settings)
        judge.configure(lambda: self.settings, slow, self.vision)
        judge.start()
        self.assertTrue(entered.wait(2))
        self.assertEqual(judge.status()['empty_count'], 1)
        self.assertEqual(len(judge.status()['candidates']), 1)
        judge.stop(); release.set()

    def test_public_desktop_and_unsupported_ai_are_never_sent(self):
        public = self.root / 'Public'; public.mkdir()
        (public / 'public.txt').write_text('secret shared text')
        self.add('asset.ai', 'illustrator source')
        self.add('animated.gif', 'not a supported image')
        with patch.object(workspace, '_desktop_roots', return_value=[self.desktop, public]):
            result = self.run_scan()
        self.assertEqual(result['deferred_count'], 2)
        self.assertEqual(self.calls, [])

    def test_low_confidence_and_nonfinite_model_answers_not_candidates(self):
        self.add('memo.txt', 'actual content')
        for confidence in [.2, float('nan'), float('inf')]:
            def model(prompt, settings=None):
                return {'summary': 'note', 'disposition': 'review_delete', 'confidence': confidence, 'reason': 'uncertain'}
            judge.configure(lambda: self.settings, model, self.vision)
            result = self.run_scan(force=True)
            self.assertEqual(result['candidates'], [])
            self.assertEqual(result['items'][0]['disposition'], 'unknown')

    def test_changed_and_removed_candidates_disappear_without_new_scan(self):
        path = self.add('memo.txt', '')
        result = self.run_scan(); item_id = result['candidates'][0]['id']
        path.write_text('now useful', encoding='utf-8')
        self.assertEqual(judge.status()['candidates'], [])
        self.assertEqual(judge.inspect_candidates([item_id]), [])
        path.unlink()
        self.assertEqual(judge.status()['items'], [])

    def test_symlink_not_followed(self):
        self.add('memo.txt', 'actual content')
        with patch.object(workspace, '_safe_route', return_value=False):
            result = self.run_scan()
        self.assertEqual(self.calls, [])
        self.assertEqual(result['candidates'], [])

    def test_stop_preference_persists_on_configure(self):
        self.run_scan(); judge.stop(); judge._reset_runtime()
        judge.configure(lambda: self.settings, self.model, self.vision)
        self.assertFalse(judge.status()['enabled'])

    def test_transient_model_failure_retries_after_delay(self):
        self.add('memo.txt', 'event')
        count = []
        def broken(prompt, settings=None):
            count.append(1)
            raise TimeoutError('private server detail')
        judge.configure(lambda: self.settings, broken, self.vision)
        result = self.run_scan()
        self.assertEqual(result['deferred_count'], 1)
        self.assertIn('retry_after', result['items'][0])
        self.run_scan()
        self.assertEqual(len(count), 1)
        data = judge._read()
        for record in data['records'].values():
            record['retry_after'] = '2000-01-01T00:00:00+00:00'
        judge._save(data)
        judge.configure(lambda: self.settings, self.model, self.vision)
        result = self.run_scan()
        self.assertEqual(result['analyzed'], 1)
        self.assertNotIn('private server detail', judge.STATE.read_text(encoding='utf-8'))

    def test_sensitive_model_response_is_erased_and_protected(self):
        self.add('memo.txt', 'ordinary')
        for response in [
                {'summary': 'password=very-private', 'reason': 'credential', 'confidence': .9},
                {'summary': 'private personal data', 'sensitive': True, 'confidence': .9}]:
            judge.configure(lambda: self.settings, lambda prompt, settings=None: response, self.vision)
            result = self.run_scan(force=True)
            self.assertEqual(result['items'][0]['status'], 'protected')
            self.assertEqual(result['items'][0]['summary'], '')
            self.assertEqual(result['candidates'], [])
            self.assertNotIn('very-private', judge.STATE.read_text(encoding='utf-8'))
            self.assertNotIn('private personal data', judge.STATE.read_text(encoding='utf-8'))

    def test_preexisting_temporary_file_is_preserved_and_refuses_start(self):
        temp = judge.STATE.with_suffix('.json.tmp')
        temp.write_text('unrelated preserved content', encoding='utf-8')
        result = judge.start()
        self.assertFalse(result['running'])
        self.assertTrue(result['last_error'])
        self.assertEqual(temp.read_text(encoding='utf-8'), 'unrelated preserved content')

    def test_app_usage_is_in_model_context_without_raw_path(self):
        self.add('memo.txt', 'ordinary')
        item_id = workspace.inventory()['items'][0]['id']
        judge.record_open(item_id)
        self.run_scan()
        prompt = next(p for p in self.calls if 'observed_app_usage' in p)
        self.assertIn('"opens": 1', prompt)
        self.assertNotIn(str(self.desktop), prompt)

    def test_empty_or_malformed_model_reply_does_not_claim_image_understood(self):
        path = self.desktop / 'image.png'
        from PIL import Image
        Image.new('RGB', (20, 20), 'red').save(path)
        for reply in ['{}', 'not JSON', '{"summary":"", "reason":""}']:
            judge.configure(lambda: self.settings, self.model,
                            lambda prompt, payload, settings=None: reply)
            result = self.run_scan(force=True)
            self.assertEqual(result['image_count'], 0)
            self.assertEqual(result['deferred_count'], 1)
            self.assertEqual(result['candidates'], [])
            self.assertEqual(result['groups'], [])

    def group_records(self, count):
        return {hashlib.sha256(str(i).encode()).hexdigest(): {
            'id': hashlib.sha256(str(i).encode()).hexdigest(), 'status': 'analyzed',
            'summary': f'작업 {i} 행사 준비 내용', 'purpose': '홍보', 'project': '행사',
            'visual_role': 'meaningful_design', 'confidence': .9} for i in range(count)}

    def test_group_short_ids_map_back_and_full_or_bogus_ids_are_rejected(self):
        records = self.group_records(2)
        real_ids = list(records)
        seen = []
        def model(prompt, settings=None):
            seen.append(prompt)
            return {'groups': [
                {'label': 'bad full IDs', 'item_ids': real_ids, 'basis': 'content', 'confidence': .9, 'evidence': 'same brief'},
                {'label': 'bad missing ID', 'item_ids': ['f1', 'f999'], 'basis': 'content', 'confidence': .9, 'evidence': 'same brief'},
                {'label': '행사', 'item_ids': ['f1', 'f2'], 'basis': 'content', 'confidence': .9, 'evidence': '동일 행사 일정과 브랜드 시안'}]}
        judge.configure(lambda: self.settings, model, self.vision)
        result = judge._groups(records, self.settings)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]['item_ids']), set(real_ids))
        self.assertTrue(all(item_id not in seen[0] for item_id in real_ids))

    def test_grouping_visits_later_records_with_bounded_prompt(self):
        records = self.group_records(145)
        for row in records.values():
            row['summary'] += '구체적인 이미지 작업 내용' * 100
            row['purpose'] = '홍보 목적 ' * 100
            row['project'] = '프로젝트 ' * 100
        observed, lengths = set(), []
        def model(prompt, settings=None):
            lengths.append(len(prompt.encode('utf-8')))
            compact = json.loads(prompt.split('\n', 1)[1])
            observed.update(int(row['summary'].split()[1]) for row in compact)
            return {'groups': []}
        judge.configure(lambda: self.settings, model, self.vision)
        self.assertEqual(judge._groups(records, self.settings), [])
        self.assertEqual(observed, set(range(145)))
        self.assertLessEqual(max(lengths), judge.GROUP_PROMPT_BYTES)
        self.assertGreater(len(lengths), 1)

    def test_explicit_content_anchor_extends_group_across_batches(self):
        records = self.group_records(judge.GROUP_BATCH + 1)
        original = list(records)
        calls = []
        def model(prompt, settings=None):
            compact = json.loads(prompt.split('\n', 1)[1])
            calls.append(compact)
            if len(calls) == 1:
                ids = ['f1', 'f2']
            else:
                anchor = next(row for row in compact if 'known_group' in row)
                ids = ['f1', anchor['id']]
            return {'groups': [{'label': '행사 시안', 'item_ids': ids, 'basis': 'content', 'confidence': .95,
                                'evidence': '동일한 행사명과 날짜가 관찰됨'}]}
        judge.configure(lambda: self.settings, model, self.vision)
        result = judge._groups(records, self.settings)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]['item_ids']), {original[0], original[1], original[-1]})

    def test_group_version_change_regroups_cached_items_without_content_resend(self):
        self.add('memo.txt', 'event'); self.add('other.txt', 'event brief')
        self.run_scan()
        data = judge._read(); data['grouping_version'] = 0; judge._save(data)
        before = len(self.calls)
        self.run_scan()
        new = self.calls[before:]
        self.assertEqual(len(new), 1)
        self.assertIn('"groups"', new[0])
        self.assertEqual(judge._read()['grouping_version'], judge.GROUPING_VERSION)

    def test_group_settings_bound_json_output_and_progress_is_incremental(self):
        records = self.group_records(judge.GROUP_BATCH + 2)
        progress, seen = [], []
        def model(prompt, settings=None):
            seen.append(settings)
            return {'groups': []}
        judge.configure(lambda: self.settings, model, self.vision)
        judge._groups(records, self.settings, progress=lambda done, total, groups: progress.append((done, total)))
        self.assertEqual(progress[0], (0, len(records)))
        self.assertIn((judge.GROUP_BATCH, len(records)), progress)
        self.assertEqual(progress[-1], (len(records), len(records)))
        self.assertTrue(all(s['_judgment_json'] and s['_judgment_max_tokens'] == 1024 and s['_judgment_think'] is False for s in seen))
        self.assertNotIn('_judgment_json', self.settings)

    def test_group_failure_backoff_avoids_repeat_until_due_without_content_resend(self):
        self.add('memo.txt', 'event'); self.add('other.txt', 'event brief')
        self.run_scan()
        data = judge._read(); data['grouping_version'] = 0; judge._save(data)
        count = []
        def unavailable(prompt, settings=None):
            count.append(1)
            raise TimeoutError('private response')
        judge.configure(lambda: self.settings, unavailable, self.vision)
        result = self.run_scan()
        self.assertTrue(result['last_error'])
        failed = judge._read()
        self.assertEqual(failed['grouping_retry_attempts'], 1)
        self.run_scan()
        self.assertEqual(len(count), 1)
        self.assertEqual(judge.status()['phase'], 'waiting')
        failed['grouping_retry_after'] = '2000-01-01T00:00:00+00:00'; judge._save(failed)
        before = len(self.calls)
        judge.configure(lambda: self.settings, self.model, self.vision)
        self.run_scan()
        self.assertEqual(len(self.calls) - before, 1)
        self.assertNotIn('grouping_retry_after', judge._read())

    def test_group_cancellation_prevents_additional_model_requests(self):
        records = self.group_records(judge.GROUP_BATCH * 3)
        calls = []
        active = [True]
        def model(prompt, settings=None):
            calls.append(1); active[0] = False
            return {'groups': []}
        judge.configure(lambda: self.settings, model, self.vision)
        with self.assertRaises(judge._Cancelled):
            judge._groups(records, self.settings, is_active=lambda: active[0])
        self.assertEqual(len(calls), 1)

    def test_partial_content_group_is_saved_before_later_batch_failure(self):
        for number in range(judge.GROUP_BATCH + 1):
            self.add(f'memo{number}.txt', 'event description')
        self.run_scan()
        data = judge._read(); data['grouping_version'] = 0; judge._save(data)
        calls = []
        def model(prompt, settings=None):
            calls.append(1)
            if len(calls) > 1:
                raise TimeoutError()
            return {'groups': [{'label': '행사', 'item_ids': ['f1', 'f2'], 'basis': 'content',
                                'confidence': .9, 'evidence': '동일 행사 일정과 브랜드'}]}
        judge.configure(lambda: self.settings, model, self.vision)
        result = self.run_scan()
        self.assertTrue(result['last_error'])
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual(result['grouping_processed'], judge.GROUP_BATCH)
        self.assertEqual(result['grouping_total'], judge.GROUP_BATCH + 1)


if __name__ == '__main__':
    unittest.main()
