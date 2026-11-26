"""Command-line interface for querying WireGuard peer endpoints."""

import argparse
import ipaddress
import subprocess
import sys
import time
from collections.abc import Sequence


class PeerIPError(Exception):
    """An endpoint lookup could not be completed."""


def _peer_value(interface: str, public_key: str, field: str) -> str:
    """Read a peer's value from a non-sensitive WireGuard field.

    Parameters
    ----------
    interface : str
        A single WireGuard interface name.
    public_key : str
        The peer's public key to match exactly.
    field : str
        The non-sensitive field to query: ``endpoints`` or
        ``latest-handshakes``.

    Returns
    -------
    str
        The peer's field value as reported by ``wg``, without surrounding
        whitespace. Values such as ``(none)`` and ``0`` are returned unchanged.

    Raises
    ------
    PeerIPError
        If ``wg`` cannot be executed, fails, or exceeds the ten-second timeout;
        if the peer is absent; or if its output row does not have two fields.
    """
    try:
        result = subprocess.run(
            ["wg", "show", interface, field],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise PeerIPError("wg was not found; install wireguard-tools and check PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise PeerIPError("wg timed out after 10 seconds") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or f"exit status {exc.returncode}"
        raise PeerIPError(f"wg failed: {detail}") from exc
    except OSError as exc:
        raise PeerIPError(f"could not execute wg: {exc}") from exc

    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == public_key:
            if len(fields) != 2:
                raise PeerIPError(f"invalid {field} output from wg")
            return fields[1]
    raise PeerIPError(f"peer not found on interface {interface!r}")


def get_peer_ip(
    interface: str,
    public_key: str,
    family: int | None = None,
    max_handshake_age: int | None = None,
) -> str:
    """Retrieve a peer's endpoint address, optionally checking its handshake.

    Parameters
    ----------
    interface : str
        A single WireGuard interface name.
    public_key : str
        The peer's exact public key.
    family : {4, 6, None}, optional
        Required IP version, or either version when omitted.
    max_handshake_age : int or None, optional
        Maximum handshake age in seconds. No traffic is generated.

    Returns
    -------
    str
        The endpoint IP without its port or IPv6 brackets.

    Raises
    ------
    PeerIPError
        If the lookup fails or the requested conditions are not met.
    """
    if not interface or interface in {"all", "interfaces"}:
        raise PeerIPError("specify a single interface, not 'all' or 'interfaces'")
    if family not in (None, 4, 6):
        raise PeerIPError("address family must be 4 or 6")
    if max_handshake_age is not None and max_handshake_age < 0:
        raise PeerIPError("maximum handshake age must be non-negative")

    endpoint = _peer_value(interface, public_key, "endpoints")
    if endpoint == "(none)":
        raise PeerIPError("peer has no endpoint")
    host, separator, port = endpoint.rpartition(":")
    try:
        if not separator or not port.isascii() or not port.isdecimal():
            raise ValueError
        if not 0 <= int(port) <= 65535:
            raise ValueError
        if host.startswith("[") and host.endswith("]"):
            address = ipaddress.IPv6Address(host[1:-1])
        else:
            address = ipaddress.IPv4Address(host)
    except ValueError as exc:
        raise PeerIPError("invalid endpoint IP address or port reported by wg") from exc

    if family is not None and address.version != family:
        raise PeerIPError(f"peer endpoint is IPv{address.version}, not IPv{family}")

    if max_handshake_age is not None:
        value = _peer_value(interface, public_key, "latest-handshakes")
        try:
            timestamp = int(value)
            if timestamp < 0:
                raise ValueError
        except ValueError as exc:
            raise PeerIPError("invalid handshake timestamp reported by wg") from exc
        if timestamp == 0:
            raise PeerIPError("peer has no recorded handshake")
        # wg timestamps have whole-second precision; use the same precision here.
        age = int(time.time()) - timestamp
        if age < 0:
            raise PeerIPError("peer handshake is in the future; check the system clock")
        if age > max_handshake_age:
            raise PeerIPError(
                f"peer handshake is {age} seconds old (maximum {max_handshake_age})"
            )

    return str(address)


def _non_negative_int(value: str) -> int:
    """Parse a non-negative integer argument.

    Parameters
    ----------
    value : str
        Command-line argument to convert to an integer.

    Returns
    -------
    int
        The parsed integer, greater than or equal to zero.

    Raises
    ------
    argparse.ArgumentTypeError
        If the value cannot be parsed as an integer or is negative.
    """
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its exit status."""
    parser = argparse.ArgumentParser(
        prog="wg-peer-ip",
        description="Print a WireGuard peer's endpoint IP address.",
    )
    families = parser.add_mutually_exclusive_group()
    families.add_argument(
        "-4", dest="family", action="store_const", const=4, help="require IPv4"
    )
    families.add_argument(
        "-6", dest="family", action="store_const", const=6, help="require IPv6"
    )
    parser.add_argument(
        "--max-handshake-age",
        type=_non_negative_int,
        metavar="SECONDS",
        help="require a recorded handshake no older than SECONDS (passive check)",
    )
    parser.add_argument("interface", help="WireGuard interface name")
    parser.add_argument("public_key", metavar="public-key", help="peer public key")
    args = parser.parse_args(argv)
    try:
        address = get_peer_ip(
            args.interface, args.public_key, args.family, args.max_handshake_age
        )
    except PeerIPError as exc:
        print(f"{parser.prog}: {exc}", file=sys.stderr)
        return 1
    print(address)
    return 0
