"""Assemble the validated Othello assets into a played position and render a
fantasy Othello beauty shot in Cycles (headless). Run:

  blender --background --python-exit-code 1 --python assemble_render.py -- \
      --board BOARD.glb --light MOON.glb --dark OBS.glb \
      --border 0.0265 --out board.png

Disc cell placement is derived from the board's measured world AABB + the
resolved border, so it works whatever the seeded board dimensions came out to.
"""
import bpy, sys, math
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
def arg(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default

BOARD = arg("--board"); LIGHT = arg("--light"); DARK = arg("--dark")
BORDER = float(arg("--border", "0.0265")); OUT = arg("--out", "/tmp/othello.png")
CELLS = 8

# A natural-looking mid-game Othello position (row 0 = far side).
POSITION = [
    "........",
    "...D....",
    "..DDD...",
    "..LLDDL.",
    ".DLLLD..",
    "..DLDD..",
    "...LL...",
    "........",
]

def clear():
    bpy.ops.object.select_all(action="SELECT"); bpy.ops.object.delete()
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)

def import_glb(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    # return the largest mesh (the asset root; LODs were 'none' so there is one)
    root = max(meshes, key=lambda o: o.dimensions.x * o.dimensions.y * o.dimensions.z)
    return root, new

def world_aabb(obj):
    bpy.context.view_layer.update()
    cs = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [v.x for v in cs]; ys = [v.y for v in cs]; zs = [v.z for v in cs]
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))

clear()

board, _ = import_glb(BOARD)
bx0, bx1, by0, by1, bz0, bz1 = world_aabb(board)
bw = min(bx1 - bx0, by1 - by0)              # board footprint (square)
cx = (bx0 + bx1) / 2.0; cy = (by0 + by1) / 2.0
play = bw - 2.0 * BORDER
cell = play / CELLS
# wood surface: just under the board top (the grid ribs are the proud bit)
surf_z = bz1 - 0.004

light_disc, light_nodes = import_glb(LIGHT)
dark_disc, dark_nodes = import_glb(DARK)
for o in (light_disc, dark_disc):
    o.hide_render = True                    # originals are templates; we place copies

def place(template, r, c):
    d = template.copy(); d.data = template.data          # linked mesh (cheap)
    bpy.context.collection.objects.link(d)
    d.hide_render = False
    px = cx - play / 2.0 + (c + 0.5) * cell
    py = cy - play / 2.0 + (CELLS - 1 - r + 0.5) * cell   # row 0 = far (+Y)
    d.location = (px, py, surf_z)
    return d

n_l = n_d = 0
for r, row in enumerate(POSITION):
    for c, ch in enumerate(row):
        if ch == "L": place(light_disc, r, c); n_l += 1
        elif ch == "D": place(dark_disc, r, c); n_d += 1
print(f"PLACED moonstone={n_l} obsidian={n_d}")

# --- staging: warm table + candlelit key, soft fill, angled camera ---
bpy.ops.mesh.primitive_plane_add(size=4.0, location=(cx, cy, bz0 + 0.0005))
table = bpy.context.active_object
tmat = bpy.data.materials.new("table"); tmat.use_nodes = True
bsdf = tmat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.06, 0.035, 0.02, 1.0)
bsdf.inputs["Roughness"].default_value = 0.7
table.data.materials.append(tmat)

# warm key (candlelight) high on one side. Deliberately restrained: the whole
# point of Othello is the light-vs-dark disc contrast, which over-lighting
# destroys (obsidian washes to grey). Keep it dim + warm so the dark piece
# reads dark and the mood stays candlelit.
key = bpy.data.lights.new("key", "AREA"); key.energy = 24; key.size = 0.55
key.color = (1.0, 0.66, 0.36)
ko = bpy.data.objects.new("key", key); bpy.context.collection.objects.link(ko)
ko.location = (cx - 0.32, cy - 0.22, 0.48); ko.rotation_euler = (math.radians(40), math.radians(-15), 0)
# barely-there cool fill: just keeps the far shadows from crushing to pure black
fill = bpy.data.lights.new("fill", "AREA"); fill.energy = 1.0; fill.size = 2.0
fill.color = (0.5, 0.62, 0.95)
fo = bpy.data.objects.new("fill", fill); bpy.context.collection.objects.link(fo)
fo.location = (cx + 0.6, cy + 0.5, 0.5)
# warm rim to catch the disc edges and separate the dark pieces from the board
rim = bpy.data.lights.new("rim", "AREA"); rim.energy = 9; rim.size = 0.35
rim.color = (1.0, 0.78, 0.5)
ro = bpy.data.objects.new("rim", rim); bpy.context.collection.objects.link(ro)
ro.location = (cx + 0.12, cy + 0.55, 0.3)

world = bpy.data.worlds.new("w"); bpy.context.scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.015, 0.015, 0.02, 1.0)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.02

cam = bpy.data.cameras.new("cam"); camo = bpy.data.objects.new("cam", cam)
bpy.context.collection.objects.link(camo); bpy.context.scene.camera = camo
cam.lens = 60
camo.location = (cx + 0.02, cy - 0.62, 0.46)
# aim at board center just above surface
target = Vector((cx, cy + 0.02, surf_z))
d = target - camo.location
camo.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()

sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.samples = 160
try: sc.cycles.device = "CPU"
except Exception: pass
sc.render.resolution_x = 1600; sc.render.resolution_y = 1100
sc.view_settings.view_transform = "AgX"
sc.view_settings.exposure = -1.1        # pull the whole shot down into candlelit territory
sc.render.image_settings.file_format = "PNG"
sc.render.filepath = OUT
bpy.ops.render.render(write_still=True)
print("RENDERED ->", OUT)
