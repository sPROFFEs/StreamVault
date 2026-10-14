# StreamVault

A self-hosted media library with a shared desktop, mobile and TV web interface and
an Android WebView client. The server uses Python 3.11, FastAPI, Jinja templates,
vanilla JavaScript/CSS and SQLite. It serves pages directly; no Node frontend build
or static-site hosting is required.

- [Install, configure and use StreamVault](docs/SETUP.md)
- [Android builds, installation and device checks](android/README.md)
- [Release contract, signing and internal updates](docs/RELEASES.md)
- [Deployment services and operational details](vault/README.md)
- [Regression and browser checks](vault/core/tests/README.md)
- [Editable visual assets](vault/assets/brand/README.md)

On Debian/Ubuntu, clone your chosen repository's maintained distribution branch,
then run from its root:

```sh
sudo bash vault/setup.sh --non-interactive --domain localhost
```

The installer prepares Docker/Compose, persistent data, a session secret and the
first administrator. Open `http://localhost` and save the generated administrator
password. Configure a trusted HTTPS origin for remote devices and the release APK.
TMDB, tunnel and subtitle credentials are optional integrations; builds and fixture
tests do not require them.

GitHub Actions validates/builds the hosted server and debug/unsigned APKs. Matching
version tags can prepare signed assets and a draft release after the maintainer
configures the release branch and signing environment. The internal administrator
updater follows the checkout's configured Git branch and rebuilds the hosted
deployment. APKs are installed separately.

External media sources, channel availability and device codecs vary. Verify actual
playback on your deployment and devices before relying on a provider.
