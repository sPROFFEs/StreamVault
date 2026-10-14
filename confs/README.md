# Local WireGuard configuration

Only `wireguard.conf.example` belongs in source control. Its placeholders are not
usable credentials. Obtain a new profile from your VPN provider and replace all
placeholders locally; never commit real credentials.
Local `confs/*.conf` profiles are ignored and existing files are left on disk.

The retained WireGuard utility defaults to `/saas-data/wireguard`
(`vault/core/network/vpn_rotator.py:15`), outside tracked source. The current
Compose services do not mount `confs/` or start this utility. If you maintain a
separate VPN deployment, keep its real profiles in that deployment's persistent
data directory and retain its existing capabilities and network configuration.
Do not use the example as a working connection profile.

If a private key was ever committed or shared, revoke/replace it. Removing a file
does not invalidate credentials or remove copies from old clones and releases.
