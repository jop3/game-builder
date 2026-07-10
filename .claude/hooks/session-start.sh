#!/bin/bash
# SessionStart-hook (Claude Code på webben): förladda ALLT en session behöver
# innan arbetet börjar, i stället för att varje session återupptäcker det:
#   1. pinnad Blender 4.2 + Godot (scripts/setup_toolchain.sh, idempotent)
#   2. lavapipe (mjukvaru-Vulkan) + ffmpeg + xvfb för rendering/film
#   3. texlib-cachen (pinnade CC0-fotoscans + HDRI:er)
#   4. PATH till /opt/toolchain/bin persistent för HELA sessionen via
#      $CLAUDE_ENV_FILE — "godot: command not found"-fällan försvinner.
# Containertillståndet cachas efter hooken, så fullpriset betalas bara första
# gången per miljö; därefter är alla steg snabba no-ops.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0   # lokal laptop: rör ingenting (se CLAUDE-runbooken där)
fi

cd "$CLAUDE_PROJECT_DIR"

# 1) pinnad toolchain (blender + godot + pip-deps) — skriptet är idempotent
bash scripts/setup_toolchain.sh

# 2) renderingsvägen: lavapipe + ffmpeg + xvfb (idempotent via dpkg-koll)
need=""
[ -f /usr/share/vulkan/icd.d/lvp_icd.json ] || need="$need mesa-vulkan-drivers"
command -v ffmpeg  >/dev/null 2>&1 || need="$need ffmpeg"
command -v xvfb-run >/dev/null 2>&1 || need="$need xvfb"
if [ -n "$need" ]; then
  apt-get update -qq >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $need >/dev/null
fi

# 3) pinnade externa ingredienser (sha256-verifierade, idempotent)
python3 -m assetpipe texlib fetch

# 4) persistenta miljövariabler för sessionen
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo 'export PATH=/opt/toolchain/bin:$PATH' >> "$CLAUDE_ENV_FILE"
fi

echo "session-start: toolchain + lavapipe/ffmpeg + texlib klara; PATH satt."
