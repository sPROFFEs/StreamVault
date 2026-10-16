# Private federation and community profiles

Federation is an opt-in StreamVault-to-StreamVault protocol (v1), inspired by
Aoede's publisher/consumer separation. It does **not** make an Aoede music node a
video node, implement ActivityPub, or expose an unauthenticated public catalog.
Each node still controls its own files and accounts. Both nodes must run a version
with this protocol; no access is activated during installation/update.

## Connect two nodes

To let node B's users watch node A's downloaded media:

1. On A, open **Administration → Federation**, choose a node name, enable
   publication and save it.
2. On A, issue a credential named for B with **Share completed movies and
   episodes** enabled. Profile sharing is a separate permission, off by default.
3. Copy the credential immediately and send it privately to B's administrator.
   A stores only its SHA-256 digest; the clear token is returned once.
4. On B, add A's canonical HTTPS URL and the supplied credential under **Nodes we
   consume**, then **Synchronize**. For a private RFC1918/ULA LAN node, explicitly
   enable the LAN option. HTTP is permitted only for these private addresses;
   prefer HTTPS even on the LAN. Loopback, Docker service names, link-local/cloud
   metadata addresses and redirecting nodes are not accepted.
5. On B, open the matching movie/episode. A source labeled **Node · A** appears
   alongside local and external providers. Choose it and play normally.
6. In **Downloads → Available library**, completed local and synchronized remote
   files appear together. Use **Origin** to show this server, all federated nodes,
   or a specific node. Each file identifies its server; remote entries use the
   same authenticated playback/audio relay and never offer local delete/preview
   actions. The storage/quota bar measures local disk only. Listing uses the local
   metadata cache, so opening Downloads does not contact every node.
7. Use **Save to device** for an explicit offline copy of a local/node movie or
   episode. This uses the authenticated relay, never exposes a node credential,
   and does not consume the local server's download quota. See
   [device downloads, limits and privacy](OFFLINE.md).

Repeat in the opposite direction for reciprocal sharing. Disable a consumed peer
to immediately remove its sources from selection. Revoke an issued credential to
stop that node's subsequent catalog/playback requests. Revocation does not undo
bytes already delivered or force-close an already-authorized stream. Administrators
must share only content they have authority to share.

## Media, matching and performance

The publisher scans completed, non-empty video downloads using the existing
incomplete-download markers. IDs are opaque HMACs; catalog responses contain
movie/series/season/episode metadata, not absolute/relative server paths. Optional
indexed metadata contributes TMDB IDs. Consumers match those IDs first, otherwise
exact normalized localized/original title and year; episodes require an exact season and episode number.
This avoids guessing a different remake/episode. Files with unrelated titles or
missing/incorrect episode metadata cannot be automatically matched to a TMDB page.

Catalogs are paginated at 100 items, at most 10,000 items per peer, with bounded
2 MiB JSON pages and a two-minute synchronization deadline. A full valid sync
replaces imported metadata atomically and removes deleted remote entries. A failed
sync preserves old metadata but hides its sources while the node is offline.
Background work starts after HTTP readiness, checks health roughly every minute
and synchronizes roughly every 15 minutes, one peer at a time. Requests have bounded
timeouts; DNS is validated and pinned per request. TLS SNI and Host retain the
configured node name. Credentials are never forwarded through redirects.

Playback remains same-origin for the browser. The serving node sends its encrypted
stored peer credential only to the remote publisher. Proxying preserves byte
Range/206/416 responses and closes upstream connections on cancellation; buffers
are 64 KiB and concurrent relays are capped at eight. Media is not downloaded or
imported as a local copy. Browser-incompatible formats use the remote node's
existing bounded remux engine, audio selector and timeline offsets. Included and
sidecar text subtitles use authenticated same-origin proxies too.

Streaming uses both nodes' network bandwidth. It does not bypass Cloudflare or
increase host uplink capacity. Heavy media conversion happens on the publisher,
not again on the consumer. Public endpoints must route to the app under
`/api/federation/v1/`; these routes require a scoped bearer token rather than a
user cookie. Regular consumer/proxy APIs require an active user session.

Outbound credentials are encrypted using `SECRET_KEY`. Keep this key stable:
after rotating it, re-enter the outbound peer credentials. Inbound grant hashes
are not encrypted with that key, but media IDs change, so synchronize peers again.
Configuration and federation/profile tables are included in maintenance backups.

## Community and privacy

Open **Community** from your avatar. New profiles are private. Each user chooses:

- **Only me**: no directory or remote publication.
- **Users of this server**: visible only to signed-in local users.
- **This server and authorized nodes**: also available to peers granted profile
  access by the administrator.

Sharing aggregate metrics is a separate checkbox. Metrics count completed movies,
completed episodes, started history entries and favorites; they do not disclose
the titles/history or pretend that playback positions measure actual watch time.
Remote profile directories are read on demand, not permanently mirrored; disabled
nodes or revoked profile access stop subsequent remote reads. Pagination handles
local directories; the current remote directory view returns up to 100 profiles.

## Verification

The regression suite covers token scopes/revocation, private profiles, encrypted
credentials, LAN/SSRF/redirect/DNS pinning, completed episode selection, atomic sync
and deletion/offline behavior. ASGI protocol tests execute publication → catalog
sync → resolver → playback descriptor → proxy byte Range without a user cookie on
the publisher. Browser tests cover admin tabs and remote-source audio/offset wiring
at mobile, desktop and TV sizes. A real two-server/Cloudflare deployment and media
decoding with real network bandwidth still need a deployment smoke test.
