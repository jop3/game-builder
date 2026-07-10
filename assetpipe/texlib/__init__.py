"""texlib -- pinnat, licensrent bibliotek av externa texturassets (CC0).

Pipelinen är procedurell-först, men vissa material (marmor, travertin, trä,
klippa) blir markant bättre av RIKTIGA PBR-fotoscans, och render-harnessens
L1-rigg blir ett bättre materialdomarljus med en riktig studio-HDRI. texlib
gör det reproducerbart och licenssäkert:

- ``manifest.json`` pinnar varje asset: källa, URL, **sha256**, licens (endast
  CC0 accepteras här), taggar. Ingen asset committas i git -- de hämtas till
  en cache (``texlib_cache/`` i repo-roten, gitignorad; override med
  ``ASSETPIPE_TEXLIB_CACHE``) och verifieras mot sin pin vid varje fetch.
  Ändrad fil hos källan => högljutt sha-fel, aldrig tyst drift.
- ``python -m assetpipe texlib fetch`` hämtar allt (idempotent);
  ``texlib list`` visar status.
- ``resolve(id)`` ger kartvägar för recept (PBR: Color/NormalGL/Roughness/
  Displacement/... upptäcks på ambientCG:s suffixkonvention) eller
  HDR-filvägen (hdri).

Nätfakta för molnsandlådan (verifierade 2026-07-10): ambientcg.com och
api/dl.polyhaven.org är NÅBARA genom egress-proxyn (GitHub är det inte).
Offline (t.ex. lokal laptop utan nät): kör fetch en gång när nät finns --
cachen är ren data och kan kopieras mellan maskiner.

Determinism: samma pin => samma bytes => samma bake. Ett recept som använder
texlib MÅSTE tåla att cachen saknas med ett tydligt fel som säger
"kör python -m assetpipe texlib fetch" (TexlibMissing gör det åt dig).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

_USER_AGENT = "game-builder-assetpipe-texlib"
_MANIFEST_PATH = Path(__file__).parent / "manifest.json"
_OK_MARKER = ".texlib_ok"

# ambientCG:s suffixkonvention -> kanoniska kartnamn som recepten använder.
PBR_MAP_SUFFIXES = {
    "color": "_Color.jpg",
    "normal_gl": "_NormalGL.jpg",
    "roughness": "_Roughness.jpg",
    "displacement": "_Displacement.jpg",
    "ao": "_AmbientOcclusion.jpg",
    "metalness": "_Metalness.jpg",
    "emission": "_Emission.jpg",
    "opacity": "_Opacity.jpg",
}

ALLOWED_LICENSES = {"CC0-1.0"}


class TexlibError(RuntimeError):
    pass


class TexlibMissing(TexlibError):
    """Asseten finns i manifestet men inte i cachen -- kör fetch."""

    def __init__(self, asset_id: str):
        super().__init__(
            f"texlib-asset '{asset_id}' saknas i cachen -- "
            f"kör: python -m assetpipe texlib fetch")
        self.asset_id = asset_id


def load_manifest(path: Path | None = None) -> dict[str, dict]:
    """Läs manifestet till {id: entry} och validera grundfälten."""
    raw = json.loads((path or _MANIFEST_PATH).read_text())
    out: dict[str, dict] = {}
    for entry in raw:
        for field in ("id", "kind", "source", "url", "sha256", "license"):
            if field not in entry:
                raise TexlibError(f"manifest-post saknar '{field}': {entry}")
        if entry["kind"] not in ("pbr", "hdri"):
            raise TexlibError(f"okänd kind '{entry['kind']}' för {entry['id']}")
        if entry["license"] not in ALLOWED_LICENSES:
            raise TexlibError(
                f"{entry['id']}: licensen {entry['license']!r} är inte tillåten i "
                f"texlib ({sorted(ALLOWED_LICENSES)}) -- assets med "
                f"attributionskrav hör hemma i en separat, krediterad kanal")
        if len(entry["sha256"]) != 64:
            raise TexlibError(f"{entry['id']}: sha256 ser inte ut som en pin")
        if entry["id"] in out:
            raise TexlibError(f"dubblett-id i manifestet: {entry['id']}")
        out[entry["id"]] = entry
    return out


def cache_dir() -> Path:
    env = os.environ.get("ASSETPIPE_TEXLIB_CACHE")
    if env:
        return Path(env)
    # repo-roten = tre nivåer upp från assetpipe/texlib/__init__.py
    return Path(__file__).resolve().parents[2] / "texlib_cache"


def _download(url: str) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read()


def _asset_dir(asset_id: str) -> Path:
    return cache_dir() / asset_id


def is_cached(asset_id: str, entry: dict) -> bool:
    marker = _asset_dir(asset_id) / _OK_MARKER
    return marker.exists() and marker.read_text().strip() == entry["sha256"]


def fetch(ids: list[str] | None = None, *, downloader=None,
          manifest: dict[str, dict] | None = None) -> dict[str, Path]:
    """Hämta (idempotent) och verifiera assets; returnera {id: katalog}.

    ``downloader(url) -> bytes`` är injicerbar för tester. En asset vars
    nedladdade bytes inte matchar sin sha256-pin raderas och ger TexlibError
    -- hellre rött än en tyst utbytt textur i en deterministisk pipeline.
    """
    dl = downloader or _download
    man = manifest or load_manifest()
    targets = list(man) if ids is None else ids
    out: dict[str, Path] = {}
    for asset_id in targets:
        if asset_id not in man:
            raise TexlibError(f"okänt texlib-id: {asset_id}")
        entry = man[asset_id]
        dest = _asset_dir(asset_id)
        if is_cached(asset_id, entry):
            out[asset_id] = dest
            continue
        data = dl(entry["url"])
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"]:
            raise TexlibError(
                f"{asset_id}: sha256-avvikelse (fick {digest[:12]}…, "
                f"pinnat {entry['sha256'][:12]}…) -- källan har ändrat filen; "
                f"granska och uppdatera pinnen medvetet")
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        if entry["kind"] == "pbr":
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tf:
                tf.write(data)
                tmp = Path(tf.name)
            try:
                with zipfile.ZipFile(tmp) as zf:
                    zf.extractall(dest)
            finally:
                tmp.unlink(missing_ok=True)
        else:  # hdri: en enda .hdr-fil
            (dest / entry.get("file", f"{asset_id}.hdr")).write_bytes(data)
        (dest / _OK_MARKER).write_text(entry["sha256"])
        out[asset_id] = dest
    return out


def resolve(asset_id: str, *, manifest: dict[str, dict] | None = None) -> dict:
    """Slå upp en cachad asset: {'kind', 'dir', 'maps'|'file'}.

    PBR-kartor upptäcks på suffixkonventionen (PBR_MAP_SUFFIXES) så
    manifestet slipper lista filnamn per post.
    """
    man = manifest or load_manifest()
    if asset_id not in man:
        raise TexlibError(f"okänt texlib-id: {asset_id}")
    entry = man[asset_id]
    dest = _asset_dir(asset_id)
    if not is_cached(asset_id, entry):
        raise TexlibMissing(asset_id)
    if entry["kind"] == "hdri":
        hdrs = sorted(dest.glob("*.hdr"))
        if not hdrs:
            raise TexlibError(f"{asset_id}: ingen .hdr i cachen trots ok-markör")
        return {"kind": "hdri", "dir": dest, "file": hdrs[0]}
    maps: dict[str, Path] = {}
    for name, suffix in PBR_MAP_SUFFIXES.items():
        hits = sorted(dest.glob(f"*{suffix}"))
        if hits:
            maps[name] = hits[0]
    if "color" not in maps:
        raise TexlibError(f"{asset_id}: ingen *_Color.jpg hittad i cachen")
    return {"kind": "pbr", "dir": dest, "maps": maps}
