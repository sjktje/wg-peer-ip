"""Tests for endpoint lookup and CLI behaviour without a live interface."""

import subprocess
from unittest.mock import Mock

import pytest

from wg_peer_ip import cli


KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
NOW = 1_800_000_000


@pytest.fixture
def wg(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Replace wg queries and fix the clock for deterministic tests."""
    run = Mock(
        return_value=subprocess.CompletedProcess(
            [], 0, stdout=f"{KEY}\t192.168.1.2:51820\n", stderr=""
        )
    )
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli.time, "time", lambda: NOW + 0.75)
    return run


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("192.168.1.2:51820", "192.168.1.2"),
        ("203.0.113.8:1234", "203.0.113.8"),
        ("[2001:db8:0:0::1]:51820", "2001:db8::1"),
        ("[fe80::1%eth0]:51820", "fe80::1%eth0"),
    ],
)
def test_endpoint(wg: Mock, endpoint: str, expected: str) -> None:
    wg.return_value.stdout = f"other-key\t198.51.100.1:9\n{KEY}\t{endpoint}\n"
    assert cli.get_peer_ip("wg0", KEY) == expected
    wg.assert_called_once_with(
        ["wg", "show", "wg0", "endpoints"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    ("endpoint", "family"),
    [("192.168.1.2:51820", 4), ("[2001:db8::1]:51820", 6)],
)
def test_matching_family(wg: Mock, endpoint: str, family: int) -> None:
    wg.return_value.stdout = f"{KEY}\t{endpoint}\n"
    assert cli.get_peer_ip("wg0", KEY, family)


@pytest.mark.parametrize(
    ("endpoint", "family"),
    [("192.168.1.2:51820", 6), ("[2001:db8::1]:51820", 4)],
)
def test_wrong_family(wg: Mock, endpoint: str, family: int) -> None:
    wg.return_value.stdout = f"{KEY}\t{endpoint}\n"
    with pytest.raises(cli.PeerIPError, match=f"not IPv{family}"):
        cli.get_peer_ip("wg0", KEY, family)


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("", "peer not found"),
        (f"{KEY}extra\t192.0.2.1:51820\n", "peer not found"),
        (f"{KEY}\t(none)\n", "no endpoint"),
        (f"{KEY}\n", "invalid endpoints output"),
        (f"{KEY}\t192.0.2.1:1\textra\n", "invalid endpoints output"),
    ],
)
def test_missing_or_malformed_peer(wg: Mock, output: str, message: str) -> None:
    wg.return_value.stdout = output
    with pytest.raises(cli.PeerIPError, match=message):
        cli.get_peer_ip("wg0", KEY)


@pytest.mark.parametrize(
    "endpoint",
    [
        "example.com:51820",
        "192.168.1.2",
        "192.168.1.2:abc",
        "192.168.1.2:65536",
        "192.168.1.2:-1",
        "[2001:db8::1:51820",
        "2001:db8::1:51820",
        "[192.168.1.2]:51820",
    ],
)
def test_invalid_endpoint(wg: Mock, endpoint: str) -> None:
    wg.return_value.stdout = f"{KEY}\t{endpoint}\n"
    with pytest.raises(cli.PeerIPError, match="invalid endpoint"):
        cli.get_peer_ip("wg0", KEY)


@pytest.mark.parametrize(
    ("timestamp", "limit", "message"),
    [
        (str(NOW), 0, None),
        (str(NOW - 299), 300, None),
        (str(NOW - 300), 300, None),
        (str(NOW - 301), 300, "301 seconds old"),
        ("0", 300, "no recorded handshake"),
        (str(NOW + 1), 300, "in the future"),
        ("-1", 300, "invalid handshake timestamp"),
        ("invalid", 300, "invalid handshake timestamp"),
    ],
)
def test_handshake(
    wg: Mock, timestamp: str, limit: int, message: str | None
) -> None:
    wg.side_effect = [
        wg.return_value,
        subprocess.CompletedProcess([], 0, stdout=f"{KEY}\t{timestamp}\n"),
    ]
    if message is None:
        assert cli.get_peer_ip("wg0", KEY, max_handshake_age=limit) == "192.168.1.2"
    else:
        with pytest.raises(cli.PeerIPError, match=message):
            cli.get_peer_ip("wg0", KEY, max_handshake_age=limit)
    assert [call.args[0] for call in wg.call_args_list] == [
        ["wg", "show", "wg0", "endpoints"],
        ["wg", "show", "wg0", "latest-handshakes"],
    ]


def test_peer_removed_between_queries(wg: Mock) -> None:
    wg.side_effect = [
        wg.return_value,
        subprocess.CompletedProcess([], 0, stdout=""),
    ]
    with pytest.raises(cli.PeerIPError, match="peer not found"):
        cli.get_peer_ip("wg0", KEY, max_handshake_age=300)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (FileNotFoundError(), "install wireguard-tools"),
        (PermissionError("permission denied"), "could not execute wg"),
        (subprocess.TimeoutExpired("wg", 10), "timed out"),
        (
            subprocess.CalledProcessError(1, "wg", stderr="Operation not permitted\n"),
            "Operation not permitted",
        ),
        (subprocess.CalledProcessError(2, "wg", stderr=""), "exit status 2"),
    ],
)
def test_command_failure(wg: Mock, error: Exception, message: str) -> None:
    wg.side_effect = error
    with pytest.raises(cli.PeerIPError, match=message):
        cli.get_peer_ip("wg0", KEY)


@pytest.mark.parametrize("interface", ["", "all", "interfaces"])
def test_interface_selector_rejected(wg: Mock, interface: str) -> None:
    with pytest.raises(cli.PeerIPError, match="single interface"):
        cli.get_peer_ip(interface, KEY)
    wg.assert_not_called()


def test_cli_success(wg: Mock, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["-4", "wg0", KEY]) == 0
    captured = capsys.readouterr()
    assert captured.out == "192.168.1.2\n"
    assert captured.err == ""


def test_cli_failure(wg: Mock, capsys: pytest.CaptureFixture[str]) -> None:
    wg.return_value.stdout = f"{KEY}\t(none)\n"
    assert cli.main(["wg0", KEY]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "wg-peer-ip: peer has no endpoint\n"


def test_cli_handshake_option(wg: Mock, capsys: pytest.CaptureFixture[str]) -> None:
    wg.side_effect = [
        wg.return_value,
        subprocess.CompletedProcess([], 0, stdout=f"{KEY}\t{NOW - 300}\n"),
    ]
    assert cli.main(["--max-handshake-age", "300", "wg0", KEY]) == 0
    assert capsys.readouterr().out == "192.168.1.2\n"
    assert wg.call_count == 2


@pytest.mark.parametrize(
    ("timestamp", "message"),
    [
        (NOW - 301, "peer handshake is 301 seconds old (maximum 300)"),
        (0, "peer has no recorded handshake"),
        (NOW + 1, "peer handshake is in the future; check the system clock"),
    ],
    ids=["stale", "missing", "future"],
)
def test_cli_handshake_rejection(
    wg: Mock,
    capsys: pytest.CaptureFixture[str],
    timestamp: int,
    message: str,
) -> None:
    """Reject invalid handshake ages without printing the retrieved endpoint."""
    wg.side_effect = [
        wg.return_value,
        subprocess.CompletedProcess([], 0, stdout=f"{KEY}\t{timestamp}\n"),
    ]
    assert cli.main(["--max-handshake-age", "300", "wg0", KEY]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"wg-peer-ip: {message}\n"
    assert [call.args[0] for call in wg.call_args_list] == [
        ["wg", "show", "wg0", "endpoints"],
        ["wg", "show", "wg0", "latest-handshakes"],
    ]


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            subprocess.TimeoutExpired("wg", 10),
            "wg timed out after 10 seconds",
        ),
        (
            subprocess.CalledProcessError(
                1, "wg", stderr="Unable to access interface: No such device\n"
            ),
            "wg failed: Unable to access interface: No such device",
        ),
    ],
    ids=["timeout", "command-failure"],
)
def test_cli_handshake_query_failure(
    wg: Mock,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    message: str,
) -> None:
    """Suppress the retrieved endpoint when the handshake query fails."""
    wg.side_effect = [wg.return_value, error]
    assert cli.main(["--max-handshake-age", "300", "wg0", KEY]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"wg-peer-ip: {message}\n"
    assert [call.args[0] for call in wg.call_args_list] == [
        ["wg", "show", "wg0", "endpoints"],
        ["wg", "show", "wg0", "latest-handshakes"],
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["-4", "-6", "wg0", KEY],
        ["--max-handshake-age", "-1", "wg0", KEY],
        ["--max-handshake-age", "1.5", "wg0", KEY],
        ["--max-handshake-age", "invalid", "wg0", KEY],
        ["wg0"],
        [],
    ],
)
def test_invalid_arguments(wg: Mock, args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2
    wg.assert_not_called()
