# Changelog

## [Unreleased]

## [0.1.0] - 2026-11-26

### Added
- Retrieve a WireGuard peer's endpoint IP address using `wireguard-tools`,
  without retrieving private or preshared keys.
- Require an IPv4 or IPv6 endpoint using `-4` or `-6`.
- Optionally require a recent handshake using `--max-handshake-age`, without
  generating traffic.
- Install the command using pipx, with no third-party runtime dependencies.
