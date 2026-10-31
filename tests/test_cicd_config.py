"""Guards for the CI/CD setup: no credentials or connection details in the repository, and the pipeline keeps its safety properties."""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci-cd.yml"
WORKFLOW_TEXT = WORKFLOW_PATH.read_text(encoding="utf-8")
WORKFLOW = yaml.safe_load(WORKFLOW_TEXT)

SKIP_DIRS = {".git", "chroma_db", "__pycache__", ".pytest_cache", ".vscode", ".playwright-mcp", "venv", ".venv", "node_modules"}
SKIP_SUFFIXES = {".db", ".tgz", ".log", ".bin", ".sqlite3", ".png"}

# No host or user name is written in this repository, not even in pieces. Any private IPv4 address is
# rejected, and exact values can be supplied at run time, e.g. FORBIDDEN_LITERALS="<host>,<user>" pytest
PRIVATE_IPV4 = r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
FORBIDDEN_LITERALS = [v for v in os.environ.get("FORBIDDEN_LITERALS", "").split(",") if v]
SSHPASS_ARG = "sshpass" + " -p"  # password as a command-line argument: forbidden
FORBIDDEN_PATTERNS = [r"BEGIN (RSA|OPENSSH|EC|PRIVATE)", r"ghp_[A-Za-z0-9]{20}", "github_" + "pat_", "sshpass" + r"\s+-p"]
ALLOWED_SECRETS = {
    "DEPLOY_HOST", "DEPLOY_USER", "DEPLOY_PASSWORD", "DEPLOY_HOST_KEY", "WEBHOOK_SECRET", "TICKET_API_KEY",
    "MOCK_ITSM_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
}


def publishable_files():
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if not path.is_file() or set(rel.parts) & SKIP_DIRS:
            continue
        if path.name == ".env" or path.suffix in SKIP_SUFFIXES:
            continue
        yield path


def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def test_no_connection_details_or_credentials_in_publishable_files():
    offenders = []
    for path in publishable_files():
        text = read_text(path)
        if text is None:
            continue
        for literal in FORBIDDEN_LITERALS:
            if literal in text:
                offenders.append((str(path.relative_to(ROOT)), "host or user literal"))
        if re.search(PRIVATE_IPV4, text):
            offenders.append((str(path.relative_to(ROOT)), "private IPv4 address"))
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, text):
                offenders.append((str(path.relative_to(ROOT)), pattern))
    assert not offenders, offenders


def test_gitignore_blocks_secrets_and_generated_data():
    ignored = (ROOT / ".gitignore").read_text()
    for entry in (".env", "chroma_db/", "*.db", "*.tgz", "*.log", ".vscode/"):
        assert entry in ignored.splitlines(), f"{entry} missing from .gitignore"
    assert ".env.example" in ignored  # explicitly un-ignored template


def test_env_example_holds_placeholders_only():
    for line in (ROOT / ".env.example").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if any(word in key for word in ("SECRET", "KEY", "TOKEN", "PASSWORD")):
                assert value.startswith("<") or value == "", f"{key} must be a placeholder"


def test_workflow_stages_and_ordering():
    jobs = WORKFLOW["jobs"]
    assert set(jobs) == {"test", "build", "deploy"}
    assert jobs["build"]["needs"] == "test" and jobs["deploy"]["needs"] == "build"


def test_workflow_has_least_privilege_and_no_pull_request_deploy():
    assert WORKFLOW["permissions"] == {"contents": "read"}
    condition = WORKFLOW["jobs"]["deploy"]["if"]
    assert "refs/heads/main" in condition and "pull_request" in condition  # pull requests never deploy
    assert WORKFLOW["jobs"]["deploy"]["environment"] == "production"  # approval rules attach here
    assert WORKFLOW["jobs"]["deploy"]["concurrency"]["cancel-in-progress"] is False
    assert "self-hosted" in WORKFLOW["jobs"]["deploy"]["runs-on"]  # a hosted runner cannot reach a private address


def test_only_pull_request_and_main_triggers():
    triggers = WORKFLOW[True] if True in WORKFLOW else WORKFLOW["on"]  # PyYAML reads the key `on` as True
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]


def test_all_credentials_come_from_secrets():
    used = set(re.findall(r"secrets\.([A-Z0-9_]+)", WORKFLOW_TEXT))
    assert used <= ALLOWED_SECRETS, used - ALLOWED_SECRETS
    assert {"DEPLOY_HOST", "DEPLOY_USER", "DEPLOY_PASSWORD", "DEPLOY_HOST_KEY"} <= used


def test_ssh_is_strict_and_password_never_on_a_command_line():
    assert "StrictHostKeyChecking=yes" in WORKFLOW_TEXT
    assert "StrictHostKeyChecking=no" not in WORKFLOW_TEXT and "UserKnownHostsFile=/dev/null" not in WORKFLOW_TEXT
    assert "sshpass -e" in WORKFLOW_TEXT and SSHPASS_ARG not in WORKFLOW_TEXT
    # the password is only exposed to the one step that connects, through the environment
    deploy_steps = WORKFLOW["jobs"]["deploy"]["steps"]
    holders = [s["name"] for s in deploy_steps if "SSHPASS" in str(s.get("env", {}))]
    assert holders == ["Upload the release and deploy it"]


def test_package_allow_list_excludes_tests_and_secrets():
    block = re.search(r"tar -czf release\.tgz \\\n(.*?)\n\s*echo", WORKFLOW_TEXT, re.S).group(1)
    members = block.replace("\\", " ").split()
    for member in members:
        assert not member.startswith(("tests", "scripts", ".env", "chroma_db", ".git")), member
        assert member == "VERSION" or (ROOT / member).exists(), f"package member does not exist: {member}"  # VERSION is generated by the build
    assert {"app.py", "services", "webhook_receiver", "mock_itsm", "deploy", "data/golden"} <= set(members)


def test_deploy_script_syntax_and_safety_flags():
    script = ROOT / "deploy" / "remote_deploy.sh"
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    text = script.read_text()
    assert "set -euo pipefail" in text and "umask 077" in text
    assert "rm -f \"$SETTINGS_FILE\"" in text  # the secrets file is always removed


@pytest.mark.parametrize("path", [".github/dependabot.yml", ".streamlit/config.toml"])
def test_supporting_config_files_exist(path):
    assert (ROOT / path).is_file()


# ---- every workflow that touches the server is held to the same rules ---------------------------------
WORKFLOW_FILES = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_every_workflow_uses_only_known_secrets_and_strict_ssh(path):
    text = path.read_text(encoding="utf-8")
    assert set(re.findall(r"secrets\.([A-Z0-9_]+)", text)) <= ALLOWED_SECRETS
    if "ssh" in text:
        assert "StrictHostKeyChecking=yes" in text and "StrictHostKeyChecking=no" not in text
        assert "sshpass -e" in text and SSHPASS_ARG not in text
    assert re.search(r"permissions:\s*\n\s+contents: read", text)


def test_server_check_is_manual_read_only_and_gated():
    check = yaml.safe_load((ROOT / ".github" / "workflows" / "server-check.yml").read_text())
    triggers = check[True] if True in check else check["on"]
    assert set(triggers) == {"workflow_dispatch"}  # never runs on push or pull request
    job = check["jobs"]["check"]
    assert job["environment"] == "production" and "self-hosted" in job["runs-on"]
    text = (ROOT / ".github" / "workflows" / "server-check.yml").read_text()
    assert "remote_deploy.sh" not in text  # it must not be able to deploy


def test_check_server_script_is_read_only_and_valid():
    script = ROOT / "deploy" / "check_server.sh"
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    # Advice printed through ok/warn/bad may mention sudo or pip; only lines that execute are checked.
    executed = "\n".join(
        line for line in script.read_text().splitlines()
        if not re.match(r"\s*(ok|warn|bad|echo|printf)\b", line) and not re.search(r'\b(ok|warn|bad) "', line)
    )
    for forbidden in ("rm -rf", "sudo ", "systemctl --user enable", "systemctl --user start", "pip install", "mkdir -p"):
        assert forbidden not in executed, f"a read-only check must not execute: {forbidden}"


def test_setup_guide_exists_and_uses_placeholders():
    guide = (ROOT / "docs" / "deployment-setup.md").read_text()
    for secret in ALLOWED_SECRETS - {"ANTHROPIC_BASE_URL"}:
        assert secret in guide, f"{secret} is not documented"
    assert "<server>" in guide and "<deploy-user>" in guide
