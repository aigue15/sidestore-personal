#!/usr/bin/env python3
"""Local-only SideStore source builder. Python 3.11+, standard library only."""
import argparse
import datetime as dt
import fcntl
import functools
import hashlib
import http.server
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import stat
import tempfile
from urllib.parse import quote, urlsplit
import zipfile

MAX_IPA = 2 * 1024**3
MAX_EXPANDED = 6 * 1024**3
MAX_PLIST = 2 * 1024**2
ID = re.compile(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\Z')
VERSION = re.compile(r'\d+(?:\.\d+){0,3}\Z')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path):
    return json.loads(path.read_text())


def check_base(base, audience):
    u = urlsplit(base)
    require(not u.username and not u.password and not u.query and not u.fragment,
            'Credentials, query tokens and fragments are forbidden in base URLs')
    require(u.hostname and not any(c.isspace() for c in base), 'Invalid URL')
    loopback = u.hostname in ('127.0.0.1', 'localhost', '::1')
    require(u.scheme == 'https' or (u.scheme == 'http' and loopback),
            'HTTPS required except for loopback previews')
    require('..' not in u.path.split('/'), 'Unsafe URL path')
    return base.rstrip('/')


def inspect_ipa(path):
    require(path.is_file() and not path.is_symlink(), 'IPA must be a regular file, not a symlink')
    require(0 < path.stat().st_size <= MAX_IPA, 'IPA exceeds 2 GiB limit or is empty')
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        require(len(entries) <= 30000, 'Too many archive entries')
        seen = set()
        total = 0
        for item in entries:
            name = item.filename
            p = PurePosixPath(name)
            require(not p.is_absolute() and '..' not in p.parts and '\\' not in name
                    and '\x00' not in name and ':' not in name, 'Unsafe archive path')
            key = str(p).casefold()
            require(key not in seen, 'Duplicate/case-colliding archive path')
            seen.add(key)
            kind = stat.S_IFMT(item.external_attr >> 16)
            require(kind in (0, stat.S_IFREG, stat.S_IFDIR), 'Archive links/special files forbidden')
            require(not item.flag_bits & 1, 'Encrypted ZIP entries unsupported')
            total += item.file_size
            require(total <= MAX_EXPANDED, 'Expanded archive exceeds 6 GiB')
            require(item.file_size <= max(1024**2, item.compress_size * 300), 'Suspicious compression ratio')
        main = [i for i in entries if re.fullmatch(r'Payload/[^/]+\.app/Info.plist', i.filename)]
        require(len(main) == 1, 'Expected exactly one top-level Payload/*.app/Info.plist')
        plists = {}
        for item in entries:
            if item.filename.endswith('/Info.plist'):
                require(item.file_size <= MAX_PLIST, 'Info.plist exceeds 2 MiB')
                value = plistlib.loads(z.read(item))
                require(isinstance(value, dict), 'Info.plist must be a dictionary')
                plists[item.filename] = value
        info = plists[main[0].filename]
        for key in ('CFBundleIdentifier', 'CFBundleShortVersionString', 'CFBundleVersion'):
            require(isinstance(info.get(key), str) and info[key], 'Missing/invalid ' + key)
        require(ID.fullmatch(info['CFBundleIdentifier']), 'Invalid bundle identifier')
        require(VERSION.fullmatch(info['CFBundleShortVersionString']), 'Use a numeric marketing version (e.g. 1.2.3)')
        minimum = info.get('MinimumOSVersion')
        require(minimum is None or (isinstance(minimum, str) and VERSION.fullmatch(minimum)), 'Invalid minimum iOS version')
        executable = info.get('CFBundleExecutable')
        require(isinstance(executable, str) and executable and '/' not in executable and executable not in ('.', '..'), 'Missing/invalid executable name')
        exe = main[0].filename.rsplit('/', 1)[0] + '/' + executable
        require(exe in z.namelist() and z.getinfo(exe).file_size > 0, 'Missing app executable')
        # Stream every entry to validate CRC and decompression; never extract or execute any app code.
        for item in entries:
            with z.open(item) as f:
                while f.read(1024**2):
                    pass
        privacy = {name: {k: v for k, v in value.items() if 'UsageDescription' in k}
                   for name, value in plists.items()}
        return {'bundle': info['CFBundleIdentifier'], 'version': info['CFBundleShortVersionString'],
                'build': info['CFBundleVersion'], 'name': info.get('CFBundleDisplayName') or info.get('CFBundleName') or info['CFBundleIdentifier'],
                'minimum': minimum, 'privacy': privacy}


def init(root, name, identifier, base, audience):
    require(ID.fullmatch(identifier), 'Invalid source identifier')
    require(not root.exists(), 'Choose a new empty store directory')
    base = check_base(base, audience)
    root.mkdir(parents=True)
    atomic(root / 'config.json', encoded({'name': name, 'identifier': identifier, 'base': base, 'audience': audience}))
    atomic(root / 'www/source.json', encoded({'name': name, 'identifier': identifier, 'sourceURL': base + '/source.json', 'apps': [], 'news': []}))


def version_key(value):
    return tuple((list(map(int, value.split('.'))) + [0] * 4)[:4])


def publish(root, ipa, icon, developer, description, date, notes='', share_bundle=None):
    cfg = read_json(root / 'config.json')
    check_base(cfg['base'], cfg['audience'])
    require(dt.date.fromisoformat(date).isoformat() == date, 'Date must be YYYY-MM-DD')
    require(developer.strip() and description.strip(), 'Developer and description are required')
    require(icon.is_file() and not icon.is_symlink() and icon.stat().st_size <= 5*1024**2, 'Use a PNG icon at most 5 MiB')
    icon_data = icon.read_bytes()
    require(icon_data.startswith(b'\x89PNG\r\n\x1a\n'), 'Icon must be a PNG')
    # Snapshot first to prevent an input changing between validation and publication.
    with tempfile.TemporaryDirectory(dir=root) as temp:
        snapshot = Path(temp) / 'input.ipa'
        require(ipa.is_file() and not ipa.is_symlink() and ipa.stat().st_size <= MAX_IPA, 'Invalid/oversized IPA')
        with ipa.open('rb') as src, snapshot.open('wb') as dst:
            remaining = MAX_IPA + 1
            while remaining:
                chunk = src.read(min(1024**2, remaining))
                if not chunk:
                    break
                dst.write(chunk)
                remaining -= len(chunk)
            require(snapshot.stat().st_size <= MAX_IPA, 'IPA exceeds 2 GiB')
        meta = inspect_ipa(snapshot)
        if cfg['audience'] == 'public':
            require(share_bundle == meta['bundle'], 'Public staging requires --share-bundle with this exact bundle identifier')
        with snapshot.open('rb') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
        with (root / '.publish.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            source = read_json(root / 'www/source.json')
            app = next((a for a in source['apps'] if a['bundleIdentifier'] == meta['bundle']), None)
            existing = next((v for v in app['versions'] if v['version'] == meta['version']), None) if app else None
            asset = digest + '.ipa'
            if existing:
                require(existing['downloadURL'].endswith('/' + asset),
                        'Version already exists with different bytes; increment marketing version')
                return meta  # Deliberate immutable/idempotent release; metadata is unchanged.
            icon_name = hashlib.sha256(icon_data).hexdigest() + '.png'
            assets = root / 'www/assets'
            assets.mkdir(exist_ok=True)
            target = assets / asset
            if not target.exists():
                os.replace(snapshot, target)
            atomic(assets / icon_name, icon_data)
            release = {'version': meta['version'], 'date': date, 'size': target.stat().st_size,
                       'downloadURL': cfg['base'] + '/assets/' + asset, 'localizedDescription': notes}
            if meta['minimum']:
                release['minOSVersion'] = meta['minimum']
            if app is None:
                app = {'bundleIdentifier': meta['bundle'], 'versions': []}
                source['apps'].append(app)
            app.update(name=meta['name'], developerName=developer, localizedDescription=description,
                       iconURL=cfg['base'] + '/assets/' + icon_name)
            app['versions'].append(release)
            app['versions'].sort(key=lambda v: (version_key(v['version']), v['version']), reverse=True)
            source['apps'].sort(key=lambda a: a['bundleIdentifier'])
            # Local audit only. Build and permission report intentionally stay out of the download tree.
            atomic(root / 'audit' / (digest + '.json'), encoded(meta | {'sha256': digest}))
            atomic(root / 'www/source.json', encoded(source))  # Commit point, after all immutable assets.
        return meta


class Preview(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(404)
    def log_message(self, *args):
        pass
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()
    def send_head(self):
        route = urlsplit(self.path).path
        if not (route == '/source.json' or re.fullmatch(r'/assets/[0-9a-f]{64}\.(ipa|png)', route)):
            self.send_error(404)
            return None
        target = Path(self.translate_path(route))
        if target.is_symlink() or not target.is_file():
            self.send_error(404)
            return None
        return super().send_head()


def server(root, port):
    return http.server.ThreadingHTTPServer(('127.0.0.1', port), functools.partial(Preview, directory=str(root / 'www')))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--store', type=Path, default=Path('stores/public'))
    sub = p.add_subparsers(dest='command', required=True)
    i = sub.add_parser('init')
    i.add_argument('--name', default="Ege's Apps")
    i.add_argument('--identifier', default='personal.ege.sidestore.public')
    i.add_argument('--base-url', default='http://127.0.0.1:8197')
    i.add_argument('--audience', choices=['public'], default='public')
    a = sub.add_parser('add')
    a.add_argument('ipa', type=Path)
    a.add_argument('--icon', required=True, type=Path)
    a.add_argument('--developer', required=True)
    a.add_argument('--description', required=True)
    a.add_argument('--date', required=True)
    a.add_argument('--notes', default='')
    a.add_argument('--share-bundle')
    s = sub.add_parser('serve')
    s.add_argument('--port', type=int, default=8197)
    sub.add_parser('link')
    args = p.parse_args()
    try:
        if args.command == 'init':
            init(args.store, args.name, args.identifier, args.base_url, args.audience)
        elif args.command == 'add':
            m = publish(args.store, args.ipa, args.icon, args.developer, args.description, args.date, args.notes, args.share_bundle)
            print(f"Staged locally: {m['bundle']} {m['version']} ({m['build']})")
        elif args.command == 'link':
            print('sidestore://source?url=' + quote(read_json(args.store / 'config.json')['base'] + '/source.json', safe=''))
        else:
            with server(args.store, args.port) as httpd:
                print(f'Local preview only: http://127.0.0.1:{args.port}/source.json', flush=True)
                httpd.serve_forever()
    except (ValueError, OSError, zipfile.BadZipFile, plistlib.InvalidFileException) as e:
        p.exit(1, f'Error: {e}\n')


if __name__ == '__main__':
    main()
