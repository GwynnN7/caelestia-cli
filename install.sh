#!/bin/sh

set -eu

repo_url=${CAELESTIA_REPO_URL:-https://github.com/GwynnN7/caelestia-cli.git}
repo_ref=${CAELESTIA_REPO_REF:-main}
root_dir=${CAELESTIA_ROOT_DIR:-$HOME/.cache/caelestia}
repo_dir=${CAELESTIA_REPO_DIR:-$root_dir/caelestia-cli}

printf '%s\n' 'Upgrading system and required packages...' >&2

sudo pacman -Syu --needed --noconfirm git base-devel < /dev/tty

mkdir -p "$(dirname "$repo_dir")"

git clone --depth 1 --branch "$repo_ref" "$repo_url" "$repo_dir" >/dev/null
cd "$repo_dir"
makepkg -si --noconfirm < /dev/tty
rm -rf "$repo_dir"
cd ~ && caelestia install < /dev/tty