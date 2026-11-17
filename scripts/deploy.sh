#!/usr/bin/env bash
# Local-only deployment. Never sources .env, prints credentials, or removes volumes.
set -Eeuo pipefail
umask 077
export DOCKER_HOST=unix:///var/run/docker.sock
unset DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH DOCKER_CONFIG
export DEBIAN_FRONTEND=noninteractive
stage=preflight
deployment_error() {
    local status=$1 line=$2 source=${BASH_SOURCE[1]} frame reason='command failed'
    # ERR is inherited by functions. Report run's caller, not its timeout line,
    # even when run is reached through another function. Never print arguments.
    for ((frame=1; frame<${#FUNCNAME[@]}; frame++)); do
        if [[ ${FUNCNAME[$frame]} == run ]]; then
            line=${BASH_LINENO[$frame]}
            source=${BASH_SOURCE[$((frame+1))]:-${BASH_SOURCE[1]}}
            break
        fi
    done
    case "$status" in
        124) reason='time limit reached or command returned 124' ;;
        125) reason='timeout wrapper or command failed' ;;
        126) reason='command could not be invoked or returned 126' ;;
        127) reason='command not found or returned 127' ;;
        137) reason='command was killed or returned 137' ;;
    esac
    printf 'Deployment failed at %s (%s:%s, exit %s): %s; data/volumes preserved. See .local/deploy.log.\n' \
        "$stage" "${source##*/}" "$line" "$status" "$reason" >&2
    exit "$status"
}
trap 'deployment_error "$?" "$LINENO"' ERR
fail() { printf '%s\n' "$*" >&2; exit 1; }
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
demo=false
port_args=()
project_hint=video-service
min_free_mb=8192
while (($#)); do
    case "$1" in
        --demo) demo=true; shift ;;
        --port) (($# >= 2)) || fail 'Missing option value'; port_args+=("$1" "$2"); shift 2 ;;
        --project) (($# >= 2)) || fail 'Missing option value'; project_hint=$2; port_args+=("$1" "$2"); shift 2 ;;
        --min-free-mb) (($# >= 2)) || fail 'Missing space limit'; min_free_mb=$2; shift 2 ;;
        --help) printf '%s\n' 'sudo bash scripts/deploy.sh [--demo] [--port 8080] [--project video-service] [--min-free-mb 8192]'; exit 0 ;;
        *) fail 'Unknown option; use --help' ;;
    esac
done
[[ $EUID == 0 ]] || fail 'Run with sudo bash scripts/deploy.sh --demo'
# shellcheck source=/dev/null
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(dpkg --print-architecture) == amd64 ]] || fail 'Ubuntu 24.04 LTS amd64 required'
[[ $min_free_mb =~ ^[0-9]+$ && $min_free_mb -ge 512 ]] || fail 'Invalid free-space limit (minimum 512 MiB)'
check_space() { local free; free=$(df -Pm "$1" | awk 'NR==2 {print $4}'); ((free >= min_free_mb)) || fail "Insufficient disk space: need $min_free_mb MiB at $1"; }
check_space "$root"
check_space /var/lib
[[ ! -L $root/.local ]] || fail 'Refusing symlink .local'
mkdir -p "$root/.local"
chmod 700 "$root/.local"
[[ ! -L $root/.local/deploy.log && ! -L $root/.local/deploy.lock ]] || fail 'Refusing symlink log/lock'
exec 9>"$root/.local/deploy.lock"
flock -n 9 || fail 'Another deployment is running'
touch "$root/.local/deploy.log"
chmod 600 "$root/.local/deploy.log"
log=$root/.local/deploy.log
run() { timeout --kill-after=10 --foreground "$1" "${@:2}" >>"$log" 2>&1; }
# Check connectivity before apt; no preinstalled Python/Node/PostgreSQL needed.
timeout --kill-after=10 15 bash -c 'exec 3<>/dev/tcp/download.docker.com/443' || fail 'Internet/DNS unavailable'
stage=docker
if ! command -v docker >/dev/null || ! timeout --kill-after=10 15 docker compose version >/dev/null 2>&1 ||
   { ! command -v dockerd >/dev/null && ! timeout --kill-after=10 30 docker info >/dev/null 2>&1; }; then
    [[ ! -L $root/.local/docker.asc && ! -L $root/.local/docker.sources ]] || fail 'Refusing symlink apt bootstrap files'
    for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
        if [[ $(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true) == 'install ok installed' ]]; then
            fail "Conflicting package $package; resolve manually. Nothing was removed."
        fi
    done
    run 300 apt-get update
    run 300 apt-get --no-remove install -y ca-certificates curl
    install -d -m 0755 /etc/apt/keyrings
    run 60 curl --fail --silent --show-error --location --max-time 30 https://download.docker.com/linux/ubuntu/gpg -o "$root/.local/docker.asc"
    cat > "$root/.local/docker.sources" <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    [[ ! -e /etc/apt/sources.list.d/docker.list ]] || fail 'Existing docker.list; review apt configuration manually'
    for item in docker.asc docker.sources; do
        target=/etc/apt/keyrings/docker.asc
        [[ $item == docker.asc ]] || target=/etc/apt/sources.list.d/docker.sources
        if [[ -e $target ]]; then
            cmp -s "$root/.local/$item" "$target" || fail 'Existing Docker apt configuration differs; not overwritten'
        else
            install -m 0644 "$root/.local/$item" "$target"
        fi
    done
    run 300 apt-get update
    run 900 apt-get --no-remove install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    run 60 systemctl enable --now docker
fi
if ! timeout --kill-after=10 30 docker info >/dev/null 2>&1; then
    run 60 systemctl start docker
fi
timeout --kill-after=10 30 docker info >/dev/null 2>&1 || fail 'Docker daemon unavailable'
check_space "$(timeout --kill-after=10 30 docker info --format '{{.DockerRootDir}}')"
stage=configuration
if [[ ! -e $root/.env ]]; then
    existing_volumes=$(timeout --kill-after=10 30 docker volume ls -q --filter "label=com.docker.compose.project=$project_hint")
    [[ -z $existing_volumes ]] || fail 'Existing project volumes but .env is missing; restore original secrets from backup'
fi
python_image=python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
helper=(docker run --rm -i --network host --user 0:0 -v "$root:/workspace" "$python_image" python /workspace/scripts/deployment_config.py)
run 300 docker image inspect "$python_image" || run 300 docker pull "$python_image"
run 60 "${helper[@]}" init --root /workspace "${port_args[@]}"
dc=(env -i PATH="$PATH" docker --host unix:///var/run/docker.sock compose --project-directory "$root" --env-file "$root/.env" -f "$root/compose.yaml")
# Compose is the sole dotenv parser. Secret-bearing JSON stays inside this pipe.
settings=$(timeout --kill-after=10 60 "${dc[@]}" --profile demo config --format json 2>>"$log" | timeout --kill-after=10 60 "${helper[@]}" configure --root /workspace "${port_args[@]}" 2>>"$log")
read -r project port <<< "$settings"
[[ $project =~ ^[a-z][a-z0-9_-]*$ && $port =~ ^[0-9]+$ ]] || fail 'Invalid parsed configuration'
# A published port is reusable only when it belongs to this project's existing web.
web_id=$(timeout --kill-after=10 30 "${dc[@]}" ps -aq web)
if [[ -n $web_id ]] && [[ $(timeout --kill-after=10 30 docker inspect --format '{{.State.Running}}' "$web_id") == true ]]; then
    binding=$(timeout --kill-after=10 30 docker inspect --format '{{range (index .NetworkSettings.Ports "8080/tcp")}}{{.HostIp}}:{{.HostPort}}{{end}}' "$web_id")
    [[ $binding == "127.0.0.1:$port" ]] || fail 'Existing web has an unexpected binding'
else
    run 30 "${helper[@]}" port --root /workspace --port "$port"
fi
run 60 "${dc[@]}" config --quiet
stage=build
printf '%s\n' 'Building application images (details in .local/deploy.log)...'
run 1800 "${dc[@]}" build api web
stage=dependencies
run 180 "${dc[@]}" up -d --wait --wait-timeout 150 db storage
stage=migrations
run 180 "${dc[@]}" run --rm --no-deps migrate
stage=storage-init
run 120 "${dc[@]}" run --rm --no-deps storage-init
stage=services
run 240 "${dc[@]}" up -d --no-deps --wait --wait-timeout 180 api worker
run 180 "${dc[@]}" up -d --no-deps --wait --wait-timeout 120 web
run 30 "${dc[@]}" exec -T worker python -m app.worker.health
if $demo; then
    stage=demo
    printf '%s\n' 'Preparing synthetic demonstration data...'
    run 900 "${dc[@]}" --profile demo run --rm --no-deps seed
fi
stage=http-smoke
run 120 docker run --rm --network host -v "$root/scripts:/scripts:ro" "$python_image" python /scripts/check_http.py --base-url "http://localhost:$port"
printf 'Ready: http://localhost:%s\nSwagger: http://localhost:%s/docs\n' "$port" "$port"
if $demo; then printf 'Demo credentials (not printed): %s/.local/demo/credentials.json\n' "$root"; fi
