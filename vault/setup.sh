#!/usr/bin/env bash
# ==============================================================================
# StreamVault — Production-Grade Automated Installer & Guided Setup Wizard
# ==============================================================================

set -euo pipefail

ORIGINAL_ARGS=("$@")
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_DIR="$ROOT_DIR"
if [[ ! -f "$VAULT_DIR/docker-compose.yaml" && -f "$ROOT_DIR/vault/docker-compose.yaml" ]]; then
    VAULT_DIR="$ROOT_DIR/vault"
fi

ENV_FILE="$VAULT_DIR/.env"
EXAMPLE_FILE="$VAULT_DIR/.env.example"
DEFAULT_DATA_ROOT="/opt/saas-data"

NON_INTERACTIVE=false
ADMIN_USERNAME="${STREAMVAULT_ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${STREAMVAULT_ADMIN_PASSWORD:-}"
DATA_ROOT_VALUE="${STREAMVAULT_DATA_ROOT:-$DEFAULT_DATA_ROOT}"
DOMAIN_VALUE="${STREAMVAULT_DOMAIN:-}"
TMDB_API_KEY_VALUE="${TMDB_API_KEY:-}"
TMDB_TOKEN_VALUE="${TMDB_TOKEN:-}"
OPENSUBTITLES_KEY_VALUE="${OPENSUBTITLES_API_KEY:-}"
CLOUDFLARE_TOKEN_VALUE="${TUNNEL_TOKEN:-}"
REPO_URL="${STREAMVAULT_REPO_URL:-https://github.com/your-org/StreamVault.git}"
INSTALL_DIR="${STREAMVAULT_INSTALL_DIR:-/opt/streamvault}"

# Colors
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_RED='\033[38;2;229;9;20m'
C_GREEN='\033[38;2;70;211;105m'
C_YELLOW='\033[38;2;245;197;24m'
C_BLUE='\033[38;2;59;130;246m'
C_CYAN='\033[38;2;56;189;248m'
C_MUTED='\033[38;2;136;136;153m'

banner() {
    cat <<EOF

${C_RED}${C_BOLD}  ███████╗████████╗██████╗ ███████╗ █████╗ ███╗   ███╗██╗   ██╗ █████╗ ██╗   ██╗██╗  ████████╗
  ██╔════╝╚══██╔══╝██╔══██╗██╔════╝██╔══██╗████╗ ████║██║   ██║██╔══██╗██║   ██║██║  ╚══██╔══╝
  ███████╗   ██║   ██████╔╝█████╗  ███████║██╔████╔██║██║   ██║███████║██║   ██║██║     ██║   
  ╚════██║   ██║   ██╔══██╗██╔══╝  ██╔══██║██║╚██╔╝██║╚██╗ ██╔╝██╔══██║██║   ██║██║     ██║   
  ███████║   ██║   ██║  ██║███████╗██║  ██║██║ ╚═╝ ██║ ╚████╔╝ ██║  ██║╚██████╔╝███████╗██║   
  ╚══════╝   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝  ╚═══╝  ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝   ${C_RESET}
  ${C_MUTED}Cinematic Self-Hosted Media Cloud & Stremio-Grade Streaming Engine${C_RESET}
──────────────────────────────────────────────────────────────────────────────────────────
EOF
}

step() {
    echo -e "\n${C_CYAN}${C_BOLD}[▶] $1${C_RESET}"
}

success() {
    echo -e "${C_GREEN}${C_BOLD}[✓] $1${C_RESET}"
}

warn() {
    echo -e "${C_YELLOW}${C_BOLD}[!] $1${C_RESET}"
}

error() {
    echo -e "${C_RED}${C_BOLD}[✗] $1${C_RESET}" >&2
}

usage() {
    cat <<'EOF'
Usage: setup.sh [options]
  --non-interactive          Install without interactive prompts (uses environment variables or defaults).
  --admin-user USER          Initial administrator username (default: admin).
  --admin-password PASSWORD  Initial administrator password (min 8 chars, 1 upper, 1 lower, 1 digit, 1 symbol).
  --data-root PATH           Persistent host storage directory (default: /opt/saas-data).
  --domain HOST              Public hostname, domain, or IP address.
  --tmdb-api-key KEY         Optional TMDB API key (v3 auth).
  --tmdb-token TOKEN         Optional TMDB bearer token (v4 auth).
  --opensubtitles-key KEY    Optional OpenSubtitles.com API key.
  --cloudflare-token TOKEN   Optional Cloudflare Tunnel token for zero-trust remote access.
  --repo-url URL             Git clone repository URL when running through curl.
  --install-dir PATH         Destination directory for curl installations (default: /opt/streamvault).
  -h, --help                 Show this help message.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --non-interactive) NON_INTERACTIVE=true; shift ;;
            --admin-user) ADMIN_USERNAME="$2"; shift 2 ;;
            --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
            --data-root) DATA_ROOT_VALUE="$2"; shift 2 ;;
            --domain) DOMAIN_VALUE="$2"; shift 2 ;;
            --tmdb-api-key) TMDB_API_KEY_VALUE="$2"; shift 2 ;;
            --tmdb-token) TMDB_TOKEN_VALUE="$2"; shift 2 ;;
            --opensubtitles-key) OPENSUBTITLES_KEY_VALUE="$2"; shift 2 ;;
            --cloudflare-token) CLOUDFLARE_TOKEN_VALUE="$2"; shift 2 ;;
            --repo-url) REPO_URL="$2"; shift 2 ;;
            --install-dir) INSTALL_DIR="$2"; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) error "Unknown option: $1"; usage; exit 2 ;;
        esac
    done
}

run_root() {
    if [[ "$EUID" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        error "Root privileges are required. Please run with sudo or as root."
        exit 1
    fi
}

run_docker() {
    if docker info >/dev/null 2>&1; then
        docker "$@"
    else
        run_root docker "$@"
    fi
}

ensure_bootstrap_packages() {
    local missing_pkgs=()
    for pkg in curl ca-certificates gnupg python3 git; do
        if ! command -v "$pkg" >/dev/null 2>&1; then
            missing_pkgs+=("$pkg")
        fi
    done

    if [[ ${#missing_pkgs[@]} -gt 0 ]]; then
        step "Installing required system packages (${missing_pkgs[*]})..."
        run_root apt-get update -qq
        run_root apt-get install -y -qq "${missing_pkgs[@]}"
        success "System packages installed successfully."
    fi
}

checkout_source_if_needed() {
    if [[ -f "$VAULT_DIR/docker-compose.yaml" ]]; then
        return
    fi
    if [[ -z "$REPO_URL" ]]; then
        error "StreamVault files not found. Pass --repo-url to clone the repository."
        exit 2
    fi
    step "Cloning StreamVault repository from $REPO_URL..."
    if [[ ! -f "$INSTALL_DIR/vault/docker-compose.yaml" ]]; then
        if [[ -e "$INSTALL_DIR" ]]; then
            error "Target directory exists but is not a StreamVault checkout: $INSTALL_DIR"
            exit 1
        fi
        run_root mkdir -p "$(dirname "$INSTALL_DIR")"
        run_root git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    fi
    exec "$INSTALL_DIR/vault/setup.sh" "${ORIGINAL_ARGS[@]}"
}

require_linux() {
    if [[ "$(uname -s)" != "Linux" ]]; then
        error "StreamVault requires a Linux operating system (Ubuntu 20.04+, Debian 11+, or compatible)."
        exit 1
    fi
}

get_lan_ip() {
    local ip
    ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7}' | head -n 1 || true)"
    if [[ -z "$ip" ]]; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    fi
    printf '%s' "${ip:-127.0.0.1}"
}

get_public_ip() {
    local pub_ip
    pub_ip="$(curl -fsS --max-time 2 https://api.ipify.org 2>/dev/null || curl -fsS --max-time 2 https://ifconfig.me 2>/dev/null || true)"
    printf '%s' "$pub_ip"
}

prompt_value() {
    local prompt="$1"
    local default_value="${2:-}"
    local result=""
    if [[ -n "$default_value" ]]; then
        read -r -p "$(echo -e "${C_BOLD}$prompt${C_RESET} [${C_CYAN}$default_value${C_RESET}]: ")" result
        result="${result:-$default_value}"
    else
        read -r -p "$(echo -e "${C_BOLD}$prompt${C_RESET}: ")" result
    fi
    printf '%s' "$result"
}

prompt_secret() {
    local prompt="$1"
    local result=""
    while [[ -z "$result" ]]; do
        read -r -s -p "$(echo -e "${C_BOLD}$prompt${C_RESET}: ")" result
        echo ""
    done
    printf '%s' "$result"
}

validate_password_strength() {
    local pass="$1"
    python3 - "$pass" <<'PY'
import sys, re
p = sys.argv[1]
if len(p) < 8:
    sys.exit(1)
if not re.search(r"[a-z]", p):
    sys.exit(2)
if not re.search(r"[A-Z]", p):
    sys.exit(3)
if not re.search(r"[0-9]", p):
    sys.exit(4)
if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", p):
    sys.exit(5)
sys.exit(0)
PY
}

generate_admin_password() {
    python3 - <<'PY'
import secrets
import string

lower = string.ascii_lowercase
upper = string.ascii_uppercase
digits = string.digits
symbols = "!@#$%^&*()"

while True:
    pwd = [
        secrets.choice(lower),
        secrets.choice(upper),
        secrets.choice(digits),
        secrets.choice(symbols)
    ]
    all_chars = lower + upper + digits + symbols
    pwd += [secrets.choice(all_chars) for _ in range(16)]
    secrets.SystemRandom().shuffle(pwd)
    password = "".join(pwd)
    print(password)
    break
PY
}

write_env_value() {
    local key="$1"
    local value="$2"
    python3 - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

if not path.exists():
    path.touch()

lines = path.read_text(encoding="utf-8").splitlines()
prefix = f"{key}="
updated = False
for index, line in enumerate(lines):
    if line.startswith(prefix):
        lines[index] = prefix + value
        updated = True
        break
if not updated:
    lines.append(prefix + value)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

install_docker() {
    if command -v docker >/dev/null 2>&1 && run_docker compose version >/dev/null 2>&1; then
        success "Docker Engine and Docker Compose are already installed."
        return
    fi

    step "Installing official Docker Engine and Docker Compose plugin..."
    run_root apt-get update -qq
    run_root apt-get install -y -qq ca-certificates curl gnupg lsb-release
    run_root install -m 0755 -d /etc/apt/keyrings

    local distro_id
    distro_id="$(. /etc/os-release && printf '%s' "$ID")"
    if [[ "$distro_id" != "ubuntu" && "$distro_id" != "debian" ]]; then
        distro_id="debian"
    fi

    if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
        curl -fsSL "https://download.docker.com/linux/${distro_id}/gpg" | run_root gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
        run_root chmod a+r /etc/apt/keyrings/docker.gpg
    fi

    local version_codename
    version_codename="$(. /etc/os-release && printf '%s' "$VERSION_CODENAME")"
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${distro_id} ${version_codename} stable" \
        | run_root tee /etc/apt/sources.list.d/docker.list >/dev/null

    run_root apt-get update -qq
    run_root apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    run_root systemctl enable --now docker

    local invoking_user="${SUDO_USER:-${USER:-root}}"
    if [[ "$invoking_user" != "root" ]] && ! groups "$invoking_user" 2>/dev/null | grep -q '\bdocker\b'; then
        run_root usermod -aG docker "$invoking_user" || true
        warn "Added user '$invoking_user' to 'docker' group."
    fi
    success "Docker installed and service started."
}

wait_for_health() {
    local attempt
    echo -ne "${C_MUTED}Waiting for StreamVault Core service to become ready...${C_RESET} "
    for attempt in $(seq 1 60); do
        if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 || curl -fsS http://127.0.0.1:80/api/health >/dev/null 2>&1; then
            echo -e "${C_GREEN}Ready!${C_RESET}"
            return
        fi
        echo -ne "${C_MUTED}.${C_RESET}"
        sleep 2
    done
    echo ""
    error "StreamVault did not respond in time. Recent container logs:"
    run_docker compose -f "$VAULT_DIR/docker-compose.yaml" logs --tail=50 core
    exit 1
}

main() {
    parse_args "$@"
    require_linux

    banner
    ensure_bootstrap_packages
    checkout_source_if_needed
    install_docker

    local lan_ip pub_ip
    lan_ip="$(get_lan_ip)"
    pub_ip="$(get_public_ip)"

    local password_confirm generated_password=false

    if [[ "$NON_INTERACTIVE" == false ]]; then
        step "1/5 — Network and Host Configuration"
        echo -e "${C_MUTED}Detected LAN IP:${C_RESET} ${C_BOLD}$lan_ip${C_RESET} | ${C_MUTED}Public IP:${C_RESET} ${C_BOLD}${pub_ip:-none}${C_RESET}"
        
        local default_host="${DOMAIN_VALUE:-$lan_ip}"
        DOMAIN_VALUE="$(prompt_value "Server Domain or Access IP" "$default_host")"

        step "2/5 — Storage Directory"
        echo -e "${C_MUTED}Persistent folder for database, media downloads, and user settings.${C_RESET}"
        DATA_ROOT_VALUE="$(prompt_value "Host data directory" "$DATA_ROOT_VALUE")"

        step "3/5 — Metadata & API Integrations (Optional)"
        echo -e "${C_MUTED}TMDB provides posters, cast, plot summaries, and trailers (https://www.themoviedb.org/settings/api)${C_RESET}"
        TMDB_API_KEY_VALUE="$(prompt_value "TMDB API Key (optional)" "$TMDB_API_KEY_VALUE")"
        if [[ -z "$TMDB_API_KEY_VALUE" ]]; then
            TMDB_TOKEN_VALUE="$(prompt_value "TMDB Read Access Token (v4, optional)" "$TMDB_TOKEN_VALUE")"
        fi
        OPENSUBTITLES_KEY_VALUE="$(prompt_value "OpenSubtitles API Key (optional)" "$OPENSUBTITLES_KEY_VALUE")"
        CLOUDFLARE_TOKEN_VALUE="$(prompt_value "Cloudflare Tunnel Token (optional, for zero-trust HTTPS)" "$CLOUDFLARE_TOKEN_VALUE")"

        step "4/5 — Administrator Account Setup"
        ADMIN_USERNAME="$(prompt_value "Admin Username" "$ADMIN_USERNAME")"
        
        while [[ -z "$ADMIN_PASSWORD" ]]; do
            echo -e "${C_MUTED}Leave password blank to auto-generate a secure random password.${C_RESET}"
            read -r -s -p "$(echo -e "${C_BOLD}Admin Password${C_RESET}: ")" ADMIN_PASSWORD
            echo ""
            if [[ -z "$ADMIN_PASSWORD" ]]; then
                ADMIN_PASSWORD="$(generate_admin_password)"
                generated_password=true
                break
            fi

            if ! validate_password_strength "$ADMIN_PASSWORD"; then
                warn "Password must be at least 8 characters and include uppercase, lowercase, numbers, and symbols (!@#\$%^&*...)."
                ADMIN_PASSWORD=""
                continue
            fi

            read -r -s -p "$(echo -e "${C_BOLD}Confirm Admin Password${C_RESET}: ")" password_confirm
            echo ""
            if [[ "$ADMIN_PASSWORD" != "$password_confirm" ]]; then
                warn "Passwords do not match. Please try again."
                ADMIN_PASSWORD=""
            fi
        done
    else
        DOMAIN_VALUE="${DOMAIN_VALUE:-$lan_ip}"
        if [[ -z "$ADMIN_PASSWORD" ]]; then
            ADMIN_PASSWORD="$(generate_admin_password)"
            generated_password=true
        fi
    fi

    step "5/5 — Applying Configurations & Provisioning Containers"

    # Ensure storage paths exist with proper permissions
    run_root mkdir -p "$DATA_ROOT_VALUE"/{downloads,config,db,media,users}
    local invoking_user="${SUDO_USER:-${USER:-root}}"
    run_root chown -R "$invoking_user":"$invoking_user" "$DATA_ROOT_VALUE" 2>/dev/null || true
    run_root chmod -R 775 "$DATA_ROOT_VALUE" 2>/dev/null || true

    if [[ ! -f "$ENV_FILE" ]]; then
        cp "$EXAMPLE_FILE" "$ENV_FILE"
    fi

    # Generate or retain secure SECRET_KEY
    local random_key existing_secret
    existing_secret="$(sed -n 's/^SECRET_KEY=//p' "$ENV_FILE" 2>/dev/null | head -n 1 || true)"
    if [[ ${#existing_secret} -ge 32 \
        && "$existing_secret" != "unsafe-default-secret-key-change-me-in-production" \
        && "$existing_secret" != "streamvault-dev-secret-change-me" \
        && "$existing_secret" != "change_this_secret_before_exposing_the_app" ]]; then
        random_key="$existing_secret"
    else
        random_key="$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")"
    fi

    write_env_value "SECRET_KEY" "$random_key"
    write_env_value "DOMAIN" "$DOMAIN_VALUE"
    write_env_value "ALLOWED_HOSTS" "localhost,127.0.0.1,0.0.0.0,$DOMAIN_VALUE,$lan_ip"
    write_env_value "HOST_DATA_ROOT" "$DATA_ROOT_VALUE"
    write_env_value "TMDB_API_KEY" "$TMDB_API_KEY_VALUE"
    write_env_value "TMDB_TOKEN" "$TMDB_TOKEN_VALUE"
    write_env_value "OPENSUBTITLES_API_KEY" "$OPENSUBTITLES_KEY_VALUE"
    write_env_value "TUNNEL_TOKEN" "$CLOUDFLARE_TOKEN_VALUE"
    write_env_value "TUNNEL_PROTOCOL" "http2"
    chmod 600 "$ENV_FILE"

    cd "$VAULT_DIR"

    step "Building and launching Docker containers..."
    run_docker compose up -d --build
    wait_for_health

    step "Seeding Administrator Account..."
    export STREAMVAULT_ADMIN_USERNAME="$ADMIN_USERNAME"
    export STREAMVAULT_ADMIN_PASSWORD="$ADMIN_PASSWORD"
    run_docker compose run --rm \
        -e STREAMVAULT_ADMIN_USERNAME \
        -e STREAMVAULT_ADMIN_PASSWORD \
        core python create_admin.py
    export -n STREAMVAULT_ADMIN_PASSWORD

    echo ""
    echo -e "${C_GREEN}${C_BOLD}╔════════════════════════════════════════════════════════════════════════════╗${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}║              StreamVault Installation Completed Successfully!              ║${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}╚════════════════════════════════════════════════════════════════════════════╝${C_RESET}"
    echo ""
    echo -e "  ${C_BOLD}Access URLs:${C_RESET}"
    echo -e "    • Local / LAN:   ${C_CYAN}http://${lan_ip}/${C_RESET}"
    if [[ -n "$DOMAIN_VALUE" && "$DOMAIN_VALUE" != "$lan_ip" && "$DOMAIN_VALUE" != "localhost" ]]; then
        echo -e "    • Host / Domain: ${C_CYAN}http://${DOMAIN_VALUE}/${C_RESET}"
    fi
    if [[ -n "$pub_ip" && "$pub_ip" != "$lan_ip" ]]; then
        echo -e "    • Public IP:     ${C_CYAN}http://${pub_ip}/${C_RESET}"
    fi
    echo ""
    echo -e "  ${C_BOLD}Admin Credentials:${C_RESET}"
    echo -e "    • Username:      ${C_YELLOW}${ADMIN_USERNAME}${C_RESET}"
    echo -e "    • Password:      ${C_YELLOW}${ADMIN_PASSWORD}${C_RESET}"
    if [[ "$generated_password" == true ]]; then
        echo -e "      ${C_RED}${C_BOLD}(Save this generated password now!)${C_RESET}"
    fi
    echo ""
    echo -e "  ${C_BOLD}Storage Path:${C_RESET}      ${C_MUTED}${DATA_ROOT_VALUE}${C_RESET}"
    echo -e "  ${C_BOLD}Compose Path:${C_RESET}      ${C_MUTED}${VAULT_DIR}${C_RESET}"
    echo ""
    echo -e "  ${C_BOLD}Useful Management Commands:${C_RESET}"
    echo -e "    • View live logs:    ${C_MUTED}cd ${VAULT_DIR} && docker compose logs -f${C_RESET}"
    echo -e "    • Restart stack:     ${C_MUTED}cd ${VAULT_DIR} && docker compose restart${C_RESET}"
    echo -e "    • Update & rebuild:  ${C_MUTED}cd ${VAULT_DIR} && git pull && ./setup.sh${C_RESET}"
    echo ""
}

main "$@"
