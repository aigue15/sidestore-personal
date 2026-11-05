"""Synthetic ZIP fixtures only: these are NOT installable iOS apps."""
import base64
import json
from pathlib import Path
import plistlib
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile
import repo
import github_publish
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = self.root / 'store'
        repo.init(self.store, 'Test', 'test.source.public', 'http://127.0.0.1:8197', 'public')
        self.icon = self.root / 'icon.png'
        self.icon.write_bytes(PNG)
    def ipa(self, version='1.0', extra=None, info=None, binary=False):
        path = self.root / (version + '.ipa')
        data = dict(CFBundleIdentifier='test.example.app', CFBundleShortVersionString=version,
                    CFBundleVersion='42', CFBundleDisplayName='Synthetic test only', CFBundleExecutable='Test',
                    MinimumOSVersion='16.0', NSCameraUsageDescription='Synthetic camera description')
        data.update(info or {})
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('Payload/Test.app/Info.plist', plistlib.dumps(data, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML))
            z.writestr('Payload/Test.app/Test', b'SYNTHETIC TEST FIXTURE - NOT INSTALLABLE')
            if extra: z.writestr(*extra)
        return path
    def add(self, path):
        return repo.publish(self.store, path, self.icon, 'Test', 'Test app', '2026-11-05', share_bundle='test.example.app')
    def source(self):
        return repo.read_json(self.store / 'www/source.json')
    def test_xml_and_binary_metadata(self):
        for binary in (False, True):
            m = repo.inspect_ipa(self.ipa(binary=binary))
            self.assertEqual(m['build'], '42')
            self.assertEqual(m['minimum'], '16.0')
            self.assertIn('NSCameraUsageDescription', m['privacy']['Payload/Test.app/Info.plist'])
    def test_numeric_order_and_history(self):
        self.add(self.ipa('1.9'))
        old = self.source()['apps'][0]['versions'][0]
        self.add(self.ipa('1.10'))
        vs = self.source()['apps'][0]['versions']
        self.assertEqual([v['version'] for v in vs], ['1.10', '1.9'])
        self.assertEqual(vs[1], old)
    def test_idempotent(self):
        p = self.ipa(); self.add(p)
        before = self.source(); self.add(p)
        self.assertEqual(before, self.source())
    def test_same_version_changed_bytes(self):
        self.add(self.ipa()); before = self.source()
        with self.assertRaisesRegex(ValueError, 'different bytes'): self.add(self.ipa(extra=('extra', b'changed')))
        self.assertEqual(before, self.source())
    def test_explicit_public_app_selection(self):
        with self.assertRaisesRegex(ValueError, 'share-bundle'):
            repo.publish(self.store, self.ipa(), self.icon, 'T', 'D', '2026-11-05')
        self.assertEqual(self.source()['apps'], [])
    def test_traversal_paths(self):
        for n in ('../outside', '/absolute', 'Payload/../../outside', 'C:\\file'):
            with self.subTest(n=n), self.assertRaisesRegex(ValueError, 'Unsafe'):
                repo.inspect_ipa(self.ipa(extra=(n, b'bad')))
    def test_symlinks(self):
        e = zipfile.ZipInfo('Payload/Test.app/link'); e.create_system = 3
        e.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaisesRegex(ValueError, 'links'): repo.inspect_ipa(self.ipa(extra=(e, b'/etc/passwd')))
    def test_duplicate_and_multiple_apps(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            repo.inspect_ipa(self.ipa(extra=('payload/test.app/TEST', b'collision')))
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            repo.inspect_ipa(self.ipa(extra=('Payload/Other.app/Info.plist', plistlib.dumps({}))))
    def test_invalid_metadata(self):
        for d in ({'CFBundleIdentifier': '../bad'}, {'CFBundleShortVersionString': 'beta'}, {'MinimumOSVersion': 'wrong'}, {'CFBundleExecutable': '../escape'}):
            with self.subTest(d=d), self.assertRaises(ValueError): repo.inspect_ipa(self.ipa(info=d))
    def test_limits_and_bad_zip(self):
        p = self.ipa()
        for field in ('MAX_IPA', 'MAX_EXPANDED', 'MAX_PLIST'):
            with patch.object(repo, field, 1), self.assertRaises(ValueError): repo.inspect_ipa(p)
        p.write_bytes(b'not zip')
        with self.assertRaises(zipfile.BadZipFile): self.add(p)
    def test_url_safety(self):
        for u in ('https://u:p@example.org', 'https://example.org?token=abc', 'https://example.org#abc', 'http://example.org'):
            with self.assertRaises(ValueError): repo.check_base(u, 'public')
    def test_atomic_failure(self):
        before = self.source(); original = repo.atomic
        def fail(path, data):
            if path.name == 'source.json': raise OSError('simulated failure')
            return original(path, data)
        with patch.object(repo, 'atomic', side_effect=fail), self.assertRaises(OSError): self.add(self.ipa())
        self.assertEqual(before, self.source())
        self.add(self.ipa()); self.assertEqual(len(self.source()['apps']), 1)
    def test_http_loopback_and_no_private_files(self):
        self.add(self.ipa())
        with repo.server(self.store, 0) as s:
            t = threading.Thread(target=s.serve_forever, daemon=True); t.start()
            base = 'http://127.0.0.1:' + str(s.server_port)
            try:
                with urllib.request.urlopen(base + '/source.json') as r: self.assertEqual(json.load(r), self.source())
                for p in ('/', '/config.json', '/../config.json', '/audit/test.json'):
                    with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(base + p)
                    caught.exception.close()
                name = self.source()['apps'][0]['versions'][0]['downloadURL'].split('/')[-1]
                with urllib.request.urlopen(base + '/assets/' + name) as r: self.assertEqual(r.read(), self.ipa().read_bytes())
            finally: s.shutdown(); t.join()
    def test_github_plan_without_network(self):
        self.add(self.ipa('1.9')); self.add(self.ipa('1.10'))
        with patch.object(github_publish, 'gh', side_effect=AssertionError('network')):
            source, uploads = github_publish.prepare(self.store, 'example/apps')
        vs = source['apps'][0]['versions']
        self.assertIn('/test.example.app-v1.10/', vs[0]['downloadURL'])
        self.assertIn('/test.example.app-v1.9/', vs[1]['downloadURL'])
        self.assertEqual(len(uploads), 2)
        self.assertEqual(len(vs[0]['sha256']), 64)
        self.assertNotIn('marketplaceID', source['apps'][0])
    def test_tampering(self):
        self.add(self.ipa()); asset = next((self.store / 'www/assets').glob('*.ipa'))
        asset.write_bytes(self.ipa(extra=('tamper', b'changed')).read_bytes())
        with self.assertRaisesRegex(ValueError, 'hash mismatch'): github_publish.prepare(self.store, 'example/apps')
    def test_publisher_remote_commit_order(self):
        self.add(self.ipa())
        calls = []
        def fake(*args, payload=None):
            calls.append((args, payload))
            if args == ('api', 'repos/example/apps'): return json.dumps({'private': False, 'default_branch': 'main'})
            if 'contents/source.json?ref=main' in args[-1]:
                return json.dumps({'sha': 'previous', 'content': base64.b64encode(b'{"apps":[]}').decode()})
            if '--slurp' in args: return '[[]]'
            if '/releases/tags/' in args[-1]: return '{"draft":true,"assets":[]}'
            return '{}'
        with patch.object(github_publish, 'gh', side_effect=fake):
            github_publish.publish(self.store, 'example/apps', True, True)
        self.assertEqual(calls[-1][1]['sha'], 'previous')
        self.assertEqual(calls[-1][0][1:3], ('--method', 'PUT'))
        self.assertTrue(any('upload' in a for a, _ in calls[:-1]))

    def test_publisher_rejects_stale_local_store(self):
        old = {'apps': [{'bundleIdentifier': 'old.app', 'versions': [{'version': '1', 'downloadURL': 'https://example.org/old.ipa'}]}]}
        def fake(*args, payload=None):
            if args == ('api', 'repos/example/apps'): return json.dumps({'private': False, 'default_branch': 'main'})
            return json.dumps({'sha': 'old', 'content': base64.b64encode(json.dumps(old).encode()).decode()})
        with patch.object(github_publish, 'gh', side_effect=fake), self.assertRaisesRegex(ValueError, 'remove/change'):
            github_publish.publish(self.store, 'example/apps', True, True)

    def test_catalogue_shape(self):
        source = repo.read_json(Path(__file__).resolve().parents[1] / 'source.json')
        self.assertIsInstance(source['apps'], list)
        self.assertTrue(source['sourceURL'].endswith('/main/source.json'))

if __name__ == '__main__': unittest.main()
