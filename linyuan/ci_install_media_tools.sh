#!/usr/bin/env bash
# Runner preparation only. Never retry production, source fetch, or publication.
set -euo pipefail

# GitHub Ubuntu runners may use mirror+file, not only ubuntu.sources.
# Replace only the official Azure mirror with the official Ubuntu archive;
# apt signature verification and the requested packages remain unchanged.
for source_config in /etc/apt/apt-mirrors.txt /etc/apt/sources.list.d/ubuntu.sources; do
  if [ -f "$source_config" ]; then
    sudo sed -i 's|https\?://azure\.archive\.ubuntu\.com/ubuntu|https://archive.ubuntu.com/ubuntu|g' "$source_config"
  fi
done
apt_options=(-o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
sudo timeout --signal=TERM --kill-after=15s 600s apt-get "${apt_options[@]}" update -qq
sudo timeout --signal=TERM --kill-after=15s 600s apt-get "${apt_options[@]}" install -y -qq --no-install-recommends \
  ffmpeg fonts-noto-cjk poppler-utils libgl1 libglib2.0-0 zstd
fc-cache -f
ffprobe -version
font_path=$(fc-match -f '%{file}' 'Noto Sans CJK SC:lang=zh-cn')
test -n "$font_path"
test -f "$font_path"
case "$font_path" in
  *Noto*CJK*) printf 'Verified Chinese font: %s\n' "$font_path" ;;
  *) printf 'Required Noto CJK font is not available\n' >&2; exit 1 ;;
esac
