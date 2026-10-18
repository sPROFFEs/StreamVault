# Device downloads and federated playback

## Save a movie or episode on this device

1. Open **Downloads → Available library** and use **Save to device** beside a
   completed local or federated video. Alternatively, select a local/node source
   in the movie/episode page and use **Save to device** there. If already playing,
   its selected audio track is preselected. The confirmation dialog lets you
   choose which audio language to save. Additional languages require separately
   saved copies and consume additional video storage; they are not downloaded
   silently.
2. Confirm the device download. Keep that tab/app screen open until completion;
   this is separate from downloading Torrentio content onto the server.
3. Open **On this device · Offline** from Downloads or the mobile More menu.
   This public, device-only screen also opens when normal library navigation
   cannot reach the server. Android's connection-error screen includes the same
   shortcut in updated APKs.
4. The offline library uses a poster catalog with search/movie/series filters,
   cinematic details, and the same custom player as online. Series are grouped
   with season/episode selectors. Completed copies support seeking, play/pause,
   volume, fullscreen, playback speed, subtitle language/off, subtitle size and
   timing offset without a server connection. Position and track preferences are
   saved on this device, not synchronized into another account's server history.
   Previously saved audio variants are selectable in the player. Deleting device
   copies never deletes server files; deleting a grouped title confirms the
   number of copies/episodes being removed.

New downloads save bounded poster/backdrop images and public metadata alongside
the video. Existing copies remain playable without a migration or redownload.
While connected and signed in, **Refresh details online** can retrieve missing
artwork/synopses for those older copies without downloading the video again.

Bookmark **`https://YOUR-SERVER/assets/offline.html`** to open device downloads
directly, including after closing and reopening the browser without Internet or
an active server session. Visit it once while connected after updating the server
and finish a **Save to device** download before testing airplane mode. Normal
`/downloads` and `/portal` navigation also falls back to this screen when the
network is unavailable. Server-only downloads are not available offline until
you save a separate copy to the device.

Only completed local/node files are supported, not embedded/external streaming
pages. Files use the normal authenticated playback preparation, including a
compatible conversion if required. The selected audio and up to ten included VTT
subtitle tracks (at most 1 MiB each) are saved. When saving from the movie/episode
page, an active provider subtitle is also copied through the server's authenticated
subtitle proxy, within that ten-track limit. Selecting it and saving an existing
copy adds the subtitle without downloading the video again. Provider links and
credentials are not stored. Other online provider results are not downloaded
automatically. Offline can select only audio/subtitles already saved; it cannot
fetch new languages or convert incompatible codecs without a server.

## Space and reliability

- The default device budget is **5 GiB**, adjustable from **0.25 to 100 GiB** in
  the offline library. The browser's available quota can be smaller.
- Video data is streamed to IndexedDB in 1 MiB blocks and served with HTTP byte
  ranges. A multi-gigabyte movie is not accumulated in JavaScript memory.
- Poster/backdrop copies are limited to 1 MiB each, fetched without credentials
  from public app assets or TMDB's image host, and included in the device budget.
  Missing artwork/subtitles never invalidate a completed video download.
- One device download runs at a time. Where supported, a Web Lock coordinates
  different tabs. Failed/cancelled transfers remove their partial blocks. If the
  browser/app is forcibly closed, an incomplete entry can be deleted from the
  device library; it is never offered as playable.
- Downloads do **not** continue after closing the page/app and do not resume a
  partial transfer. Retrying starts again. Streaming conversion may take longer
  and uses the existing server encoder/concurrency limits.
- HTTPS and service workers are required (localhost is supported for testing).
  Private browsing, disabled site storage or an outdated WebView may prevent use.
  Browser storage persistence is requested, not guaranteed. The browser/OS may
  reclaim data when storage is low. This is a viewing convenience, not a backup.

## Privacy

Copies belong to this browser/device profile and server origin, not a server
session. They intentionally remain playable after sign-out or session expiry.
Anyone using that device profile can view them. Delete copies on shared devices.
Clearing site data or uninstalling the app can remove them; switching servers in
the Android wrapper clears web storage rather than transferring the library.

No login HTML, admin pages, API responses, session cookies or federation bearer
credentials are cached in the application shell. Peer connection URLs/tokens and local
server paths are not stored in downloaded-media records. The shell caches only
public assets; videos are saved only after an explicit confirmation. Revoking a
node credential cannot retract bytes that were already downloaded to a device.

## Faster federation startup

Modern publisher/consumer nodes combine metadata and default playback preparation
so first playback at zero avoids a second browser/node round trip. Older peers
continue using the previous descriptor request; seeking and alternate audio still
prepare the appropriate stream. Relays forward available fragments immediately
instead of collecting 64 KiB before forwarding, and disable intermediary buffering.

Live conversions target a two-second starting reserve with a four-second maximum
wait after metadata, rather than five/eight seconds. This reduces intentional
startup delay, but cannot compensate for a connection slower than the video's
bitrate or a server unable to encode in real time. Prefer an already converted
copy, a lower-bitrate release, or an explicit device download on slow connections.
