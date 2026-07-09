#!/usr/bin/env bash
# check_sync.sh — verifiera att konsumenternas shader-kopior är i synk med
# kanoniska envkit/godot/. Godot-projekt kan inte ladda filer utanför sin
# projektrot, så konsumenterna MÅSTE ha kopior; det här är disciplinen som
# hindrar kopiorna från att tyst divergera. Tyst + exit 0 = i synk.
set -euo pipefail
cd "$(dirname "$0")/.."

# konsument-manifest: "kanonisk_fil konsument_fil" per rad
CONSUMERS="
envkit/godot/sea.gdshader  examples/othello/game/sea.gdshader
envkit/godot/sky.gdshader  examples/othello/game/sky.gdshader
envkit/godot/rock.gdshader examples/othello/game/rock.gdshader
"

fail=0
while read -r canon consumer; do
	[ -z "${canon:-}" ] && continue
	if ! diff -q "$canon" "$consumer" >/dev/null 2>&1; then
		echo "DIVERGED: $consumer != $canon" >&2
		fail=1
	fi
done <<< "$CONSUMERS"

if [ "$fail" -ne 0 ]; then
	echo "envkit: kopior ur synk — iterera i konsumenten, kopiera tillbaka till envkit/, committa båda." >&2
	exit 1
fi
