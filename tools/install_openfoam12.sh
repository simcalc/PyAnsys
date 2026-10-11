#!/usr/bin/env bash
# Exact official package on a disposable public Ubuntu 24.04 worker only.
set -euo pipefail
python3 -B tools/public_runner_guard.py
if [[ "${RUNNER_OS:-}" != Linux || "${GITHUB_ACTIONS:-}" != true ]]; then
  printf 'Require the verified public GitHub Linux worker.\n' >&2
  exit 2
fi
package_path="$RUNNER_TEMP/openfoam12_20250206_amd64.deb"
if [[ -e "$package_path" || -L "$package_path" ]]; then
  printf 'Use a fresh isolated official package destination.\n' >&2
  exit 2
fi
curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 2 --max-time 180 \
  'https://downloads.sourceforge.net/project/foam/foam/ubuntu/dists/noble/main/binary-amd64/openfoam12_20250206_amd64.deb' \
  --output "$package_path"
printf '%s  %s\n' 'b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1' "$package_path" | sha256sum --check --strict
sudo apt-get update -q
sudo apt-get install --no-install-recommends -y "$package_path"
test "$(dpkg-query --show --showformat='${Version}' openfoam12)" = '20250206'
