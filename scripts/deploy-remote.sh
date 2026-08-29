#!/usr/bin/env bash
set -euo pipefail

environment=${1:?usage: deploy-remote.sh <prod|staging> <commit-sha> <archive>}
release_sha=${2:?usage: deploy-remote.sh <prod|staging> <commit-sha> <archive>}
archive=${3:?usage: deploy-remote.sh <prod|staging> <commit-sha> <archive>}

if [[ ! "$release_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "release SHA must be a 40-character lowercase Git commit ID" >&2
  exit 64
fi

case "$environment" in
  prod)
    app_dir=/opt/ayla/prod
    data_dir=/opt/ayla/data/prod
    runtime_env=/opt/ayla/config/prod.env
    bot_port=8090
    site_api_port=3333
    ;;
  staging)
    app_dir=/opt/ayla/staging
    data_dir=/opt/ayla/data/staging
    runtime_env=/opt/ayla/config/staging.env
    bot_port=8091
    site_api_port=3334
    ;;
  *)
    echo "unsupported environment: $environment" >&2
    exit 64
    ;;
esac

runtime_env_dir=$(dirname -- "$runtime_env")
youtube_cookies_host_path="$runtime_env_dir/cookies.txt"

expected_archive="/tmp/ayla-$release_sha.tar.gz"
if test "$archive" != "$expected_archive"; then
  echo "unexpected archive path" >&2
  exit 64
fi

release_dir="$app_dir/releases/$release_sha"
current_link="$app_dir/current"
previous_release=
previous_sha=

require_file() {
  test -f "$1" || { echo "required file missing: $1" >&2; exit 1; }
}

require_directory() {
  test -d "$1" || { echo "required directory missing: $1" >&2; exit 1; }
}

compose() {
  local directory=$1
  local sha=$2
  shift 2
  AYLA_ENV="$environment" \
  RELEASE_SHA="$sha" \
  DATA_DIR="$data_dir" \
  RUNTIME_ENV_FILE="$runtime_env" \
  YOUTUBE_COOKIES_HOST_PATH="$youtube_cookies_host_path" \
  BOT_HOST_PORT="$bot_port" \
  SITE_API_HOST_PORT="$site_api_port" \
  docker compose --project-name "ayla-$environment" -f "$directory/deploy/compose.yml" "$@"
}

wait_for_health() {
  local container status
  for container in "ayla-$environment-bot" "ayla-$environment-site-api"; do
    for _ in $(seq 1 24); do
      status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
      if test "$status" = healthy; then
        break
      fi
      if test "$status" = unhealthy || test "$status" = exited || test "$status" = dead; then
        return 1
      fi
      sleep 5
    done
    test "${status:-}" = healthy || return 1
  done
}

restore_previous_release() {
  if test -n "$previous_release"; then
    if ! compose "$previous_release" "$previous_sha" up --detach --no-build --remove-orphans; then
      echo "failed to restore the previous release" >&2
      return 1
    fi
    if ! wait_for_health; then
      echo "previous release did not become healthy after rollback" >&2
      return 1
    fi
    return 0
  fi

  # First deploy: remove only this environment's attempted Compose project.
  compose "$release_dir" "$release_sha" down --remove-orphans || true
  return 0
}

handle_failed_activation() {
  local reason=$1
  echo "$reason; restoring the previous state" >&2
  restore_previous_release
}

prune_releases() {
  local current_release candidate
  local -a removable=()
  declare -A protected=()

  protected["$release_dir"]=1
  if test -n "$previous_release"; then
    protected["$previous_release"]=1
  fi
  if test -L "$current_link"; then
    current_release=$(readlink -f "$current_link")
    protected["$current_release"]=1
  fi

  for candidate in "$app_dir"/releases/*; do
    test -d "$candidate" || continue
    [[ "$candidate" == "$app_dir/releases/"* ]] || continue
    test -n "${protected[$candidate]+present}" && continue
    removable+=("$candidate")
  done

  ((${#removable[@]} > 3)) || return 0
  printf '%s\n' "${removable[@]}" | while IFS= read -r candidate; do
    printf '%s %s\n' "$(stat -c '%Y' "$candidate")" "$candidate"
  done | sort -nr | tail -n +4 | cut -d' ' -f2- | while IFS= read -r candidate; do
    [[ "$candidate" == "$app_dir/releases/"* ]] || continue
    rm -rf -- "$candidate"
  done
}

require_file "$archive"
require_directory "$data_dir"
require_file "$runtime_env"
require_file "$youtube_cookies_host_path"
require_directory /opt/ayla/scripts
docker info >/dev/null

if tar -tzf "$archive" | grep -Eq '(^/|(^|/)\.\.(/|$)|(^|/)\.env(\.|$))'; then
  echo "release archive contains an unsafe path or environment file" >&2
  exit 1
fi

if test -e "$current_link" || test -L "$current_link"; then
  if ! test -L "$current_link"; then
    echo "current must be a symbolic link" >&2
    exit 1
  fi
  previous_release=$(readlink -f "$current_link")
  case "$previous_release" in
    "$app_dir"/releases/*) ;;
    *)
      echo "current points outside the release directory" >&2
      exit 1
      ;;
  esac
  previous_sha=$(basename -- "$previous_release")
  if [[ ! "$previous_sha" =~ ^[0-9a-f]{40}$ ]] || ! test -d "$previous_release"; then
    echo "current does not point to a valid release" >&2
    exit 1
  fi
fi

mkdir -p "$app_dir/releases"
rm -rf -- "$release_dir"
mkdir -p "$release_dir"
tar -xzf "$archive" --no-same-owner --no-same-permissions -C "$release_dir"
require_file "$release_dir/deploy/compose.yml"
require_file "$release_dir/site-api/Dockerfile"

# Build before replacing any existing containers.
if ! compose "$release_dir" "$release_sha" build; then
  echo "new release build failed; active release was not changed" >&2
  exit 1
fi

if ! compose "$release_dir" "$release_sha" up --detach --no-build --remove-orphans; then
  handle_failed_activation "container activation failed" || true
  exit 1
fi

if ! wait_for_health; then
  handle_failed_activation "new release failed health checks" || true
  exit 1
fi

ln -sfn "$release_dir" "$current_link"
prune_releases
