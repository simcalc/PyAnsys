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
# SourceForge may select a slow mirror. The owner's public release contains
# the exact unchanged official .deb, independently pinned to the same SHA256.
# A transport fallback never changes the package or bypasses its checksum.
if ! curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 20 --retry 0 --max-time 60 \
  'https://downloads.sourceforge.net/project/foam/foam/ubuntu/dists/noble/main/binary-amd64/openfoam12_20250206_amd64.deb' \
  --output "$package_path"; then
  printf 'Official mirror transport incomplete; download the same checksum-pinned package from the authorized public release.\n'
  curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 20 --retry 1 --max-time 180 \
    'https://github.com/simcalc/OpenFoam/releases/download/openfoam12-20250206/openfoam12_20250206_amd64.deb' \
    --output "$package_path"
fi
test "$(stat --format='%s' "$package_path")" = 112877506
printf '%s  %s\n' 'b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1' "$package_path" | sha256sum --check --strict
sudo apt-get update -q
sudo apt-get install --no-install-recommends -y "$package_path"
test "$(dpkg-query --show --showformat='${Version}' openfoam12)" = '20250206'
