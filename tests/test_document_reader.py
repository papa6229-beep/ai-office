import contextlib
import pathlib
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import document_reader as reader


class DocumentReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).resolve().parents[1])
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def file(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_search_beyond_old_preview_and_pagination(self):
        path = self.file('notes.txt', 'x' * 8000 + '\n납기: 금요일\n' + 'y' * 15000)
        result = reader.read(path, '납기')
        self.assertEqual(result['match_count'], 1)
        self.assertIn('납기: 금요일', result['matches'][0]['text'])
        self.assertTrue(result['search_complete'])
        self.assertIsNotNone(result['next_offset'])
        self.assertLessEqual(len(result['text']), 12000)

    def test_secret_redaction_before_search_and_excerpt(self):
        path = self.file('notes.txt', 'api_key="VERY_PRIVATE"\nBearer abc.def\n-----BEGIN PRIVATE KEY-----\nSECRETDATA\n-----END PRIVATE KEY-----\nhello')
        result = reader.read(path, 'VERY_PRIVATE')
        self.assertNotIn('VERY_PRIVATE', result['text'])
        self.assertNotIn('SECRETDATA', str(result))
        self.assertNotIn('abc.def', str(result))
        self.assertEqual(result['match_count'], 0)

    def test_catalog_sources_duplicates_and_depth(self):
        self.file('one/same.txt', 'a')
        self.file('two/same.txt', 'b')
        self.file('one/two/three/four/deep.txt', 'c')
        self.file('settings.json', 'secret')
        self.file('node_modules/no.txt', 'no')
        self.file('.hidden.txt', 'no')
        with patch('desktop_workspace._desktop_roots', return_value=[]), patch('scanner.FILES', []):
            result = reader.catalog(None, self.root)
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(len({item['id'] for item in result['items']}), 2)

    def test_registered_resources_no_database_creation(self):
        missing = self.root / 'missing.db'
        with patch('desktop_workspace._desktop_roots', return_value=[]), patch('scanner.FILES', []):
            self.assertEqual(reader.catalog(missing)['total'], 0)
        self.assertFalse(missing.exists())
        path = self.file('work/readme.md', 'registered')
        db = self.root / 'existing.db'
        with contextlib.closing(sqlite3.connect(db)) as connection:
            connection.execute('CREATE TABLE work_resources(path TEXT,kind TEXT)')
            connection.execute('INSERT INTO work_resources VALUES (?,?)', (str(path), 'file'))
            connection.commit()
        with patch('desktop_workspace._desktop_roots', return_value=[]), patch('scanner.FILES', []):
            self.assertEqual(reader.catalog(db)['items'][0]['name'], 'readme.md')

    def test_protected_paths_and_reparse(self):
        for name in ['settings.json', 'credentials.txt', 'config.json', 'keys.txt', '.env', 'AppData/private.txt']:
            path = self.file(name, 'secret')
            with self.assertRaises(ValueError):
                reader.read(path)
        path = self.file('ordinary.py', 'print("hello")')
        self.assertIn('print', reader.read(path)['text'])
        with patch('desktop_workspace._safe_route', return_value=False):
            with self.assertRaises(ValueError):
                reader.read(path)

    def test_corrupt_documents_and_unreadable_binary(self):
        for name in ['bad.docx', 'bad.pdf', 'binary.txt']:
            path = self.root / name
            path.write_bytes(b'not document\x00')
            with self.assertRaises(ValueError):
                reader.read(path)

    def test_docx_preserves_paragraph_text(self):
        path = self.root / 'contract.docx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>납기</w:t></w:r><w:r><w:t> 금요일</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(reader.read(path, '납기 금요일')['match_count'], 1)

    def test_truncated_extraction_scope(self):
        path = self.file('large.txt', 'x' * (reader.MAX_TEXT + 50))
        result = reader.read(path, 'absent')
        self.assertFalse(result['search_complete'])
        self.assertTrue(result['truncated'])
        self.assertIn('범위', result['note'])

    def test_scanned_pdf_and_page_bound(self):
        from pypdf import PdfWriter
        path = self.root / 'scanned.pdf'
        writer = PdfWriter()
        for _ in range(62):
            writer.add_blank_page(width=200, height=200)
        with path.open('wb') as output:
            writer.write(output)
        result = reader.read(path, '납기')
        self.assertEqual(result['pages_read'], 60)
        self.assertEqual(result['page_count'], 62)
        self.assertTrue(result['scanned_or_empty'])
        self.assertFalse(result['search_complete'])

    def test_file_and_entry_bounds(self):
        for index in range(6):
            self.file(f'{index}.txt', 'a')
        with patch('desktop_workspace._desktop_roots', return_value=[]), patch('scanner.FILES', []), patch.object(reader, 'MAX_FILES', 3):
            result = reader.catalog(None, self.root)
        self.assertEqual(result['total'], 3)
        self.assertTrue(result['truncated'])

    def test_hwpx_and_xml_entity_rejection(self):
        path = self.root / 'korean.hwpx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('Contents/section0.xml', '<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run><hp:t>검토 금요일</hp:t></hp:run></hp:p></hp:sec>')
        self.assertEqual(reader.read(path, '금요일')['match_count'], 1)
        bad = self.root / 'entity.docx'
        with zipfile.ZipFile(bad, 'w') as archive:
            archive.writestr('word/document.xml', '<!DOCTYPE x [<!ENTITY x "hidden">]><x>&x;</x>')
        with self.assertRaises(ValueError):
            reader.read(bad)

    def test_fresh_size_and_hardlink_checks(self):
        path = self.file('big.txt', 'x')
        with patch.object(reader, 'MAX_BYTES', 0):
            with self.assertRaises(ValueError):
                reader.read(path)
        link = self.root / 'linked.txt'
        try:
            link.hardlink_to(path)
        except OSError:
            self.skipTest('hardlinks unavailable')
        with self.assertRaises(ValueError):
            reader.read(path)

    def test_scanner_only_approved_files_and_traversal(self):
        one = self.file('approved.txt', 'yes')
        self.file('sibling.txt', 'no')
        with patch('desktop_workspace._desktop_roots', return_value=[]), patch('scanner.FILES', [{'path': str(one), 'protected': False}]):
            result = reader.catalog(None)
        self.assertEqual([row['name'] for row in result['items']], ['approved.txt'])
        self.file('folder/placeholder.txt', '')
        with self.assertRaises(ValueError):
            reader.read(self.root / 'folder' / '..' / 'approved.txt')

    def test_unicode_casefold_offsets(self):
        path = self.file('unicode.txt', '앞부분 Straße 끝')
        result = reader.read(path, 'STRASSE')
        self.assertEqual(result['match_count'], 1)
        self.assertEqual(result['matches'][0]['position'], 4)


if __name__ == '__main__':
    unittest.main()
