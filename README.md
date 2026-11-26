# wg-peer-ip

Print the endpoint IP address of a WireGuard peer using `wg` from
`wireguard-tools`. Runtime dependencies are limited to the Python standard
library. Python 3.10 or newer is required.

## Installation

Install `wireguard-tools` using your operating system's package manager, then
install this project from its checkout:

```sh
pipx install .
```

The caller must have permission to query the interface with `wg show` (typically
root or the appropriate network administration capability on Linux). The script
does not invoke `sudo`. If running through `sudo`, ensure the installed
`wg-peer-ip` executable is available in that environment, or use its full path.

## Usage

```text
wg-peer-ip [-h] [-4 | -6] [--max-handshake-age SECONDS] interface public-key
```

```sh
wg-peer-ip wg0 'PEER_PUBLIC_KEY'
wg-peer-ip -4 --max-handshake-age 300 wg0 'PEER_PUBLIC_KEY'
wg-peer-ip -6 wg0 'PEER_PUBLIC_KEY'
```

- Success prints only the IP address, without a port or IPv6 brackets.
- The address is the endpoint reported by WireGuard, not the peer's tunnel IP
  or an address discovered through an external service. Private and link-local
  addresses are accepted. IPv6 addresses are printed in compressed form, with
  any scope identifier preserved.
- `-4` and `-6` require a matching endpoint family; they do not select another
  address or perform DNS resolution.
- `--max-handshake-age` accepts a non-negative integer number of seconds. A
  handshake exactly that old is accepted, using whole-second precision. Zero
  requires a handshake in the current second. A missing, stale, or future-dated
  handshake fails. Without this option, the handshake is not queried.
- Failures print an error to stderr and exit with status 1. Invalid CLI arguments
  exit with status 2. No IP address is printed on failure.

## Security and Freshness

The script only runs these commands, without a shell:

```sh
wg show INTERFACE endpoints
wg show INTERFACE latest-handshakes
```

The second command is only used when handshake checking is requested. Both
commands expose peer public keys but no private or preshared keys. The script
never uses `dump` or `showconf`. Each command has a ten-second timeout.

Queries are passive: no traffic is generated and no handshake is triggered.
A recent handshake confirms recent authenticated activity, not that the peer
is still reachable or that its IP has not changed since then. Endpoint and
handshake queries are separate snapshots, not an atomic read; a peer may roam
or be reconfigured between them.

## Development

```sh
poetry install
poetry run pytest
poetry build
```

Unit tests mock `wg` and the clock; they do not require WireGuard or elevated
privileges. Pytest is a development dependency only. Development requires
Poetry 2.x.

### Installed-Command Smoke Tests

On POSIX systems, build a wheel and explicitly select it to run the packaging
smoke tests:

```sh
poetry build --format wheel
WG_PEER_IP_WHEEL=dist/wg_peer_ip-0.1.0-py3-none-any.whl poetry run pytest tests/test_installed_cli.py
```

These tests install the selected wheel into a temporary virtual environment
without accessing a package index or installing dependencies. They run the
installed console command outside the checkout against a fake `wg` executable,
checking help, IPv4/IPv6 lookups, and failure handling. No real WireGuard
interface or elevated privileges are needed. Without `WG_PEER_IP_WHEEL`, these
tests are skipped; an invalid wheel path fails rather than skipping. Set the
variable when running the full suite to include them alongside the unit tests.
