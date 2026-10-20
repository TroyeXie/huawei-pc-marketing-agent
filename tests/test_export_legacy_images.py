"""Historical public image URLs resolve only to exact, verified local bytes."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import app


class VerifiedLegacyImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='zhi-hua-export-')
        self.base = Path(self.temporary.name)
        self.content = b'precisely verified image bytes'
        self.digest = hashlib.sha256(self.content).hexdigest()
        self.filename = self.digest + '.png'
        self.path = self.base / 'data' / 'ima_assets' / self.filename
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(self.content)
        self.model = 'Exact product model'
        self.url = 'https://consumer.huawei.com/exact-image.png'
        self.image = {'model': self.model, 'url': self.url, 'source_url': self.url}
        self.cached = {'model': self.model, 'source_url': self.url,
                       'public_source_url': self.url, 'public_verified': True,
                       'asset_filename': self.filename, 'sha256': self.digest}

    def tearDown(self):
        self.temporary.cleanup()

    def resolve(self, image=None, cached=None):
        index = {self.model: {'images': [self.cached if cached is None else cached]}}
        with patch.object(app, 'BASE', self.base), patch.object(app, 'cache_index', return_value=index):
            return app._export_image_bytes(self.image if image is None else image)

    def test_exact_model_source_and_verified_hash_restore_bytes_without_mutation(self):
        original = copy.deepcopy(self.image)
        with patch('urllib.request.urlopen', side_effect=AssertionError('No network allowed')):
            self.assertEqual(self.resolve(), (self.content, 'image/png', '.png'))
        self.assertEqual(self.image, original)

    def test_wrong_model_unknown_url_and_source_conflict_are_rejected(self):
        for change in ({'model': 'Another product model'},
                       {'url': 'https://consumer.huawei.com/unknown.png',
                        'source_url': 'https://consumer.huawei.com/unknown.png'},
                       {'source_url': 'https://consumer.huawei.com/other-image.png'}):
            with self.subTest(change=change):
                self.assertIsNone(self.resolve({**self.image, **change}))
        self.assertIsNone(self.resolve(cached={**self.cached, 'model': 'Another product model'}))

    def test_missing_verification_conflicting_hash_and_tampered_bytes_are_rejected(self):
        for change in ({'public_verified': False}, {'public_verified': 'true'},
                       {'sha256': '0' * 64}, {'asset_filename': '../' + self.filename}):
            with self.subTest(change=change):
                self.assertIsNone(self.resolve(cached={**self.cached, **change}))
        self.assertIsNone(self.resolve({**self.image, 'sha256': '0' * 64}))
        self.path.write_bytes(b'different bytes')
        self.assertIsNone(self.resolve())

    def test_symlink_and_ambiguous_cache_are_rejected(self):
        self.path.unlink()
        other = self.base / 'outside.png'
        other.write_bytes(self.content)
        self.path.symlink_to(other)
        self.assertIsNone(self.resolve())
        index = {self.model: {'images': [self.cached, copy.deepcopy(self.cached)]}}
        with patch.object(app, 'BASE', self.base), patch.object(app, 'cache_index', return_value=index):
            self.assertIsNone(app._export_image_bytes(self.image))

    def test_all_three_saved_legacy_cases_pack_exact_images_and_keep_originals(self):
        source = app.BASE / 'examples' / 'index.json'
        original_bytes = source.read_bytes()
        entries = json.loads(original_bytes)['examples']
        counts = {'01': 3, '02': 3, '03': 1}
        for entry in entries:
            if entry['id'] not in counts:
                continue
            result = entry.get('publication_result') or entry['result']
            before = copy.deepcopy(result)
            run_id = result['run_id']
            with patch.object(app, '_runs', {run_id: result}), \
                 patch.object(app, 'restore_run', return_value=None), \
                 patch.object(app, 'examples', return_value=None):
                response = app.export_run(run_id, format='zip')
            with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
                images = [name for name in archive.namelist() if name.startswith('images/')]
                self.assertEqual(len(images), counts[entry['id']])
                html = archive.read('图文.html').decode()
                markdown = archive.read('稿件.md').decode()
                for index, image in enumerate(result['images'], 1):
                    content, _, suffix = app._export_image_bytes(image)
                    filename = f'images/image-{index}{suffix}'
                    self.assertEqual(archive.read(filename), content)
                    self.assertIn(f'src="{filename}"', html)
                    self.assertIn(f']({filename})', markdown)
                    self.assertIn(image['source_url'], markdown)
            self.assertEqual(result, before)
        self.assertEqual(source.read_bytes(), original_bytes)


if __name__ == '__main__':
    unittest.main()
