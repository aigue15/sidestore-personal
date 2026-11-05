# Ege's SideStore Apps

A public catalogue for deliberately shared iOS apps. Add this URL in **SideStore → Sources → +**:

**https://raw.githubusercontent.com/aigue15/sidestore-personal/main/source.json**

[Add to SideStore](sidestore://source?url=https%3A%2F%2Fraw.githubusercontent.com%2Faigue15%2Fsidestore-personal%2Fmain%2Fsource.json)

The catalogue starts empty. No installable app has been uploaded. Test fixtures are synthetic and are never published as apps.

## Publish an app

Requires Python 3.11 or newer, `gh`, and an existing GitHub login with write access to this repository. The tool never creates credentials. Run from this checkout. All commands below stage locally until the final `--execute` command.

```sh
python3 repo.py init
python3 repo.py add /path/to/MyApp.ipa \
  --icon /path/to/icon.png \
  --developer Ege \
  --description 'What this app does.' \
  --date 2026-11-05 \
  --notes 'First public version.' \
  --share-bundle com.example.myapp
python3 github_publish.py --repo aigue15/sidestore-personal
```

Use the actual bundle identifier from your IPA, not the example. Review the plan, app bundle, embedded resources, logs, configuration and entitlements for secrets, personal records and redistribution rights. The ZIP validator is not a secret scanner or malware scanner. Only select your intended public release builds.

```sh
python3 github_publish.py --repo aigue15/sidestore-personal --execute --reviewed-apps
```

This uploads selected IPA/icon files using authenticated `gh`, publishes version-specific GitHub Releases, then commits the updated catalogue through GitHub's Contents API. SideStore downloads public files without credentials. Authentication protects publishing, not downloading. Refresh Sources in SideStore to see the update. Run `git pull --ff-only` after publishing to bring the remote catalogue commit into your checkout.

Keep `stores/public/` on your publishing machine between releases; it holds your release history and local audit data. It is gitignored. A different machine needs that complete store transferred privately before publishing; the tool refuses to remove remotely published versions. The source and Release assets remain publicly downloadable if the Mac is offline. No Mac server is required.

For a new release, increment `CFBundleShortVersionString`, export a real device IPA, and repeat `add` and publish. Build-only updates are deliberately rejected when a marketing version already exists with different bytes. Numeric versions (up to four components) sort newest first; `1.10` precedes `1.9`. Release dates use `YYYY-MM-DD`. Existing releases and assets are never overwritten. Do not edit/delete release tags or assets manually: historical download URLs depend on them.

## Preview and test

```sh
python3 -m unittest discover -s tests -v
python3 repo.py serve
```

Preview binds **127.0.0.1:8197 only** and serves only catalogue and asset routes. Stop with Ctrl-C. Its localhost links are for Mac preview, not installation from an iPhone. The GitHub publisher rewrites those links to public release URLs. No background service is installed. If this port is occupied, use `--port` and initialize a new preview store with the matching `--base-url`.

The parser supports XML and binary Info.plist, extracts bundle identifier, name, marketing/build versions, minimum iOS and privacy descriptions, and validates ZIP CRCs without extracting/executing files. It rejects traversal, links, duplicate/case-colliding paths, encryption, excessive expansion and ambiguous app roots. Limits: 2 GiB compressed, 6 GiB expanded, 30,000 entries, 2 MiB per Info.plist. Supply a normal PNG icon explicitly; compiled asset catalogs/CgBI icons are not converted. Content-addressed filenames and SHA-256 checks prevent accidental asset substitution. A lock serializes local adds, assets are written before the atomic catalogue replacement, and GitHub's expected file SHA protects remote catalogue commits from concurrent changes.

A failed remote publish can leave completed/draft releases without a catalogue entry. Re-run the same command from the same store: matching asset digests are accepted and never overwritten. If a concurrent catalogue update won, pull/reconcile the complete local store before retrying. Old unused local assets are retained; there is no automatic deletion or rollback command.

## What compatibility means

This is a SideStore/AltSource feed, not an app signer. The catalogue uses direct HTTPS JSON, image and IPA URLs. It avoids marketplaceID and notarization-only Build fields. Version-specific release URLs preserve historical versions; current SideStore also understands the publisher's `sha256` field. The add-source deep link URL-encodes the catalogue URL.

The repository tooling validates archive structure and metadata; it cannot prove an IPA is installable, unencrypted, correctly signed, or compatible with your account/device. Export an iOS device build (not a Simulator bundle), and test installation and a subsequent update on your phone. No real iPhone installation has been tested for this project.

Normal free Apple Account limits still apply: seven-day signing validity, three active apps including SideStore, and ten App IDs per week; extensions can consume additional IDs. Refresh through SideStore before expiry. Entitlements/capabilities depend on Apple's provisioning and your account, and some are unavailable to free accounts or require additional approval. Hosting cannot grant HealthKit, push, app groups or any other capability. We do not strip entitlements or claim every app will work. The local audit records Info.plist privacy declarations, not a complete code-signature entitlement audit. This source targets SideStore; it does not claim the additional permission metadata required by every AltStore Classic release.

Private app delivery is out of scope. Never add a personal/private IPA to this store. A private GitHub repo is not a drop-in authenticated SideStore host, and browser login/Basic Auth/secret query URLs have not been verified as secure compatible alternatives.

See [compatibility evidence](docs/compatibility.md). CI runs the standard-library tests on each push and pull request, with read-only repository permissions. There is no automatic upload trigger.
