#!/usr/bin/env bash
# Runner preparation only. Never retry production, source fetch, or publication.
set -euo pipefail
MEDIA_TOOLS_POLICY_VERSION=2026102301
printf 'Media preparation policy: %s\n' "$MEDIA_TOOLS_POLICY_VERSION"

verify_media_tools() {
  local tool font_path
  for tool in ffmpeg ffprobe pdftoppm zstd fc-match fc-cache ldconfig; do
    command -v "$tool" >/dev/null || return 1
  done
  ffmpeg -hide_banner -filters 2>/dev/null | awk '$2 == "subtitles" {found=1} END {exit !found}' || return 1
  ffprobe -version >/dev/null || return 1
  pdftoppm -v >/dev/null 2>&1 || return 1
  zstd --version >/dev/null || return 1
  ldconfig -p 2>/dev/null | awk '
    $1 == "libGL.so.1" {gl=1}
    $1 == "libglib-2.0.so.0" {glib=1}
    END {exit !(gl && glib)}' || return 1
  font_path=$(fc-match -f '%{file}' 'Noto Sans CJK SC:lang=zh-cn') || return 1
  test -n "$font_path" && test -f "$font_path" || return 1
  case "$font_path" in
    *Noto*CJK*) printf 'Verified Chinese font: %s\n' "$font_path" ;;
    *) return 1 ;;
  esac
}

# A complete existing toolchain needs verification, not another network update.
if verify_media_tools; then
  printf 'Verified existing media tools; no package network request needed.\n'
  exit 0
fi

# Install only from Ubuntu's signed sources. Refreshing unrelated Microsoft,
# browser or third-party repositories is not needed for these packages. Actual
# jobs timed out at this stage; keep unrelated feeds out of this bounded step.
# Keep the runner's global sources untouched and preserve Signed-By/Suites.
task_apt_sources=$(mktemp -d /tmp/linyuan-apt-sources.XXXXXX)
chmod 755 "$task_apt_sources"
test -f /etc/apt/sources.list.d/ubuntu.sources
cp /etc/apt/sources.list.d/ubuntu.sources "$task_apt_sources/ubuntu.sources"
grep -q '^Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' "$task_apt_sources/ubuntu.sources"
sed -i -E \
  -e 's|^URIs:.*apt-mirrors-security.txt.*|URIs: https://security.ubuntu.com/ubuntu|' \
  -e 's|^URIs:.*apt-mirrors.txt.*|URIs: https://archive.ubuntu.com/ubuntu|' \
  -e 's|https?://azure\.archive\.ubuntu\.com/ubuntu|https://archive.ubuntu.com/ubuntu|g' \
  -e 's|http://archive\.ubuntu\.com/ubuntu|https://archive.ubuntu.com/ubuntu|g' \
  -e 's|http://security\.ubuntu\.com/ubuntu|https://security.ubuntu.com/ubuntu|g' \
  "$task_apt_sources/ubuntu.sources"
apt_options=(-o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30
  -o Acquire::ForceIPv4=true -o APT::Update::Error-Mode=any
  -o Dir::Etc::sourcelist=/dev/null -o "Dir::Etc::sourceparts=$task_apt_sources"
  -o APT::Get::List-Cleanup=0)
if ! sudo timeout --signal=TERM --kill-after=15s 180s apt-get "${apt_options[@]}" update -q; then
  printf 'Primary Ubuntu index refresh failed; trying its secondary official archive once.\n' >&2
  sed -i 's|https://archive.ubuntu.com/ubuntu|https://us.archive.ubuntu.com/ubuntu|g' "$task_apt_sources/ubuntu.sources"
  sudo timeout --signal=TERM --kill-after=15s 180s apt-get "${apt_options[@]}" update -q
fi
sudo timeout --signal=TERM --kill-after=15s 420s apt-get "${apt_options[@]}" install -y -qq --no-install-recommends \
  ffmpeg fonts-noto-cjk poppler-utils libgl1 libglib2.0-0 zstd
fc-cache -f
verify_media_tools
ffprobe -version
