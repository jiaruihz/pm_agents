#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/ops/backup_claude_code_wsl.sh [--output-dir DIR]

Back up Claude Code data visible from the current WSL environment.

Default output:
  runtime/backups/claude-code-wsl-YYYYMMDDTHHMMSS+ZZZZ/

The script checks the current WSL user, other /home users, /root if readable,
and Windows user homes mounted under /mnt/c. It is read-only against Claude
data. The archive may contain sensitive conversation history, project paths,
config, and credentials. Do not commit or upload the generated backup.
EOF
}

output_root="runtime/backups"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --output-dir requires a directory path" >&2
        exit 2
      fi
      output_root="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$HOME" ]]; then
  echo "ERROR: HOME does not exist: $HOME" >&2
  exit 1
fi

timestamp="$(date +%Y%m%dT%H%M%S%z)"
backup_dir="${output_root%/}/claude-code-wsl-${timestamp}"
archive="${backup_dir}/claude-code-wsl-data.tar.gz"
manifest="${backup_dir}/MANIFEST.txt"
listing="${backup_dir}/archive_listing.txt"
checksums="${backup_dir}/SHA256SUMS"

claude_rel_paths=(
  ".claude"
  ".claude.json"
  ".claude.json.backup"
  ".config/claude"
  ".config/Claude"
  ".cache/claude"
  ".cache/Claude"
  ".local/share/claude"
  ".local/share/Claude"
  "AppData/Roaming/Claude"
  "AppData/Local/Claude"
  "AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude"
)

base_dirs=()
add_base_dir() {
  local dir="$1"
  [[ -n "$dir" && -d "$dir" ]] || return 0
  local resolved
  resolved="$(cd "$dir" 2>/dev/null && pwd -P)" || return 0
  for existing in "${base_dirs[@]:-}"; do
    [[ "$existing" == "$resolved" ]] && return 0
  done
  base_dirs+=("$resolved")
}

add_base_dir "$HOME"

if [[ -d /home ]]; then
  while IFS= read -r -d '' dir; do
    add_base_dir "$dir"
  done < <(find /home -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)
fi

add_base_dir /root

if [[ -d /mnt/c/Users ]]; then
  while IFS= read -r -d '' dir; do
    add_base_dir "$dir"
  done < <(find /mnt/c/Users -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)
fi

include_paths=()
checked_paths=()
add_include_path() {
  local path="$1"
  [[ -e "$path" ]] || return 0
  local resolved
  resolved="$(readlink -f "$path" 2>/dev/null || printf '%s' "$path")"
  for existing in "${include_paths[@]:-}"; do
    [[ "$existing" == "$resolved" ]] && return 0
  done
  if [[ -r "$resolved" ]]; then
    include_paths+=("$resolved")
  else
    checked_paths+=("$resolved [exists but not readable]")
  fi
}

for base in "${base_dirs[@]}"; do
  for rel in "${claude_rel_paths[@]}"; do
    checked_paths+=("$base/$rel")
    add_include_path "$base/$rel"
  done
done

if [[ ${#include_paths[@]} -eq 0 ]]; then
  echo "ERROR: no readable Claude Code data paths found from this WSL environment" >&2
  echo "Checked base directories:" >&2
  printf '  %s\n' "${base_dirs[@]}" >&2
  echo "Checked relative paths:" >&2
  printf '  %s\n' "${claude_rel_paths[@]}" >&2
  echo >&2
  echo "If Claude Code was run as root, retry with:" >&2
  echo "  sudo bash scripts/ops/backup_claude_code_wsl.sh" >&2
  echo >&2
  echo "If Claude Code was run on Windows, check Windows paths such as:" >&2
  echo "  C:\\Users\\Administrator\\.claude" >&2
  echo "  C:\\Users\\Administrator\\AppData\\Roaming\\Claude" >&2
  exit 1
fi

mkdir -p "$backup_dir"

{
  echo "created_at=$(date -Is)"
  echo "host=$(hostname)"
  echo "user=$(id -un)"
  echo "home=$HOME"
  echo "pwd=$(pwd)"
  echo "archive=$archive"
  echo "base_dirs:"
  printf '%s\n' "${base_dirs[@]}"
  echo
  echo "included_paths:"
  printf '%s\n' "${include_paths[@]}"
  echo
  echo "path_sizes:"
  for path in "${include_paths[@]}"; do
    du -sh "$path" 2>/dev/null || true
  done
} > "$manifest"

tar --xattrs --acls -czf "$archive" "${include_paths[@]}"
sha256sum "$archive" > "$checksums"
tar -tzf "$archive" > "$listing"

echo "Backup complete"
echo "Archive: $archive"
echo "Manifest: $manifest"
echo "Listing: $listing"
echo "Checksum: $checksums"
du -h "$archive"
