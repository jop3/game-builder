#!/usr/bin/env bash
# verify_scene.sh — deterministisk geometri-audit av arenascenen.
#
# Kör spelscenen headless i --audit-läge: den bygger klippa, pelare, bräde och
# brickor och verifierar de rumsliga invarianterna UTAN att rendera eller spela
# partiet — inga gissningar, ingen ögonkontroll. Fångar interpenetration
# (pelarens kapitäl upp genom brädet), brickor på fel höjd och bräde under havet.
#
# Detta är felinjektions-/verifiera-verifieraren-lagret för scenen: en tyst
# geometribugg (som pelaren-genom-brädet) ska slå RÖTT här, inte upptäckas i en
# levererad film. Kör före varje inspelning.
#
#   bash examples/othello/verify_scene.sh
#
# Kräver godot på PATH (t.ex. /opt/toolchain/bin).
set -euo pipefail

GAME_DIR="$(cd "$(dirname "$0")/game" && pwd)"

out="$(godot --headless --path "$GAME_DIR" res://othello.tscn -- --audit 2>&1 || true)"
echo "$out" | grep -E "SELFTEST_|AUDIT_(PASS|FAIL)" || {
	echo "SCENE AUDIT: no AUDIT_ line produced — scen kraschade? Full utdata:"
	echo "$out" | tail -20
	exit 2
}
# verifiera-verifieraren måste passera (annars är kontrollen tyst bruten)
if ! echo "$out" | grep -q "SELFTEST_PASS"; then
	echo "SCENE AUDIT: self-test FAILED — kontrollen är tyst bruten."
	exit 3
fi
if echo "$out" | grep -q "AUDIT_FAIL"; then
	echo "SCENE AUDIT FAILED — fixa geometrin innan inspelning."
	exit 1
fi
echo "SCENE AUDIT OK"
