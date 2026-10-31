#!/usr/bin/env bash
# Read-only readiness check for the deployment target. Changes nothing.
# Run it as the deploy user ON the server, or use the "Server check" GitHub workflow:
#   bash check_server.sh
# Exit code 0 = ready (warnings are allowed), 1 = at least one blocking problem.
set -uo pipefail

failures=0
ok()   { printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; failures=$((failures + 1)); }
section() { printf '\n%s\n' "$*"; }

PYTHON_BIN="${PYTHON_BIN:-python3}"
MODEL_URL="https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
MODEL_DIR="$HOME/.cache/chroma/onnx_models/all-MiniLM-L6-v2"

printf 'Server readiness check for %s@%s\n' "$(id -un)" "$(hostname)"

section "Operating system and account"
if [ -r /etc/os-release ]; then ok "$(. /etc/os-release; echo "$PRETTY_NAME")"; else warn "unknown operating system"; fi
[ "$(id -u)" != 0 ] && ok "running as a normal user (not root)" || warn "running as root; a dedicated deploy user is safer"
[ -w "$HOME" ] && ok "home directory is writable ($HOME)" || bad "home directory is not writable"

section "Python"
if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    ok "$("$PYTHON_BIN" --version 2>&1)"
  else
    bad "$("$PYTHON_BIN" --version 2>&1) is too old; 3.10 or newer is required"
  fi
  if "$PYTHON_BIN" -c 'import venv, ensurepip' >/dev/null 2>&1; then ok "venv and ensurepip are available"; else bad "python3-venv is missing (Debian/Ubuntu: sudo apt-get install python3-venv)"; fi
else
  bad "$PYTHON_BIN not found"
fi
command -v tar >/dev/null 2>&1 && ok "tar is available" || bad "tar is missing"

section "Disk space"
free_kb="$(df -Pk "$HOME" | awk 'NR==2 {print $4}')"
if   [ "${free_kb:-0}" -ge 2097152 ]; then ok "$((free_kb / 1024)) MB free in the home filesystem"
elif [ "${free_kb:-0}" -ge 1048576 ]; then warn "only $((free_kb / 1024)) MB free; 2 GB or more is recommended (dependencies and the embedding model)"
else bad "only $((free_kb / 1024)) MB free; at least 1 GB is required"; fi

section "Service manager"
if systemctl --user show-environment >/dev/null 2>&1; then
  ok "systemd --user is available (services will be managed by systemd)"
  linger="$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || true)"
  if [ "$linger" = "yes" ]; then ok "linger is enabled (services survive logout and reboot)"
  else warn "linger is not enabled; an administrator should run: sudo loginctl enable-linger $(id -un)"; fi
else
  warn "systemd --user is not available; the deploy falls back to background processes that do not survive a reboot"
fi

section "Claude CLI (needed to answer questions)"
if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then
  ok "claude found: $( (command -v claude || echo "$HOME/.local/bin/claude") )"
else
  warn "claude is not installed for this user; the assistant cannot answer until it is installed and authenticated"
fi

section "Network access"
fetch() {  # fetch <url> -> exit 0 if reachable
  "$PYTHON_BIN" - "$1" <<'PY' >/dev/null 2>&1
import sys, urllib.request
req = urllib.request.Request(sys.argv[1], method="HEAD")
urllib.request.urlopen(req, timeout=8)
PY
}
if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if fetch "https://pypi.org/simple/pip/" ; then ok "PyPI is reachable (needed to install dependencies)"
  else warn "PyPI is not reachable; dependencies must come from an internal mirror (set PIP_INDEX_URL for this user)"; fi
  if [ -d "$MODEL_DIR" ]; then ok "embedding model already present in $MODEL_DIR"
  elif fetch "$MODEL_URL"; then ok "embedding model host is reachable (the ~79 MB model downloads on first run)"
  else warn "embedding model host is not reachable; copy the model into $MODEL_DIR or allow outbound HTTPS to chroma-onnx-models.s3.amazonaws.com"; fi
fi

section "Ports used by the application"
for port in 8501 8000 9000; do
  if "$PYTHON_BIN" -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', $port)) == 0 else 1)" 2>/dev/null; then
    warn "port $port is already in use (expected if the application is already deployed)"
  else
    ok "port $port is free"
  fi
done

printf '\n'
if [ "$failures" -eq 0 ]; then echo "RESULT: ready to deploy (review any warnings above)"; else echo "RESULT: $failures blocking problem(s); fix them before deploying"; fi
exit $(( failures > 0 ))
