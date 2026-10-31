"""Runs the real deploy/remote_deploy.sh against a temporary server layout (processes started with nohup).

The package is built from the same file list as the workflow's build job, so this also proves the
package contains everything a release needs to run.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "ci-cd.yml").read_text(encoding="utf-8")
SECRET = "deploy-test-secret-0123456789"
KEY = "deploy-test-key-0123456789"


def package_members():
    """The allow-list the workflow's build job passes to tar."""
    block = re.search(r"tar -czf release\.tgz \\\n(.*?)\n\s*echo", WORKFLOW, re.S).group(1)
    return [token for token in block.replace("\\", " ").split() if token]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def healthy(port, path):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2).read()
        return True
    except Exception:
        return False


class Server:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.root = tmp_path / "server" / "rag-helpdesk"
        self.ports = {"APP_PORT": free_port(), "RECEIVER_PORT": free_port(), "ITSM_PORT": free_port()}
        self.count = 0
        self.env = {
            **os.environ,
            "DEPLOY_ROOT": str(self.root), "DEPLOY_SKIP_INSTALL": "1", "VENV_PYTHON": sys.executable,
            "DEPLOY_FORCE_NOHUP": "1", "DEPLOY_WARM_MODEL": "0", "DEPLOY_SERVICE_PREFIX": "ragtest", "HEALTH_TIMEOUT": "60",
        }

    def make_release(self, tweak=None):
        """Build the package like CI does, unpack it into releases/, optionally corrupt it. Returns the release id."""
        self.count += 1
        release_id = f"test-r{self.count}"
        package = self.tmp / f"{release_id}.tgz"
        (ROOT / "VERSION").write_text(release_id)
        try:
            with tarfile.open(package, "w:gz") as archive:
                for member in package_members():
                    archive.add(ROOT / member, arcname=member, filter=lambda i: None if "__pycache__" in i.name else i)
        finally:
            (ROOT / "VERSION").unlink()
        target = self.root / "releases" / release_id
        target.mkdir(parents=True)
        with tarfile.open(package) as archive:
            archive.extractall(target, filter="data")
        if tweak:
            tweak(target)
        return release_id

    def deploy(self, release_id, **overrides):
        settings = {
            "WEBHOOK_SECRET": SECRET, "TICKET_API_KEY": KEY, "MOCK_ITSM_API_KEY": KEY,
            "STREAMLIT_BIND_ADDRESS": "127.0.0.1", "RECEIVER_BIND_ADDRESS": "127.0.0.1",
            **{k: str(v) for k, v in self.ports.items()}, "DEPLOY_MOCK_ITSM": "true",
        }
        settings.update(overrides)
        settings = {k: v for k, v in settings.items() if v is not None}
        settings_file = self.tmp / f"settings-{release_id}"
        settings_file.write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
        result = subprocess.run(
            ["bash", str(self.root / "releases" / release_id / "deploy" / "remote_deploy.sh"), release_id, str(settings_file)],
            env=self.env, capture_output=True, text=True, timeout=300,
        )
        self.settings_file = settings_file
        return result

    def current(self):
        return (self.root / "current").resolve().name

    def all_healthy(self):
        p = self.ports
        return healthy(p["APP_PORT"], "/_stcore/health") and healthy(p["RECEIVER_PORT"], "/health") and healthy(p["ITSM_PORT"], "/health")

    def stop(self):
        run_dir = self.root / "shared" / "run"
        for pidfile in run_dir.glob("*.pid") if run_dir.exists() else []:
            try:
                os.kill(int(pidfile.read_text()), 9)
            except (OSError, ValueError):
                pass


@pytest.fixture
def server(tmp_path):
    srv = Server(tmp_path)
    yield srv
    srv.stop()


def test_deploy_upgrade_failures_and_rollback(server):
    # 1. first deployment
    first = server.make_release()
    result = server.deploy(first)
    assert result.returncode == 0, result.stdout + result.stderr
    assert server.current() == first and server.all_healthy()
    env_file = server.root / "shared" / ".env"
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert f"WEBHOOK_SECRET={SECRET}" in env_file.read_text()
    assert not server.settings_file.exists(), "the settings file holding secrets must be removed"
    assert SECRET not in result.stdout + result.stderr and KEY not in result.stdout + result.stderr

    # 2. upgrade
    second = server.make_release()
    result = server.deploy(second)
    assert result.returncode == 0, result.stdout + result.stderr
    assert server.current() == second and server.all_healthy()
    assert (server.root / "releases" / first).exists(), "the previous release is kept for rollback"

    # 3. a release that does not even compile is rejected before anything is switched (no downtime)
    broken = server.make_release(lambda release: (release / "services" / "rag_service.py").write_text("def broken(:\n"))
    result = server.deploy(broken)
    assert result.returncode != 0 and "self-check failed" in result.stderr
    assert server.current() == second and server.all_healthy()

    # 4. a failure only visible once the services start: automatic rollback to the previous release and config
    third = server.make_release()
    result = server.deploy(third, STREAMLIT_BIND_ADDRESS="999.999.999.999", HEALTH_TIMEOUT=None)
    assert result.returncode != 0 and "ROLLING BACK" in result.stdout
    assert server.current() == second
    assert "STREAMLIT_BIND_ADDRESS=127.0.0.1" in (server.root / "shared" / "deploy.conf").read_text()
    assert server.all_healthy(), "the previous release must be serving again after the rollback"

    # 5. a missing required secret fails early and changes nothing
    fourth = server.make_release()
    result = server.deploy(fourth, WEBHOOK_SECRET=None)
    assert result.returncode != 0 and "WEBHOOK_SECRET is missing" in result.stderr
    assert server.current() == second and server.all_healthy()


def test_old_releases_are_pruned(server):
    ids = []
    for _ in range(7):  # like the pipeline: each release is unpacked just before it is deployed
        release_id = server.make_release()
        ids.append(release_id)
        result = server.deploy(release_id)
        assert result.returncode == 0, result.stdout + result.stderr
    remaining = {p.name for p in (server.root / "releases").iterdir()}
    assert ids[-1] in remaining and ids[-2] in remaining and len(remaining) <= 6
