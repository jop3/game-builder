"""matlib/imagesets -- koppla in en texlib-PBR-uppsättning i ett recepts nodträd.

Bryggan mellan texlib (pinnade CC0-fotoscans) och materialrecepten: en enda
funktion som laddar kartorna med RÄTT färgrymd (Color = sRGB; allt annat
Non-Color -- fel färgrymd på normal/roughness är den klassiska tysta baken)
och returnerar utgångssocklarna så receptet kan lägga procedurellt slitage
OVANPÅ fotoscanen (hybridrecept) i stället för att välja antingen/eller.

Recepten kör i Blender (bpy importeras i funktionskroppen, samma disciplin
som matlib.nodes). Objektrums-mappning: samma ``TexCoord.Object`` -> Mapping
som de procedurella grupperna använder, så skalan styrs likadant.
"""
from __future__ import annotations


def wire_pbr_maps(nt, maps: dict, *, scale: float = 1.0) -> dict:
    """Bygg Image Texture-noder för en texlib-kartuppsättning i nodträdet ``nt``.

    ``maps`` är ``texlib.resolve(id)["maps"]`` ({kanoniskt namn: Path}).
    Returnerar {namn: utgångssocket} plus ``"normal"`` (NormalMap-nodens
    utgång) när normal_gl finns. Ingenting kopplas till BSDF:n -- receptet
    äger sammansättningen (det är poängen med hybridrecept).
    """
    import bpy

    tex = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    nt.links.new(tex.outputs["Object"], mapping.inputs["Vector"])

    out: dict = {}
    for name, path in maps.items():
        img = bpy.data.images.load(str(path), check_existing=True)
        # sRGB BARA för albedo; data-kartor i Non-Color eller baken ljuger
        img.colorspace_settings.name = "sRGB" if name == "color" else "Non-Color"
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = img
        node.projection = "BOX"          # objektrum utan UV-krav; blend för sömlöshet
        node.projection_blend = 0.25
        nt.links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        out[name] = node.outputs["Color"]

    if "normal_gl" in out:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(out["normal_gl"], nm.inputs["Color"])
        out["normal"] = nm.outputs["Normal"]
    return out
