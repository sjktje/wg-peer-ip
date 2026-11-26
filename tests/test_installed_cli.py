"""Opt-in smoke tests of a wheel's installed console command on POSIX systems."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import venv

import pytest


KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires POSIX executables")


@pytest.fixture(scope="session")
def installed_cli(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Install the explicitly supplied wheel without accessing a package index."""
    wheel_path = os.environ.get("WG_PEER_IP_WHEEL")
    if not wheel_path:
        pytest.skip("set WG_PEER_IP_WHEEL to run installed-command smoke tests")
    wheel = Path(wheel_path).resolve()
    assert wheel.is_file(), f"wheel not found: {wheel}"
    assert wheel.suffix == ".whl", f"expected a wheel: {wheel}"
    environment = tmp_path_factory.mktemp("installed-cli") / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run(
        [
            str(environment / "bin" / "python"),
            "-m", "pip", "install", "--no-index", "--no-deps", str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return environment / "bin" / "wg-peer-ip"


@pytest.fixture
def fake_wg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a real executable that records queries without touching WireGuard."""
    executable = tmp_path / "wg"
    executable.write_text(
        f"#!{sys.executable}\n" + textwrap.dedent(
            """\
            import json
            import os
            import sys
            import time

            with open(os.environ["WG_TEST_LOG"], "a") as log:
                log.write(json.dumps(sys.argv[1:]) + "\\n")
            if sys.argv[1:3] != ["show", "wg0"] or len(sys.argv) != 4:
                sys.exit("unexpected wg arguments")
            key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            if sys.argv[3] == "endpoints":
                print(key + "\\t" + os.environ["WG_TEST_ENDPOINT"])
            elif sys.argv[3] == "latest-handshakes":
                if os.environ.get("WG_TEST_FAIL") == "1":
                    sys.exit("Unable to access interface: No such device")
                timestamp = 0 if os.environ.get("WG_TEST_NO_HANDSHAKE") == "1" else int(time.time())
                print(key + "\\t" + str(timestamp))
            else:
                sys.exit("unexpected wg field")
            """
        ),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    log = tmp_path / "wg-queries.jsonl"
    # Exclude the real wg and source-tree imports from the child environment.
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("PYTHONHOME", raising=False)
    monkeypatch.setenv("WG_TEST_LOG", str(log))
    monkeypatch.setenv("WG_TEST_ENDPOINT", "192.168.1.2:51820")
    monkeypatch.delenv("WG_TEST_FAIL", raising=False)
    monkeypatch.delenv("WG_TEST_NO_HANDSHAKE", raising=False)
    return log


def test_installed_help(installed_cli: Path, fake_wg: Path, tmp_path: Path) -> None:
    """Run the packaged entry point without requiring wg for help."""
    result = subprocess.run(
        [str(installed_cli), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "usage: wg-peer-ip" in result.stdout
    assert "--max-handshake-age" in result.stdout
    assert result.stderr == ""
    assert not fake_wg.exists()


@pytest.mark.parametrize(
    ("family", "endpoint", "expected"),
    [
        ("-4", "192.168.1.2:51820", "192.168.1.2"),
        ("-6", "[2001:db8::1]:51820", "2001:db8::1"),
    ],
)
def test_installed_lookup(
    installed_cli: Path,
    fake_wg: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    family: str,
    endpoint: str,
    expected: str,
) -> None:
    """Retrieve an endpoint through the installed command and real subprocesses."""
    monkeypatch.setenv("WG_TEST_ENDPOINT", endpoint)
    result = subprocess.run(
        [str(installed_cli), family, "--max-handshake-age", "300", "wg0", KEY],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == f"{expected}\n"
    assert result.stderr == ""
    assert [json.loads(line) for line in fake_wg.read_text().splitlines()] == [
        ["show", "wg0", "endpoints"],
        ["show", "wg0", "latest-handshakes"],
    ]


@pytest.mark.parametrize(
    ("variable", "message"),
    [
        ("WG_TEST_NO_HANDSHAKE", "peer has no recorded handshake"),
        ("WG_TEST_FAIL", "wg failed: Unable to access interface: No such device"),
    ],
    ids=["missing-handshake", "command-failure"],
)
def test_installed_failure(
    installed_cli: Path,
    fake_wg: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    message: str,
) -> None:
    """Propagate handshake-check failures without emitting an IP address."""
    monkeypatch.setenv(variable, "1")
    result = subprocess.run(
        [str(installed_cli), "--max-handshake-age", "300", "wg0", KEY],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"wg-peer-ip: {message}\n"
    assert [json.loads(line) for line in fake_wg.read_text().splitlines()] == [
        ["show", "wg0", "endpoints"],
        ["show", "wg0", "latest-handshakes"],
    ]
