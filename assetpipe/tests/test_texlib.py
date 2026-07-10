"""texlib: manifestintegritet, sha-pinnad hämtning, cache-idempotens och
kartupplösning -- allt bpy-fritt och utan nät (injicerad downloader)."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from assetpipe import texlib


# ---------- fixturer ----------

def _fake_zip(names: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, b"not-a-real-jpg")
    return buf.getvalue()


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSETPIPE_TEXLIB_CACHE", str(tmp_path / "cache"))
    return tmp_path / "cache"


def _manifest_for(data: bytes, *, kind="pbr", asset_id="test_pbr", file=None):
    entry = {
        "id": asset_id, "kind": kind, "source": "test",
        "url": f"https://example.invalid/{asset_id}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "license": "CC0-1.0", "credit": "test",
    }
    if file:
        entry["file"] = file
    return {asset_id: entry}


# ---------- det riktiga manifestet ----------

def test_shipped_manifest_loads_and_is_pinned():
    man = texlib.load_manifest()
    assert len(man) >= 5
    for asset_id, entry in man.items():
        assert entry["license"] in texlib.ALLOWED_LICENSES[entry["kind"]]
        assert len(entry["sha256"]) == 64
        assert entry["kind"] in ("pbr", "hdri", "font")
        assert entry["url"].startswith("https://")
        if entry["license"] != "CC0-1.0":     # villkorade licenser bara för typsnitt
            assert entry["kind"] == "font"


def test_manifest_rejects_non_cc0(tmp_path):
    bad = [{"id": "x", "kind": "pbr", "source": "s", "url": "https://u",
            "sha256": "0" * 64, "license": "CC-BY-4.0"}]
    p = tmp_path / "m.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(texlib.TexlibError, match="licens"):
        texlib.load_manifest(p)


def test_ofl_allowed_only_for_fonts(tmp_path):
    # OFL är ok för typsnitt...
    ok = [{"id": "f", "kind": "font", "source": "s", "url": "https://u",
           "sha256": "0" * 64, "license": "OFL-1.1"}]
    p = tmp_path / "ok.json"
    p.write_text(json.dumps(ok))
    assert "f" in texlib.load_manifest(p)
    # ...men inte för texturer (attributions-/villkorskanal hålls separat)
    bad = [dict(ok[0], kind="pbr")]
    p2 = tmp_path / "bad.json"
    p2.write_text(json.dumps(bad))
    with pytest.raises(texlib.TexlibError, match="licens"):
        texlib.load_manifest(p2)


def test_font_roundtrip(cache):
    data = b"fake-ttf-bytes"
    man = _manifest_for(data, kind="font", asset_id="test_font", file="a.ttf")
    texlib.fetch(["test_font"], downloader=lambda url: data, manifest=man)
    res = texlib.resolve("test_font", manifest=man)
    assert res["kind"] == "font"
    assert res["file"].name == "a.ttf"
    assert res["file"].read_bytes() == data


# ---------- fetch ----------

def test_fetch_verifies_extracts_and_resolves(cache):
    data = _fake_zip(["T_1K_Color.jpg", "T_1K_NormalGL.jpg", "T_1K_Roughness.jpg"])
    man = _manifest_for(data)
    got = texlib.fetch(["test_pbr"], downloader=lambda url: data, manifest=man)
    assert got["test_pbr"].exists()
    res = texlib.resolve("test_pbr", manifest=man)
    assert res["kind"] == "pbr"
    assert set(res["maps"]) == {"color", "normal_gl", "roughness"}
    for p in res["maps"].values():
        assert Path(p).exists()


def test_fetch_rejects_sha_mismatch(cache):
    data = _fake_zip(["T_1K_Color.jpg"])
    man = _manifest_for(data)
    man["test_pbr"]["sha256"] = "f" * 64
    with pytest.raises(texlib.TexlibError, match="sha256"):
        texlib.fetch(["test_pbr"], downloader=lambda url: data, manifest=man)
    # ingen halvfärdig cache får lämnas godkänd
    with pytest.raises(texlib.TexlibMissing):
        texlib.resolve("test_pbr", manifest=man)


def test_fetch_is_idempotent(cache):
    data = _fake_zip(["T_1K_Color.jpg"])
    man = _manifest_for(data)
    calls = []

    def dl(url):
        calls.append(url)
        return data

    texlib.fetch(["test_pbr"], downloader=dl, manifest=man)
    texlib.fetch(["test_pbr"], downloader=dl, manifest=man)
    assert len(calls) == 1   # andra gången: cache-träff, ingen nedladdning


def test_hdri_roundtrip(cache):
    data = b"HDR-bytes"
    man = _manifest_for(data, kind="hdri", asset_id="test_hdri", file="a.hdr")
    texlib.fetch(["test_hdri"], downloader=lambda url: data, manifest=man)
    res = texlib.resolve("test_hdri", manifest=man)
    assert res["kind"] == "hdri"
    assert res["file"].name == "a.hdr"
    assert res["file"].read_bytes() == data


def test_resolve_unknown_and_missing(cache):
    data = _fake_zip(["T_1K_Color.jpg"])
    man = _manifest_for(data)
    with pytest.raises(texlib.TexlibError, match="okänt"):
        texlib.resolve("nope", manifest=man)
    with pytest.raises(texlib.TexlibMissing, match="texlib fetch"):
        texlib.resolve("test_pbr", manifest=man)


def test_pbr_without_color_map_is_an_error(cache):
    data = _fake_zip(["T_1K_Roughness.jpg"])   # ingen _Color.jpg
    man = _manifest_for(data)
    texlib.fetch(["test_pbr"], downloader=lambda url: data, manifest=man)
    with pytest.raises(texlib.TexlibError, match="Color"):
        texlib.resolve("test_pbr", manifest=man)
