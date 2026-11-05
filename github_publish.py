#!/usr/bin/env python3
"""Plan, then explicitly publish staged public apps using existing gh authentication."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit
import repo


def gh(*args, payload=None):
    result = subprocess.run(['gh', *args], input=json.dumps(payload) if payload else None,
                            text=True, capture_output=True)
    if result.returncode:
        raise ValueError('GitHub command failed: ' + result.stderr.strip())
    return result.stdout


def prepare(store, repository):
    repo.require(re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', repository), 'Expected OWNER/REPO')
    cfg = repo.read_json(store / 'config.json')
    repo.require(cfg['audience'] == 'public', 'Only public stores may be published')
    source = copy.deepcopy(repo.read_json(store / 'www/source.json'))
    source['sourceURL'] = f'https://raw.githubusercontent.com/{repository}/main/source.json'
    uploads = {}
    for app in source['apps']:
        icon_name = Path(urlsplit(app['iconURL']).path).name
        icon = store / 'www/assets' / icon_name
        repo.require(re.fullmatch(r'[0-9a-f]{64}\.png', icon_name), 'Unexpected icon filename')
        repo.require(hashlib.sha256(icon.read_bytes()).hexdigest() + '.png' == icon_name, 'Icon hash mismatch')
        for version in app['versions']:
            asset_name = Path(urlsplit(version['downloadURL']).path).name
            repo.require(re.fullmatch(r'[0-9a-f]{64}\.ipa', asset_name), 'Unexpected IPA filename')
            asset = store / 'www/assets' / asset_name
            meta = repo.inspect_ipa(asset)
            with asset.open('rb') as f:
                digest = hashlib.file_digest(f, 'sha256').hexdigest()
            repo.require(digest + '.ipa' == asset_name, 'IPA hash mismatch')
            repo.require(meta['bundle'] == app['bundleIdentifier'] and meta['version'] == version['version'], 'IPA/source metadata mismatch')
            repo.require(asset.stat().st_size == version['size'], 'IPA/source size mismatch')
            tag = meta['bundle'] + '-v' + meta['version']
            url = f'https://github.com/{repository}/releases/download/{tag}/'
            version['downloadURL'] = url + asset_name
            # Includes optional integrity hash supported by current SideStore; older schema omits this key.
            version['sha256'] = digest
            uploads.setdefault(tag, {})[asset_name] = asset
            uploads[tag][icon_name] = icon
        repo.require(app['versions'], 'App must contain at least one release')
        latest_tag = app['bundleIdentifier'] + '-v' + app['versions'][0]['version']
        app['iconURL'] = f'https://github.com/{repository}/releases/download/{latest_tag}/{icon_name}'
    return source, uploads


def publish(store, repository, execute=False, reviewed=False):
    source, uploads = prepare(store, repository)
    print(repo.encoded({'repository': repository, 'sourceURL': source['sourceURL'],
                        'releases': {t: list(a) for t, a in uploads.items()}}).decode())
    if not execute:
        return
    repo.require(not uploads or reviewed, 'Review app contents/rights first, then use --reviewed-apps')
    remote = json.loads(gh('api', f'repos/{repository}'))
    repo.require(not remote['private'] and remote['default_branch'] == 'main', 'Target must be public with main as default branch')
    # Capture source revision before assets upload: GitHub SHA check rejects concurrent catalogue edits.
    current = json.loads(gh('api', f'repos/{repository}/contents/source.json?ref=main'))
    old_source = json.loads(base64.b64decode(current['content']))
    planned = {(a['bundleIdentifier'], v['version']): v['downloadURL'] for a in source['apps'] for v in a['versions']}
    for app in old_source.get('apps', []):
        for version in app['versions']:
            repo.require(planned.get((app['bundleIdentifier'], version['version'])) == version['downloadURL'],
                         'Local store would remove/change a published release; use the original complete store')
    for tag, assets in uploads.items():
        releases = json.loads(gh('api', '--paginate', '--slurp', f'repos/{repository}/releases?per_page=100'))
        found = next((r for page in releases for r in page if r['tag_name'] == tag), None)
        if found is None:
            gh('release', 'create', tag, '--repo', repository, '--title', tag,
               '--notes', 'Personal app release. See source.json for metadata.', '--draft')
            found = json.loads(gh('api', f'repos/{repository}/releases/tags/{tag}'))
        for name, asset in assets.items():
            existing = next((a for a in found['assets'] if a['name'] == name), None)
            if existing:
                expected = 'sha256:' + name.split('.')[0]
                repo.require(existing.get('digest') == expected, 'Existing release asset digest differs or is unavailable; refusing overwrite')
                continue
            gh('release', 'upload', tag, str(asset), '--repo', repository)
        if found['draft']:
            gh('release', 'edit', tag, '--repo', repository, '--draft=false')
    gh('api', '--method', 'PUT', f'repos/{repository}/contents/source.json', '--input', '-',
       payload={'message': 'Publish SideStore catalogue', 'branch': 'main', 'sha': current['sha'],
                'content': base64.b64encode(repo.encoded(source)).decode()})
    print('Published catalogue. Pull main before further local code edits.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--store', type=Path, default=Path('stores/public'))
    p.add_argument('--repo', required=True)
    p.add_argument('--execute', action='store_true', help='Upload to public GitHub and update source.json')
    p.add_argument('--reviewed-apps', action='store_true', help='Confirm every staged app is intended for public sharing and has been reviewed for secrets/data')
    a = p.parse_args()
    try:
        publish(a.store, a.repo, a.execute, a.reviewed_apps)
    except (ValueError, OSError) as e:
        p.exit(1, f'Error: {e}\n')
