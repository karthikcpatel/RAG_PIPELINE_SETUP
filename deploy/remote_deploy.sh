#!/usr/bin/env bash
# Deploys one release. Runs ON the target server, from inside the unpacked release.
# Called by .github/workflows/ci-cd.yml:
#   DEPLOY_ROOT=<dir> bash deploy/remote_deploy.sh <release-id> <settings-file>
#
# Layout under DEPLOY_ROOT:
#   releases/<id>/   unpacked releases (newest 5 kept)        current -> releases/<id>
#   shared/          .env (secrets, mode 600), deploy.conf, venv/, chroma_db/, mock_itsm.db, run/
#
# Order: validate -> install dependencies -> self-check the new release (live services untouched)
#        -> switch -> restart -> health check -> automatic rollback if unhealthy.
set -euo pipefail
umask 077

RELEASE_ID="${1:?usage: remote_deploy.sh <release-id> <settings-file>}"
SETTINGS_FILE="${2:?usage: remote_deploy.sh <release-id> <settings-file>}"
ROOT="${DEPLOY_ROOT:?DEPLOY_ROOT is required}"
PREFIX="${DEPLOY_SERVICE_PREFIX:-rag}"

RELEASE_DIR="$ROOT/releases/$RELEASE_ID"
SHARED="$ROOT/shared"
CURRENT="$ROOT/current"
RUN_DIR="$SHARED/run"
UNIT_DIR="$HOME/.config/systemd/user"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid release id"
[ -d "$RELEASE_DIR" ] || die "release directory not found: $RELEASE_DIR"
[ -f "$SETTINGS_FILE" ] || die "settings file not found"
mkdir -p "$SHARED" "$RUN_DIR"
trap 'rm -f "$SETTINGS_FILE"' EXIT   # the settings file holds secrets; never leave it behind

# ---- key=value files are parsed, never sourced, so a value can't run code ---------------------
declare -A CONF
load_kv() {  # load_kv <file>
  CONF=()
  local key value
  while IFS='=' read -r key value || [ -n "$key" ]; do
    [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] && CONF[$key]="$value"
  done < "$1"
}
conf() { printf '%s' "${CONF[$1]:-${2:-}}"; }

# ---- 1. prerequisites ---------------------------------------------------------------------------
load_kv "$SETTINGS_FILE"
declare -A SETTINGS=(); for k in "${!CONF[@]}"; do SETTINGS[$k]="${CONF[$k]}"; done

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ "${DEPLOY_SKIP_INSTALL:-0}" = "1" ]; then            # test hook: reuse an existing environment
  VENV_PY="${VENV_PYTHON:?VENV_PYTHON is required when DEPLOY_SKIP_INSTALL=1}"
else
  command -v "$PYTHON_BIN" >/dev/null || die "$PYTHON_BIN not found on the server"
  "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "Python 3.10 or newer is required (found $("$PYTHON_BIN" --version 2>&1))"
  VENV_PY="$SHARED/venv/bin/python"
  if [ ! -x "$VENV_PY" ]; then
    log "creating virtual environment"
    "$PYTHON_BIN" -m venv "$SHARED/venv" || die "could not create a virtual environment (is python3-venv installed?)"
  fi
  log "installing dependencies"
  "$VENV_PY" -m pip install --quiet --disable-pip-version-check -r "$RELEASE_DIR/requirements.txt" \
    || die "pip install failed"
fi

# ---- 2. build the new configuration (written beside the live one, promoted only after the self-check) --
for required in WEBHOOK_SECRET TICKET_API_KEY; do
  [ -n "${SETTINGS[$required]:-}" ] || die "required setting $required is missing"
done
MOCK="${SETTINGS[DEPLOY_MOCK_ITSM]:-true}"
if [ "$MOCK" = "true" ]; then
  [ -n "${SETTINGS[MOCK_ITSM_API_KEY]:-}" ] || die "MOCK_ITSM_API_KEY is required when DEPLOY_MOCK_ITSM=true"
fi

{
  printf 'STREAMLIT_BIND_ADDRESS=%s\n' "${SETTINGS[STREAMLIT_BIND_ADDRESS]:-127.0.0.1}"
  printf 'RECEIVER_BIND_ADDRESS=%s\n' "${SETTINGS[RECEIVER_BIND_ADDRESS]:-127.0.0.1}"
  printf 'APP_PORT=%s\n' "${SETTINGS[APP_PORT]:-8501}"
  printf 'RECEIVER_PORT=%s\n' "${SETTINGS[RECEIVER_PORT]:-8000}"
  printf 'ITSM_PORT=%s\n' "${SETTINGS[ITSM_PORT]:-9000}"
  printf 'DEPLOY_MOCK_ITSM=%s\n' "$MOCK"
} > "$SHARED/deploy.conf.new"
load_kv "$SHARED/deploy.conf.new"
APP_PORT="$(conf APP_PORT)"; RECEIVER_PORT="$(conf RECEIVER_PORT)"; ITSM_PORT="$(conf ITSM_PORT)"

{
  for key in WEBHOOK_SECRET TICKET_API_KEY MOCK_ITSM_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL; do
    [ -n "${SETTINGS[$key]:-}" ] && printf '%s=%s\n' "$key" "${SETTINGS[$key]}"
  done
  if [ -n "${SETTINGS[TICKET_API_URL]:-}" ]; then
    printf 'TICKET_API_URL=%s\n' "${SETTINGS[TICKET_API_URL]}"
  elif [ "$MOCK" = "true" ]; then
    printf 'TICKET_API_URL=http://127.0.0.1:%s\n' "$ITSM_PORT"
  fi
  printf 'HELPDESK_WEBHOOK_URL=http://127.0.0.1:%s/webhooks/issues\n' "$RECEIVER_PORT"
  printf 'CHROMA_PATH=%s\n' "$SHARED/chroma_db"
  printf 'MOCK_ITSM_DB=%s\n' "$SHARED/mock_itsm.db"
} > "$SHARED/.env.new"
chmod 600 "$SHARED/.env.new"

# ---- 3. self-check the new release with a throwaway index; live services are not touched ------------
self_check() {
  local tmp status=0
  tmp="$(mktemp -d)"
  ( cd "$RELEASE_DIR"
    set -a; . "$SHARED/.env.new"; set +a   # trusted: generated above from validated values
    export CHROMA_PATH="$tmp/chroma" MOCK_ITSM_DB="$tmp/itsm.db" PYTHONDONTWRITEBYTECODE=1
    "$VENV_PY" - "$MOCK" <<'PY'
import compileall, sys
mock = sys.argv[1] == "true"
if not compileall.compile_dir(".", quiet=2, maxlevels=10):
    sys.exit("syntax errors in the release")
from services import rag_service                         # knowledge base, Chroma and the embedding model
rag_service.refresh_index()
from webhook_receiver.app import create_app as receiver
receiver()                                               # validates required settings
if mock:
    from mock_itsm.app import create_app as itsm
    itsm()
print("self-check passed")
PY
  ) || status=$?
  rm -rf "$tmp"
  return $status
}
log "self-checking release $RELEASE_ID"
self_check || { rm -f "$SHARED/.env.new" "$SHARED/deploy.conf.new"; die "self-check failed; the running version was not changed"; }

if [ "${DEPLOY_WARM_MODEL:-1}" = "1" ]; then
  "$VENV_PY" -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as D; D()(['warm up'])" >/dev/null 2>&1 \
    || log "WARNING: the embedding model could not be loaded; it is downloaded on first use (needs internet access from this server)"
fi
command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ] \
  || log "WARNING: the Claude CLI is not installed for this user; questions cannot be answered until it is installed and authenticated"

# ---- 4. services ----------------------------------------------------------------------------------
use_systemd() { [ "${DEPLOY_FORCE_NOHUP:-0}" != "1" ] && systemctl --user show-environment >/dev/null 2>&1; }

service_cmd() {  # service_cmd <app|webhook|itsm>  -> prints the command line
  case "$1" in
    app)     printf '%q ' "$VENV_PY" -m streamlit run app.py --server.address "$(conf STREAMLIT_BIND_ADDRESS)" --server.port "$APP_PORT" ;;
    webhook) printf '%q ' "$VENV_PY" -m uvicorn webhook_receiver.app:create_app --factory --host "$(conf RECEIVER_BIND_ADDRESS)" --port "$RECEIVER_PORT" ;;
    itsm)    printf '%q ' "$VENV_PY" -m uvicorn mock_itsm.app:create_app --factory --host 127.0.0.1 --port "$ITSM_PORT" ;;
  esac
}

wanted_services() { [ "$(conf DEPLOY_MOCK_ITSM)" = "true" ] && echo "itsm webhook app" || echo "webhook app"; }

stop_pidfile() {
  local pidfile="$RUN_DIR/$PREFIX-$1.pid" pid
  [ -f "$pidfile" ] || return 0
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
}

start_service() {  # start_service <name>
  local name="$1" cmd
  cmd="$(service_cmd "$name")"
  if use_systemd; then
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/$PREFIX-$name.service" <<UNIT
[Unit]
Description=RAG helpdesk $name
After=network.target

[Service]
WorkingDirectory=$CURRENT
EnvironmentFile=$SHARED/.env
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
ExecStart=$cmd
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable "$PREFIX-$name.service" >/dev/null 2>&1 || true
    systemctl --user restart "$PREFIX-$name.service"
  else
    stop_pidfile "$name"
    ( cd "$CURRENT"
      set -a; . "$SHARED/.env"; set +a
      export PATH="$HOME/.local/bin:$PATH" PYTHONUNBUFFERED=1
      eval "nohup setsid $cmd >> '$RUN_DIR/$PREFIX-$name.log' 2>&1 &"
      echo $! > "$RUN_DIR/$PREFIX-$name.pid" )
  fi
}

stop_service() {
  if use_systemd; then
    systemctl --user disable --now "$PREFIX-$1.service" >/dev/null 2>&1 || true
  else
    stop_pidfile "$1"
  fi
}

restart_all() {
  load_kv "$SHARED/deploy.conf"
  APP_PORT="$(conf APP_PORT)"; RECEIVER_PORT="$(conf RECEIVER_PORT)"; ITSM_PORT="$(conf ITSM_PORT)"
  [ "$(conf DEPLOY_MOCK_ITSM)" = "true" ] || stop_service itsm
  local name
  for name in $(wanted_services); do start_service "$name"; done
}

check_url() {
  "$VENV_PY" - "$1" <<'PY'
import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=3).read()
except Exception:
    sys.exit(1)
PY
}

wait_healthy() {
  local deadline=$((SECONDS + ${HEALTH_TIMEOUT:-90})) name url all_ok
  while [ "$SECONDS" -lt "$deadline" ]; do
    all_ok=1
    for name in $(wanted_services); do
      case "$name" in
        app)     url="http://127.0.0.1:$APP_PORT/_stcore/health" ;;
        webhook) url="http://127.0.0.1:$RECEIVER_PORT/health" ;;
        itsm)    url="http://127.0.0.1:$ITSM_PORT/health" ;;
      esac
      check_url "$url" || all_ok=0
    done
    [ "$all_ok" = 1 ] && return 0
    sleep 2
  done
  return 1
}

# ---- 5. switch, restart, verify, roll back on failure ------------------------------------------------
PREV=""
[ -L "$CURRENT" ] && PREV="$(readlink -f "$CURRENT")"

[ -f "$SHARED/.env" ] && cp -p "$SHARED/.env" "$SHARED/.env.prev"
[ -f "$SHARED/deploy.conf" ] && cp -p "$SHARED/deploy.conf" "$SHARED/deploy.conf.prev"
mv "$SHARED/.env.new" "$SHARED/.env"
mv "$SHARED/deploy.conf.new" "$SHARED/deploy.conf"
ln -sfn "$SHARED/.env" "$RELEASE_DIR/.env"

switch_to() { ln -sfn "$1" "$CURRENT.tmp" && mv -T "$CURRENT.tmp" "$CURRENT"; }

rollback() {
  log "ROLLING BACK"
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    [ -f "$SHARED/.env.prev" ] && mv "$SHARED/.env.prev" "$SHARED/.env"
    [ -f "$SHARED/deploy.conf.prev" ] && mv "$SHARED/deploy.conf.prev" "$SHARED/deploy.conf"
    switch_to "$PREV"
    restart_all
    if wait_healthy; then log "rolled back to $(basename "$PREV"); it is healthy"; else log "WARNING: the previous release is not healthy either"; fi
  else
    log "no previous release; stopping the new services"
    for name in itsm webhook app; do stop_service "$name"; done
  fi
  exit 1
}

log "switching to $RELEASE_ID"
switch_to "$RELEASE_DIR"
restart_all
if use_systemd; then
  loginctl enable-linger "$USER" >/dev/null 2>&1 \
    || log "WARNING: could not enable linger; services will stop when $USER logs out and will not start on reboot"
fi

log "waiting for the services to become healthy"
wait_healthy || { log "health check failed"; rollback; }
log "healthy: app :$APP_PORT, webhook :$RECEIVER_PORT$([ "$(conf DEPLOY_MOCK_ITSM)" = true ] && echo ", incident system :$ITSM_PORT")"

# ---- 6. housekeeping -----------------------------------------------------------------------------------
CUR_REAL="$(readlink -f "$CURRENT")"
ls -1t "$ROOT/releases" | tail -n +6 | while read -r old; do
  full="$ROOT/releases/$old"
  [ "$(readlink -f "$full")" = "$CUR_REAL" ] || [ "$(readlink -f "$full")" = "$PREV" ] || rm -rf "$full"
done
log "deployed $RELEASE_ID"
