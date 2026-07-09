# Iterations-retro — vad som gjorde 2026-07-09-kvällssessionen snabb

En jämförelse av arbetssätt över Othello-arenans git-historik (branch
`claude/cross-project-pipeline-review-59m1k8`), skriven för att nästa session
ska återanvända mönstren i stället för att återupptäcka dem. Detta är ett
process-dokument i samma anda som `PIPELINE_DOCTRINE.md`; evidensen är
commit-loggen plus sessionens egna mätpunkter.

## Evidensen ur historiken

**07-08 → 07-09 dagtid** (spelet + arenan byggdes): stabil, metodisk takt —
spelet, reglerna, boten, tre temavarv, spiralkameran, ljudmuxen, ö-arenan,
Gerstner-havet, audit-harnessen. Fundamentet. Men titta på *iterationsmediet*:

- **6 fullständiga inspelningar per dag** (varje `othello: … inspelning`-commit
  är en ~30–40 min-render under lavapipe). Nästan varje visuell ändring
  verifierades genom att spela in HELA filmen — inspelningen var både
  leverabel och mikroskop.
- **Samma ratt vreds två gånger blint**: `5579c6d` och `7e2323f` ("fler
  vitkammar") inom 25 minuter. Räknar man på den dåvarande våguppsättningen
  var skumtröskeln **onåbar**: foldsumman toppar på Σ Q·k·A ≈ 0.30 medan
  skum-smootstepen började vid 0.32. Inga vita gäss var möjliga, oavsett hur
  många varv man rattade. Ett tre-raders överslag hade ersatt två
  render-cykler.
- Genombrottsbuggen (kapitälet genom brädet, `31884a9`) upptäcktes i en
  levererad film och ledde till audit-harnessen (`332bcd2`) — rätt slutsats,
  och samma slutsats som denna retro generaliserar: **flytta upptäckten
  uppströms om den dyra artefakten.**

**07-09 kväll** (~2 h): look-dev-harnessen (`--still`) byggdes FÖRST, sedan
~12 stillbilds-iterationer på hav/klippa/himmel (à ~40 s), envkit-modulen,
bränningssystemet + havsljud, två röda tester lagade — och EN inspelning,
i slutet, när klivet var stort. Samma verktygskedja, samma repo, samma
GPU-lösa sandlåda.

Skillnaden var inte flit utan **varvtal**: ~50× fler tittar per timme när en
titt kostar 40 s i stället för 30–40 min.

## Mönstren att behålla (i prioritetsordning)

1. **Bygg det billiga ögat FÖRST.** Innan du itererar mot en dyr artefakt
   (film, batch, bake): bygg en deterministisk engångs-sond som visar samma
   sak på sekunder. Här: `--still=SEK` som snabbspolar den dt-summerade
   klockan utan att rendera per steg och fångar EN bildruta — bit-identisk
   med motsvarande filmruta tack vare determinismen. Kostnaden (30 rader)
   betalade sig på första iterationen.
2. **Räkna innan du rattar.** När en effekt "inte syns": kontrollera först
   aritmetiskt att den ÄR nåbar (tröskel vs teoretiskt max, amplitud vs
   objektradie, brusperiod vs objektstorlek). Två av sessionens största fynd
   var onåbarheter (skumtröskeln; `dscale`-frekvensen), inte fel parametrar.
3. **Diffa artefakter över versioner.** Om ett artefaktmönster är IDENTISKT
   före/efter en parameteränring är det inte det delsystem du redigerar
   (de "vita slöjorna" satt i stenens spekular + fill-ljuset, inte i
   stänk-puffarna som misstänktes).
4. **Beskär och zooma innan du diagnostiserar.** Tre olika fel ("glasyta")
   hade tre olika rotorsaker — jättefacetter (tessellation + ren dFdx-normal),
   spekularslöja (ReflectionProbe × SPECULAR>0), polens UV-kläm — och alla
   tre syntes först i en 4×-beskuren 720p-still. På helbild såg de ut som
   samma sak.
5. **Namnge rotorsaken innan fixen.** Varje ändring i sessionen mappar till
   en fysisk förklaring, inte "det såg bättre ut". Det är vad som gör att
   lärdomarna gick att skriva ner (envkit/README.md) och återanvända.
6. **Batcha oberoende ändringar, rendera en gång.** När rendern dominerar
   väggklockan: gruppera hav+klippa+himmel-ändringar per varv och attribuera
   med kunskap; bisektera bara vid överraskning.
7. **Spela in vid MILSTOLPAR, inte per ratt** (användarens direktiv,
   inskrivet i `examples/othello/CURRENT_WORK.md`). Inspelningen är
   leverabeln, inte mikroskopet.
8. **Promota det återanvändbara ut ur exemplet.** Shaders → `envkit/` med
   dokumenterat kontrakt + `check_sync.sh`. Nästa spel börjar på mål, inte
   på noll.
9. **Laga röda tester i förbifarten.** `env/column` saknades i
   recept-testets förväntningslista sedan förra sessionen — 2 minuter att
   laga, men hade annars urholkat "grönt betyder grönt".

## Ärlig fotnot

Kvällssessionen stod på dagsessionernas axlar: spelet, pipelinen,
determinism-disciplinen och audit-harnessen fanns redan. Jämförelsen handlar
om *iterationspraktik*, inte om vem som byggde mest. Men praktiken är
replikerbar oavsett modell: det billiga ögat, överslaget före ratten och
rotorsaksnamnet före fixen är process, inte magi.
