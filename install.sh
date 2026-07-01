#!/bin/sh

set -eu

repo_url=${CAELESTIA_REPO_URL:-https://raw.githubusercontent.com/GwynnN7/caelestia-cli}
repo_ref=${CAELESTIA_REPO_REF:-main}
repo_dir=${CAELESTIA_REPO_DIR:-$HOME/.cache/caelestia/caelestia-cli}

printf '%s\n' 'Upgrading system and required packages...' >&2

sudo pacman -Syu --needed --noconfirm git base-devel < /dev/tty

mkdir -p "$repo_dir"

cd "$repo_dir"
curl -OL ${repo_url}/${repo_ref}/PKGBUILD
makepkg -si --noconfirm < /dev/tty

rm -rf "$repo_dir"
cd ~ && caelestia install < /dev/tty