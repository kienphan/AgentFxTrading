#!/usr/bin/env bash
# =============================================================================
# AgentFxTrading — one-shot installer for a fresh Ubuntu 22.04 / 24.04 / 26.04 VPS
#
#   curl -fsSL https://raw.githubusercontent.com/kienphan/AgentFxTrading/main/scripts/install.sh | sudo bash
#
# Optional environment overrides:
#   FORGE_SSH_KEY   public key for the forge user (otherwise prompted on /dev/tty)
#   AGENTFX_REPO    git URL to clone   (default: upstream GitHub repo)
#   AGENTFX_BRANCH  branch to check out (default: main)
#   (export them and run `... | sudo -E bash`; plain `sudo` strips the environment)
#
# Idempotent: every step checks its own post-condition first, so re-running the
# script is the upgrade path.
# Design: docs/superpowers/specs/2026-09-18-vps-install-script-design.md
# =============================================================================
set -Eeuo pipefail

# --- configuration -----------------------------------------------------------
FORGE_USER="${FORGE_USER:-forge}"
FORGE_GROUP="${FORGE_GROUP:-$FORGE_USER}"
FORGE_HOME="${FORGE_HOME:-/home/${FORGE_USER}}"
REPO_DIR="${REPO_DIR:-${FORGE_HOME}/AgentFxTrading}"
CTRADER_HOME="${CTRADER_HOME:-${FORGE_HOME}/ctrader}"
AGENTFX_REPO="${AGENTFX_REPO:-https://github.com/kienphan/AgentFxTrading.git}"
AGENTFX_BRANCH="${AGENTFX_BRANCH:-main}"
CTRADER_IMAGE="${CTRADER_IMAGE:-ghcr.io/spotware/ctrader-console:latest}"
DB_NAME="agentfx"
DB_USER="agentfx"
SERVICE_NAME="agentfx"
DB_PASSWORD=""        # resolved by install_postgresql, consumed by write_env
SSH_PUBLIC_KEY=""     # resolved by read_ssh_key
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a   # Ubuntu 22.04: never pop the "restart services?" dialog

# --- logging -----------------------------------------------------------------
CURRENT_STEP="startup"
log()  { printf '==> %s\n' "$*"; }
step() { CURRENT_STEP="$1"; log "$1"; }
die()  { printf 'ERROR [%s]: %s\n' "$CURRENT_STEP" "$*" >&2; exit 1; }
on_error() {
  printf '\nInstall failed during step "%s" (line %s). Fix the issue and re-run; completed steps are skipped.\n' \
    "$CURRENT_STEP" "$1" >&2
}
trap 'on_error $LINENO' ERR

# --- pure helpers (unit-tested from tests/test_install_script.py) ------------
urlencode() {
  # usage: urlencode "<string>"  → percent-encodes everything except A-Za-z0-9.~_-
  local s="$1" out="" i c
  for (( i = 0; i < ${#s}; i++ )); do
    c="${s:i:1}"
    case "$c" in
      [a-zA-Z0-9.~_-]) out+="$c" ;;
      *) out+=$(printf '%%%02X' "'$c") ;;
    esac
  done
  printf '%s' "$out"
}

urldecode() {
  # usage: urldecode "<string>"  → reverses urlencode (libpq semantics: '+' is literal, only %XX decodes)
  printf '%b' "${1//%/\\x}"
}

validate_ssh_key() {
  # usage: validate_ssh_key "<public key line>"  → 0 iff ssh-keygen can fingerprint it
  local tmp rc=1
  tmp="$(mktemp)"
  printf '%s\n' "$1" > "$tmp"
  if ssh-keygen -l -f "$tmp" >/dev/null 2>&1; then rc=0; fi
  rm -f "$tmp"
  return "$rc"
}

supported_ubuntu_version() {
  # usage: supported_ubuntu_version "<VERSION_ID>"  → 0 iff it is an LTS release this script is tested on
  case "$1" in
    22.04|24.04|26.04) return 0 ;;
    *) return 1 ;;
  esac
}

db_password_from_env() {
  # usage: db_password_from_env "<path/.env>"  → prints decoded password from DATABASE_URL (or nothing)
  local env_file="$1" url pw
  [[ -f "$env_file" ]] || return 0
  url="$(grep -E '^DATABASE_URL=' "$env_file" | tail -n1 | cut -d= -f2- | tr -d "\"'" || true)"
  [[ -n "$url" ]] || return 0
  pw="${url#*://}"   # user:pass@host...
  pw="${pw#*:}"      # pass@host...
  pw="${pw%%@*}"     # pass (still percent-encoded, so '@' inside it is safe)
  urldecode "$pw"
}

sshd_password_auth_disabled() {
  # usage: sshd_password_auth_disabled  → 0 iff `sshd -T` reports passwordauthentication no
  # Capture first, grep second. `sshd -T | grep -q` is a false negative under pipefail:
  # the keyword sits in the first 4 KiB, grep -q exits on it, sshd (which only ignores
  # SIGPIPE after -T) dies writing the rest → exit 141. Seen on OpenSSH 10.2 (Ubuntu 26.04).
  local effective
  effective="$(sshd -T 2>/dev/null)" || return 1
  grep -qix 'passwordauthentication no' <<<"$effective"
}

generate_password() { openssl rand -hex 24; }

# --- system steps --------------------------------------------------------------
preflight() {
  step "Preflight checks"
  [[ "$(id -u)" -eq 0 ]] || die "Run as root:  curl -fsSL <url>/install.sh | sudo bash"
  [[ -r /etc/os-release ]] || die "Cannot read /etc/os-release"
  local os_id os_version
  # shellcheck disable=SC1091
  os_id="$(. /etc/os-release && printf '%s' "${ID:-}")"
  # shellcheck disable=SC1091
  os_version="$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")"
  [[ "$os_id" == "ubuntu" ]] || die "Ubuntu only (detected: ${os_id:-unknown})"
  supported_ubuntu_version "$os_version" \
    || die "Ubuntu 22.04, 24.04 or 26.04 required (detected: ${os_version:-unknown})"
  command -v systemctl >/dev/null || die "systemd is required"
  log "Ubuntu ${os_version} detected"
}

# --- render helpers ------------------------------------------------------------
render_env() {
  # usage: render_env "<.env.example>" "<database_url>" "<ctrader_home>"  → prints a fresh .env
  local example="$1" db_url="$2" ctrader_home="$3"
  # Drop the template's DATABASE_URL / CTRADER_HOME lines (commented or not), then append real values.
  grep -vE '^#? ?(DATABASE_URL|CTRADER_HOME)=' "$example" || true
  printf '\nDATABASE_URL=%s\n' "$db_url"
  printf 'CTRADER_HOME=%s\n' "$ctrader_home"
}

render_systemd_unit() {
  cat <<EOF
[Unit]
Description=AgentFxTrading FastAPI server
After=network-online.target postgresql.service docker.service
Wants=network-online.target

[Service]
User=${FORGE_USER}
Group=${FORGE_GROUP}
WorkingDirectory=${REPO_DIR}
ExecStart=${REPO_DIR}/.venv/bin/uvicorn app.server:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

render_sshd_dropin() {
  cat <<'EOF'
# Managed by AgentFxTrading scripts/install.sh — key-only SSH.
# Named 00-* on purpose: sshd uses the FIRST value it sees for a keyword and
# cloud images ship 50-cloud-init.conf with PasswordAuthentication yes.
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
EOF
}

render_backup_cron() {
  printf '0 3 * * * %s %s/scripts/backup_postgres.sh\n' "$FORGE_USER" "$REPO_DIR"
}

write_env() {
  step "Writing ${REPO_DIR}/.env"
  umask 077
  local env_file="${REPO_DIR}/.env" db_url
  db_url="postgresql://${DB_USER}:$(urlencode "$DB_PASSWORD")@127.0.0.1:5432/${DB_NAME}"
  if [[ -f "$env_file" ]]; then
    log ".env already exists — keeping it"
    if ! grep -qE '^DATABASE_URL=' "$env_file"; then
      printf 'DATABASE_URL=%s\n' "$db_url" >> "$env_file"
      log "Appended DATABASE_URL"
    fi
    if ! grep -qE '^CTRADER_HOME=' "$env_file"; then
      printf 'CTRADER_HOME=%s\n' "$CTRADER_HOME" >> "$env_file"
      log "Appended CTRADER_HOME=${CTRADER_HOME}"
    fi
  else
    render_env "${REPO_DIR}/.env.example" "$db_url" "$CTRADER_HOME" > "$env_file"
    log "Created .env with DATABASE_URL and CTRADER_HOME (LLM keys left as placeholders)"
  fi
  chown "${FORGE_USER}:${FORGE_GROUP}" "$env_file"
  chmod 0600 "$env_file"
}

read_ssh_key() {
  step "SSH public key for user ${FORGE_USER}"
  local key="${FORGE_SSH_KEY:-}"
  if [[ -z "$key" ]]; then
    { : </dev/tty; } 2>/dev/null || die "FORGE_SSH_KEY is not set and there is no terminal to prompt on. Re-run with FORGE_SSH_KEY='ssh-ed25519 AAAA... you@laptop'"
    printf 'Paste the SSH public key that will log in as %s (one line, e.g. "ssh-ed25519 AAAA... you@laptop"):\n> ' "$FORGE_USER" > /dev/tty
    IFS= read -r key < /dev/tty
  fi
  key="$(printf '%s' "$key" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [[ -n "$key" ]] || die "SSH public key is empty — refusing to continue because password login will be disabled"
  validate_ssh_key "$key" || die "Not a valid SSH public key: ${key:0:40}..."
  SSH_PUBLIC_KEY="$key"
  log "Key accepted (${key%% *})"
}

apt_install() { apt-get -o DPkg::Lock::Timeout=600 install -y -q "$@"; }

install_base_packages() {
  step "Installing base packages"
  apt-get -o DPkg::Lock::Timeout=600 update -q
  apt_install git curl ca-certificates gnupg lsb-release openssl cron ufw \
    python3 python3-venv python3-dev build-essential libpq-dev
}

create_forge_user() {
  step "Creating user ${FORGE_USER}"
  if id "$FORGE_USER" >/dev/null 2>&1; then
    log "User ${FORGE_USER} already exists"
  else
    adduser --disabled-password --gecos "" "$FORGE_USER"
  fi
  usermod -aG sudo "$FORGE_USER"
  printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$FORGE_USER" > "/etc/sudoers.d/${FORGE_USER}"
  chmod 0440 "/etc/sudoers.d/${FORGE_USER}"
  visudo -cf "/etc/sudoers.d/${FORGE_USER}" >/dev/null || die "Generated sudoers file is invalid"

  install -d -m 0700 -o "$FORGE_USER" -g "$FORGE_GROUP" "${FORGE_HOME}/.ssh"
  local auth="${FORGE_HOME}/.ssh/authorized_keys"
  touch "$auth"
  if grep -qxF "$SSH_PUBLIC_KEY" "$auth"; then
    log "Key already present in authorized_keys"
  else
    printf '%s\n' "$SSH_PUBLIC_KEY" >> "$auth"
    log "Key added to authorized_keys"
  fi
  chown "${FORGE_USER}:${FORGE_GROUP}" "$auth"
  chmod 0600 "$auth"
}

harden_sshd() {
  step "Hardening sshd (key-only login)"
  grep -qxF "$SSH_PUBLIC_KEY" "${FORGE_HOME}/.ssh/authorized_keys" \
    || die "authorized_keys does not contain the key — refusing to disable password login"
  install -d -m 0755 /etc/ssh/sshd_config.d
  if ! grep -qE '^Include /etc/ssh/sshd_config\.d/\*\.conf' /etc/ssh/sshd_config; then
    sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' /etc/ssh/sshd_config
  fi
  render_sshd_dropin > /etc/ssh/sshd_config.d/00-agentfx.conf
  sshd -t || die "sshd -t failed; NOT reloading. Inspect /etc/ssh/sshd_config.d/00-agentfx.conf"
  systemctl reload ssh 2>/dev/null || systemctl restart ssh
  sshd_password_auth_disabled \
    || die "Effective sshd config still allows password login; look for directives above the Include line in /etc/ssh/sshd_config"
  log "Password authentication disabled; root login is key-only"
}

install_postgresql() {
  step "Installing PostgreSQL 17"
  if dpkg -s postgresql-17 >/dev/null 2>&1; then
    log "postgresql-17 already installed"
  else
    install -d /usr/share/postgresql-common/pgdg
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
    printf 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt %s-pgdg main\n' \
      "$(lsb_release -cs)" > /etc/apt/sources.list.d/pgdg.list
    apt-get -o DPkg::Lock::Timeout=600 update -q
    apt_install postgresql-17
  fi
  systemctl enable --now postgresql

  # Password: reuse the one in an existing .env (re-run), otherwise generate.
  # The role password is always set to match, so DB and .env never disagree.
  DB_PASSWORD="$(db_password_from_env "${REPO_DIR}/.env")"
  if [[ -n "$DB_PASSWORD" ]]; then
    log "Reusing database password from existing .env"
  else
    DB_PASSWORD="$(generate_password)"
    log "Generated a new database password"
  fi
  local pw_sql
  pw_sql="$(printf '%s' "$DB_PASSWORD" | sed "s/'/''/g")"

  if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
    sudo -u postgres psql -qc "ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${pw_sql}'"
    log "Role ${DB_USER} exists — password synced"
  else
    sudo -u postgres psql -qc "CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${pw_sql}'"
    log "Role ${DB_USER} created"
  fi
  if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    log "Database ${DB_NAME} exists"
  else
    sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
    log "Database ${DB_NAME} created"
  fi
}

install_docker() {
  step "Installing Docker CE"
  if dpkg -s docker-ce >/dev/null 2>&1; then
    log "docker-ce already installed"
  else
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
      "$(dpkg --print-architecture)" "$(lsb_release -cs)" > /etc/apt/sources.list.d/docker.list
    apt-get -o DPkg::Lock::Timeout=600 update -q
    apt_install docker-ce docker-ce-cli containerd.io
  fi
  systemctl enable --now docker
  usermod -aG docker "$FORGE_USER"
  log "User ${FORGE_USER} is in the docker group"
}

run_as_forge() { sudo -u "$FORGE_USER" -H "$@"; }

verify_db_connection() {
  step "Verifying DATABASE_URL from .env can connect"
  local db_url
  db_url="$(grep -E '^DATABASE_URL=' "${REPO_DIR}/.env" | tail -n1 | cut -d= -f2- | tr -d "\"'")"
  [[ -n "$db_url" ]] || die ".env has no DATABASE_URL"
  run_as_forge psql "$db_url" -tAc 'SELECT 1' >/dev/null \
    || die "Cannot connect with DATABASE_URL from .env (app would silently fall back to SQLite)"
  log "PostgreSQL connection OK"
}

clone_repo() {
  step "Fetching repository into ${REPO_DIR}"
  if [[ -d "${REPO_DIR}/.git" ]]; then
    run_as_forge git -C "$REPO_DIR" fetch --quiet origin "$AGENTFX_BRANCH"
    run_as_forge git -C "$REPO_DIR" checkout --quiet "$AGENTFX_BRANCH"
    run_as_forge git -C "$REPO_DIR" pull --ff-only --quiet origin "$AGENTFX_BRANCH"
    log "Updated existing checkout (${AGENTFX_BRANCH})"
  else
    run_as_forge git clone --quiet --branch "$AGENTFX_BRANCH" "$AGENTFX_REPO" "$REPO_DIR"
    log "Cloned ${AGENTFX_REPO} (${AGENTFX_BRANCH})"
  fi
}

setup_venv() {
  step "Python virtualenv and dependencies"
  [[ -x "${REPO_DIR}/.venv/bin/python" ]] || run_as_forge python3 -m venv "${REPO_DIR}/.venv"
  run_as_forge "${REPO_DIR}/.venv/bin/pip" install -q --upgrade pip
  run_as_forge "${REPO_DIR}/.venv/bin/pip" install -q -r "${REPO_DIR}/requirements.txt"
  log "Dependencies installed"
}

prepare_ctrader_home() {
  step "Preparing ${CTRADER_HOME}"
  install -d -m 0755 -o "$FORGE_USER" -g "$FORGE_GROUP" "$CTRADER_HOME"
  install -d -m 0700 -o "$FORGE_USER" -g "$FORGE_GROUP" "${CTRADER_HOME}/ctrader_data"
  log "ctrader_data/ ready for cTID password files"
}

ctrader_console() {
  # Runs the Spotware CLI with the same mounts every cBot container uses.
  docker run --rm -v "${REPO_DIR}:/workspace" -v "${CTRADER_HOME}:/root" "$CTRADER_IMAGE" "$@"
}

build_algo() {
  # usage: build_algo <BotName>   compiles cBot/<BotName>.cs → cBot/<BotName>.algo
  local name="$1"
  local src="${REPO_DIR}/cBot/${name}.cs"
  local out="${REPO_DIR}/cBot/${name}.algo"
  local robots="${CTRADER_HOME}/cAlgo/Sources/Robots"
  local csproj="${robots}/${name}/${name}/${name}.csproj"
  [[ -f "$src" ]] || die "Missing cBot source: ${src}"
  if [[ -f "$out" && "$out" -nt "$src" ]]; then
    log "${name}.algo is newer than ${name}.cs — skipping"
    return 0
  fi
  [[ -f "$csproj" ]] || ctrader_console create cbot "$name"
  cp "$src" "${robots}/${name}/${name}/${name}.cs"
  ctrader_console build "/root/cAlgo/Sources/Robots/${name}/${name}/${name}.csproj"
  cp "${robots}/${name}.algo" "$out"
  log "Built ${name}.algo"
}

build_algos() {
  step "Building cBot .algo packages (this pulls ${CTRADER_IMAGE})"
  docker pull -q "$CTRADER_IMAGE"
  local name
  for name in AiAgentBot AsianRangeJudasSweepBot FlowRsiBot; do
    build_algo "$name"
  done
  # The console container runs as root, so hand everything back to forge.
  chown -R "${FORGE_USER}:${FORGE_GROUP}" "$CTRADER_HOME" "${REPO_DIR}/cBot"
}

install_systemd_unit() {
  step "Installing systemd service ${SERVICE_NAME}"
  render_systemd_unit > "/etc/systemd/system/${SERVICE_NAME}.service"
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME" >/dev/null
  systemctl restart "$SERVICE_NAME"
  local i
  for i in $(seq 1 30); do
    if curl -fs http://127.0.0.1:8000/api/watchdog/status >/dev/null 2>&1; then
      log "Service is answering on 127.0.0.1:8000 (after ${i}s)"
      return 0
    fi
    sleep 1
  done
  journalctl -u "$SERVICE_NAME" -n 50 --no-pager >&2 || true
  die "Service did not answer within 30s — see journal output above"
}

install_backup_cron() {
  step "Installing daily database backup"
  chmod +x "${REPO_DIR}/scripts/backup_postgres.sh"
  render_backup_cron > /etc/cron.d/agentfx-backup
  chmod 0644 /etc/cron.d/agentfx-backup
  log "Backups run daily at 03:00 into ${REPO_DIR}/backups"
}

configure_firewall() {
  step "Configuring ufw"
  local ports p allowed=""
  ports="$(sshd -T 2>/dev/null | awk '$1=="port"{print $2}')"
  [[ -n "$ports" ]] || die "Cannot determine sshd port from 'sshd -T'; refusing to enable ufw"
  for p in $ports; do
    ufw allow "${p}/tcp" >/dev/null
    allowed="${allowed}${p} "
  done
  ufw --force enable >/dev/null
  log "ufw enabled: only SSH (tcp ${allowed% }) allowed inbound"
}

print_summary() {
  local ip
  ip="$(curl -fs4 --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
  cat <<EOF

=============================================================================
 AgentFxTrading installed
=============================================================================
 User         : ${FORGE_USER}  (passwordless sudo, SSH key only)
 Project      : ${REPO_DIR}
 Service      : systemctl status ${SERVICE_NAME}
 Database     : postgresql://${DB_USER}:***@127.0.0.1:5432/${DB_NAME}  (full URL in .env)
 cBots built  : ${REPO_DIR}/cBot/{AiAgentBot,AsianRangeJudasSweepBot,FlowRsiBot}.algo
 cTrader home : ${CTRADER_HOME}  (mounted as /root inside cBot containers)

 Next steps
 1. BEFORE closing this session, prove the key works from ANOTHER terminal:
      ssh ${FORGE_USER}@${ip} 'sudo -n true && echo LOGIN_OK'
    Password and root-password logins are now disabled. If this fails, fix
    ${FORGE_HOME}/.ssh/authorized_keys from this session first.
 2. Open the dashboard through an SSH tunnel from your machine:
      ssh -L 8000:127.0.0.1:8000 ${FORGE_USER}@${ip}
    then browse http://127.0.0.1:8000
 3. Put your LLM API key in ${REPO_DIR}/.env
    (LLM_PROVIDER, DASHSCOPE_API_KEY, ...), then:  sudo systemctl restart ${SERVICE_NAME}
 4. Add your cTrader account and start bots from
    Dashboard → Docker Bot Management → Setup Instances.
 Re-running this installer is safe; it updates the checkout and rebuilds bots.
=============================================================================
EOF
}

# --- main ---
main() {
  cd /
  preflight
  read_ssh_key
  install_base_packages
  create_forge_user
  harden_sshd
  install_postgresql
  install_docker
  clone_repo
  setup_venv
  write_env
  verify_db_connection
  prepare_ctrader_home
  build_algos
  install_systemd_unit
  install_backup_cron
  configure_firewall
  print_summary
}

# Run main when executed (`bash install.sh`) or piped (`curl ... | bash`,
# where BASH_SOURCE is empty). Sourcing the file (tests) does not run main.
if [[ -z "${BASH_SOURCE[0]:-}" || "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
