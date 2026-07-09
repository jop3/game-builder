# envkit — återanvändbara deterministiska miljö-shaders (Godot 4.x)

Spelagnostiska miljöbitar som vuxit fram i `examples/othello` (Grekland-arenan)
och är avsedda att återanvändas i nästa spel i stället för att skrivas om:
ett helt utomhus-"set" — hav, himmel och klippor — som procedurella shaders,
utan en enda textur- eller ljud-asset.

```
godot/
  sea.gdshader   Gerstner-hav: trochoidala vågor (analytisk normal), Fresnel,
                 vindsträckta vita gäss (Jacobian-fold + anisotropt brus),
                 grunt-vatten-band + pulserande bränningskrage runt en ö.
  sky.gdshader   Dagsljushimmel: fbm-cumulus med mörka baser, molnbank vid
                 horisonten, blixt-uniform (`flash`), mörk sjödis under horisonten.
  rock.gdshader  Kustklippa: ridged-fbm-förskjutning i tre lager, sedimentära
                 band i världs-Y, AO i sprickor, lav på flata toppar, smalt
                 blött band vid vattenlinjen.
```

## Determinism-kontraktet (det viktiga)

Varje animerat värde läser uniformen **`t`** — spelets dt-summerade klocka
(`_elapsed`) — **aldrig `TIME`**. Under mjukvaru-Vulkan (lavapipe) renderas en
bildruta på sekunder; väggklocketid skulle desynka bild mot simulering, medan
en dt-summerad `t` ger bit-stabila, FPS-låsta inspelningar. Konsumenten sätter
uniformen varje stegad bildruta:

```gdscript
_sea_mat.set_shader_parameter("t", _elapsed)
_sky_mat.set_shader_parameter("t", _elapsed)
```

`rock.gdshader` är helt statisk (ingen `t`).

## Integrationsanteckningar (dyrköpta)

- **`TAU`/`PI` är inbyggda** i Godots shaderspråk — omdefiniering ger
  "Redefinition"-kompilfel. Shadern använder eget `TWO_PI`.
- **`rock`: per-instans `dscale`** (instance uniform) skalar förskjutningen och
  brusfrekvensen mot objektets egen radie. Utan den får små block antingen
  glasskärvs-spikar (fast amplitud) eller blir släta potatisar (fast frekvens):
  ```gdscript
  chunk.set_instance_shader_parameter("dscale", chunk_radius / main_radius)
  ```
- **`rock`: tessellera tätt** (sfär ~160×80 för huvudmassan) — förskjutningen
  facetterar annars, och fragmentnormalen (dFdx-blandning) kan inte rädda
  jättetrianglar. Rotera instanser runt alla axlar så sfärpolens UV-kläm
  ("blomman") inte tittar mot kameran; shadern plattar också av förskjutningen
  vid lokala polen (topphylla utan stjärnmönster).
- **`sea`: skummet kräver att foldsumman når tröskeln.** Foldens maxvärde är
  `Σ Q·k·A` över vågorna — ändras våguppsättningen, kontrollera att
  `smoothstep`-trösklarna i skumtermerna fortfarande kan nås, annars
  försvinner alla vita gäss tyst.
- **`sea`: `island_r`-uniformen** styr både grunt-vatten-bandet och
  bränningskragen; sätt den från scenens ö-radie.
- **Torr sten: `SPECULAR ≈ 0`.** Med en ReflectionProbe i scenen lägger även
  0.18 i spekulärt en vit himmelsslöja över hela klippan.
- **Reflektionssond/himmelsradians behöver uppvärmningsbildrutor**: rendera
  ~30 bildrutor innan första fångsten (se `--still`-läget i
  `examples/othello/game/othello.gd`) — annars är omgivningsljuset inte
  konvergerat och klippan renderas nattsvart.

## Look-dev-harnessen (kopiera mönstret)

`examples/othello/game/othello.gd` har ett `--still=SEKUNDER --out=fil.png`-läge:
det spolar den deterministiska klockan till önskad tidpunkt **utan** att rendera
varje steg, renderar EN bildruta och avslutar. En shader-iteration kostar
~40 s i stället för en hel inspelning (~30 min). Bygger man ett nytt spel på
de här shadersarna: implementera samma läge först, iterera sedan.

## Synk-disciplin

Konsumenterna (spelen) har egna kopior under sina projektrötter — Godot kan
inte ladda `res://` utanför projektet. Kanoniska versionen ligger HÄR;
`check_sync.sh` failar om någon konsumentkopia divergerat:

```bash
bash envkit/check_sync.sh    # tyst + exit 0 = i synk
```

Arbetsflödet vid ändring: iterera i konsumentprojektet (look-dev-harnessen
finns där), kopiera tillbaka hit, kör `check_sync.sh`, committa båda.
