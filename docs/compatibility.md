# Compatibility evidence

Reviewed 2026-11-05. Upstream develop can change; this records what was inspected, not an on-device certification.

- [SideStore source guide](https://docs.sidestore.io/docs/advanced/app-sources): AltSource compatibility and warning against marketplaceID/notarized Build fields.
- [SideStore URL scheme](https://docs.sidestore.io/docs/advanced/url-schema): `sidestore://source?url=...`; encode the URL parameter.
- [AltStore source specification](https://faq.altstore.io/developers/make-a-source): app metadata, direct IPA download URLs, version array ordering and permission metadata.
- [SideStore published schema](https://github.com/SideStore/sidestore-source-types/blob/main/schema.json): required source/app/version properties. It is older than current decoder extensions: `sha256` and optional `buildVersion` appear in the app decoder but not this schema. Local generator emits the common schema subset; GitHub publisher additionally emits `sha256`.
- [FetchSourceOperation](https://github.com/SideStore/SideStore/blob/develop/SideStore/Core/Operations/StandaloneOperations/FetchSourceOperation.swift): plain URLRequest and URLSession.shared fetch the JSON.
- [DownloadAppOperation](https://github.com/SideStore/SideStore/blob/develop/SideStore/Core/Operations/StandaloneOperations/DownloadAppOperation.swift): default URLSession fetches app URLs. Public GitHub Releases satisfy direct download semantics; no custom GitHub Authorization header is configured by this source.
- [AppVersion decoder](https://github.com/SideStore/SideStore/blob/develop/AltStore/Core/Model/AppVersion.swift): required version/date/URL/size and optional lowercased SHA-256, buildVersion, min/max OS. This implementation deliberately requires marketing-version bumps rather than supporting build-only updates.
- [Source decoder](https://github.com/SideStore/SideStore/blob/develop/AltStore/Core/Model/Source.swift): empty apps/news arrays are acceptable. A stable source URL matters for identity.
- [GitHub release asset API](https://docs.github.com/en/rest/releases/assets#get-a-release-asset): public asset access requires no authentication; private assets need authenticated access. Upload uses the existing gh account separately from public client downloads.
- [SideStore FAQ](https://docs.sidestore.io/docs/faq): free-account limits and refresh requirements.
- [Apple supported capabilities](https://developer.apple.com/help/account/reference/supported-capabilities-ios): capability/account distinctions. Repository availability does not resolve signing entitlements.

Current source and download code provide no basis to promise that Safari login cookies, Basic Auth prompts or a private GitHub URL work in SideStore. URL tokens are bearer secrets and would need a separately designed revocable download gateway; none is implemented. Public-only hosting avoids that authentication dependency.

Manual phone acceptance still required after the first real intended IPA is supplied: add source, inspect app identity/icon/version, install, launch, upload a new marketing version, refresh the source, install update and confirm app data continuity. Check minimum iOS filtering and refresh with the actual Apple Account. Test fixtures are not suitable for any of these device checks.
