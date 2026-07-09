#!/usr/bin/env bash
# regen_assets.sh — kör om arenans asset-set (pelare/bräde/bricka) genom
# pipelinen och leverera in i spelet. Två steg eftersom agent-vision är
# interaktiv (den drivande sessionen TITTAR på renders och skriver report.json
# per call_NNNN/ i exchange-katalogen — se assetpipe/vision/agent_client.py):
#
#   bash examples/othello/regen_assets.sh batch /tmp/exchange
#       … servera vision-callen tills batchen är klar …
#   bash examples/othello/regen_assets.sh deliver runs_arena_auto/<run-id>
#
# deliver kopierar BARA assets vars terminal-state är "validated" (gaten är
# arbetet — en best_effort-asset ska inte tyst hamna i spelet).
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo-roten

GAME_ASSETS=examples/othello/game/assets
declare -A MAP=(
	[greek_column_01]=column.glb
	[greek_board_01]=board.glb
	[greek_disc_01]=disc.glb
)

case "${1:-}" in
	batch)
		exchange="${2:?ange exchange-katalog (t.ex. /tmp/exchange)}"
		python3 -m assetpipe batch --requests examples/othello/arena_batch.json \
			--out runs_arena_auto/ --parallel 1 \
			--vision-client agent --vision-exchange "$exchange"
		;;
	deliver)
		rundir="${2:?ange run-katalog (runs_arena_auto/<id>)}"
		fail=0
		for asset in "${!MAP[@]}"; do
			hist="$rundir/$asset/history.jsonl"
			if [ ! -f "$hist" ]; then
				echo "SKIPPAD: $asset (ingen history i $rundir)" >&2; fail=1; continue
			fi
			if ! grep -q '"event": "terminal", "state": "validated"' "$hist"; then
				echo "EJ VALIDERAD: $asset — levereras inte (läs $rundir/$asset/diagnosis.md)" >&2
				fail=1; continue
			fi
			glb=$(ls "$rundir/$asset"/iter_*/"$asset".glb | sort | tail -1)
			cp "$glb" "$GAME_ASSETS/${MAP[$asset]}"
			echo "LEVERERAD: $asset -> $GAME_ASSETS/${MAP[$asset]}"
		done
		exit $fail
		;;
	*)
		echo "användning: $0 batch <exchange-dir> | deliver <run-dir>" >&2
		exit 2
		;;
esac
