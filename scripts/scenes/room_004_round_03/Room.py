"""
build_room.py — deterministic blank-sheet room assembly
=======================================================
Take a RoomPlan scan (`room.usdz` + capture frames) plus a set of generated
per-object GLBs, and assemble them into ONE blank room scene. This is the
"layer" between asset generation and the harness: pure, deterministic geometry
placement on a neutral baseline. Same inputs -> same `room.glb`, no agents,
no randomness, no materials, no fixtures — those are the harness's job.

What it does (and ONLY this):
  1. import the RoomPlan shell (walls + floor)            -> "Room_Shell"
  2. thicken the thin walls outward (clean inside corners)
  3. boolean-cut the door/window openings through the walls
  4. drop every generated GLB into its RoomPlan box        -> "Furniture" / "Openings"
     (each asset becomes a NAMED empty handle: Table0, Chair0, Door0, ...)
  5. rebuild the ARKit capture cameras (exact pose + intrinsics) -> "ARKit_Cameras"
  6. paint the shell a single neutral grey (the BLANK baseline — no ceiling,
     no textures, no wall fixtures) and floor-ground the furniture
  7. export:  room.glb              the assembled blank scene (named nodes)
              room_layout.json      machine-readable objects + cameras

Inputs
------
  <scan_dir>   a folder with room.usdz + frame_*.{jpg,json}  (RoomPlan capture)
  <assets_dir> a folder with glb/*.glb + manifest.json       (see pack_assets.py)
               (no manifest -> glob glb/*.glb, map each by prim name)
Output
------
  <out.glb>    and room_layout.json next to it

CLI
---
  blender -b --python build_room.py -- <scan_dir> <assets_dir> <out.glb>

Python API (inside Blender)
---------------------------
  scene = RoomScene(scan_dir, assets_dir, out_glb).build()
  scene.summary(); scene.tables(); scene.top_surface("Table0")
  scene.place_on("Table0", "/path/lamp.glb", offset=(0.2, -0.1))
  scene.render_from(30, "/tmp/v.png")
"""

import json
import math
import os
import re
import sys
import zipfile

import bpy
from mathutils import Matrix, Vector
"""Shared finite wall volume convention for layout checks and Blender assembly.

Measured planes are not 16 cm centred slabs. Thin walls grow OUTWARD to the
renderer minimum; the captured interior face stays put. No furniture exemption.
"""
import math

WALL_TARGET = .10
WALL_MIN_MESH = .001
CONTACT_TOL = .005

def on_floor(point, triangles):
    x,y=point[:2]
    for a,b,c in triangles:
        den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if abs(den)<1e-12:continue
        u=((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/den
        v=((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/den
        if u>=-1e-6 and v>=-1e-6 and u+v<=1+1e-6:return True
    return False

def outward_sign(center, normal, room_center, triangles=(), target=WALL_TARGET):
    """Same floor-sidedness rule as upstream Blender, with its centroid fallback."""
    dot=sum(normal[i]*(center[i]-room_center[i]) for i in (0,1))
    sign=1 if dot>=0 else -1
    if triangles:
        distance=max(target,.05)*2
        plus=on_floor([center[i]+normal[i]*distance for i in (0,1)],triangles)
        minus=on_floor([center[i]-normal[i]*distance for i in (0,1)],triangles)
        if plus!=minus:return (-1 if plus else 1),'floor'
    return sign,'centroid_fallback'

def collision_walls(shell):
    """Derive solids fresh, ignoring any caller-supplied collision override fields."""
    walls=shell.get('walls') or {}
    mids=[[(w['start'][i]+w['end'][i])/2 for i in (0,1)] for w in walls.values()]
    center=[sum(p[i] for p in mids)/max(len(mids),1) for i in (0,1)]
    floor=shell.get('floor') or {};verts=floor.get('verts') or []
    triangles=[[verts[i] for i in face] for face in floor.get('faces',[]) if len(face)==3]
    result={}
    for name,w in walls.items():
        start,end=w['start'],w['end'];length=math.dist(start,end)
        measured=float(w.get('thickness',.001))
        if not math.isfinite(length) or length<=0 or not math.isfinite(measured) or measured<=0:
            raise ValueError('Invalid measured wall geometry: '+name)
        n=[-(end[1]-start[1])/length,(end[0]-start[0])/length]
        mid=[(start[i]+end[i])/2 for i in (0,1)]
        sign,source=outward_sign(mid,n,center,triangles)
        mesh=max(measured,WALL_MIN_MESH);total=max(mesh,WALL_TARGET)
        offset=sign*(total-mesh)/2
        result[name]=dict(w,thickness=measured,solid_interval=[offset-total/2,offset+total/2],
                          solid_policy='outward-v1',solid_side_source=source)
    return result

def footprint_penetration(corners, wall):
    """Clip the footprint to the finite wall span, then measure solid penetration.

    A door panel merely touching the interior face has zero depth. A deep box
    crossing a wall is still rejected even if its centre is well inside the room.
    """
    a,b=wall['start'],wall['end'];length=math.dist(a,b)
    if length<=0:raise ValueError('Degenerate wall')
    along=[(b[i]-a[i])/length for i in (0,1)];normal=[-along[1],along[0]]
    poly=[[sum((p[i]-a[i])*along[i] for i in (0,1)),
           sum((p[i]-a[i])*normal[i] for i in (0,1))] for p in corners]
    for edge,side in ((0,1),(length,-1)):
        clipped=[]
        for prev,cur in zip(poly[-1:]+poly[:-1],poly):
            pin=side*(prev[0]-edge)>=0;cin=side*(cur[0]-edge)>=0
            if pin!=cin:
                t=(edge-prev[0])/(cur[0]-prev[0])
                clipped.append([edge,prev[1]+t*(cur[1]-prev[1])])
            if cin:clipped.append(cur)
        poly=clipped
        if not poly:return 0.
    # Ignore zero-area tangency at the segment endpoint, not a real overlap.
    if max(p[0] for p in poly)-min(p[0] for p in poly)<1e-9:return 0.
    half=float(wall.get('thickness',.001))/2
    lo,hi=wall.get('solid_interval',[-half,half])
    pmin=min(p[1] for p in poly);pmax=max(p[1] for p in poly)
    return max(0.,min(hi-pmin,pmax-lo))


# ============================================================================
# CONFIG
# ============================================================================
try:
    SELF_PATH = os.path.abspath(__file__)
    HERE = os.path.dirname(SELF_PATH)
except NameError:
    SELF_PATH = None
    HERE = os.getcwd()
PROJECT = os.path.dirname(HERE)  # room_ops package root


def _default_scan():
    inp = os.path.join(PROJECT, "input")
    if os.path.isdir(inp):
        for n in sorted(os.listdir(inp)):
            if os.path.exists(os.path.join(inp, n, "room.usdz")):
                return os.path.join(inp, n)
    return os.path.join(inp, "scan")


_argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
SCAN_DIR = _argv[0] if len(_argv) > 0 else _default_scan()
ASSETS_DIR = _argv[1] if len(_argv) > 1 else os.path.join(PROJECT, "input", "assets")
OUT_GLB = _argv[2] if len(_argv) > 2 else os.path.join(PROJECT, "run", "room.glb")

BUILD_CAMERAS = True  # rebuild the ARKit capture cameras + record their info

# assembly tuning
WALL_THICK = 0.10  # wall thickness, extruded OUTWARD only (m)
OPENING_FILL = 1.02  # door/window sized vs the hole -> fills + overlaps rim
CUT_DEPTH = 1.0  # opening cutter depth along wall normal (> wall thickness)
CUT_MARGIN = 0.98  # hole enlarged slightly vs the opening box
# Emissive card behind windows: reads as daylight in Cycles/Eevee, but glTF has no emission-
# strength concept to carry it, so in Room.glb / the Three.js viewer it exports as a flat opaque
# WHITE rectangle plugging the opening. Off by default; turn on only for Blender renders.
DAYLIGHT_CARD = False
CHAIR_YAW = 0.0  # no blanket chair flip: orientation now comes from the box yaw
# (set non-zero only if a real, uniform front-convention offset is found)
SENSOR_W = 36.0  # reference camera sensor width (mm) for lens math

# the blank baseline: one neutral grey on every wall + the floor, nothing else.
# Materials, the ceiling and wall fixtures are deliberately NOT built here —
# they are what the harness produces, so every scene starts identical + blank.
BLANK_SHELL_SRGB = (0.62, 0.62, 0.62)

# scene-graph collections
COLL_SHELL = "Room_Shell"  # walls + floor
COLL_FURN = "Furniture"  # tables, chairs, storage, ...
COLL_OPEN = "Openings"  # doors, windows
COLL_CAM = "ARKit_Cameras"  # capture cameras
COLL_ADDED = "Added"  # anything placed after build (e.g. items on tables)

OPENING_PREFIXES = ("Door", "Window", "Opening")
PLACEABLE_CATS = {"table", "desk", "storage", "counter", "cabinet", "shelf"}

# RoomPlan prim prefix -> semantic category
_CATEGORY_PREFIXES = [
    ("Wall", "wall"),
    ("Floor", "floor"),
    ("Ceiling", "ceiling"),
    ("Door", "door"),
    ("Window", "window"),
    ("Opening", "opening"),
    ("Table", "table"),
    ("Chair", "chair"),
    ("Storage", "storage"),
    ("Sofa", "sofa"),
    ("Bed", "bed"),
    ("Television", "television"),
    ("Refrigerator", "refrigerator"),
    ("Oven", "oven"),
    ("Stove", "stove"),
    ("Sink", "sink"),
    ("Toilet", "toilet"),
    ("Bathtub", "bathtub"),
    ("Stairs", "stairs"),
    ("Fireplace", "fireplace"),
]


def category_of(name):
    base = re.sub(r"\.\d+$", "", name)
    for prefix, cat in _CATEGORY_PREFIXES:
        if base.startswith(prefix):
            return cat
    return "object"


# ============================================================================
# Low-level Blender helpers
# ============================================================================
def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for coll in (bpy.data.meshes, bpy.data.cameras, bpy.data.lights, bpy.data.images):
        for db in list(coll):
            if db.users == 0:
                coll.remove(db)
    for c in list(bpy.data.collections):
        if not c.objects and not c.children:
            bpy.data.collections.remove(c)


def find_usdz(scan_dir):
    for p in (os.path.join(scan_dir, "room.usdz"), os.path.join(scan_dir, "roomplan", "room.usdz")):
        if os.path.exists(p):
            return p
    raise SystemExit(f"room.usdz not found in {scan_dir}")


def import_usd(path):
    before = set(bpy.data.objects)
    bpy.ops.wm.usd_import(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def import_glb(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    imported = [o for o in bpy.data.objects if o not in before]
    # Articulated assets (doors/windows) ship a closed->open animation. The assembled room must
    # hold the CLOSED/rest pose for bbox/placement (else a window swung open reads as ~4.8 m), so
    # evaluate frame 0 (closed). We KEEP the animation (do NOT clear it) so the clip survives into
    # the assembled Room.glb (export_animations=True) — the leaf stays closed at rest and plays on
    # demand in a viewer. Placement transforms the ROOT matrix only, so the leaf's local clip rides
    # along untouched.
    bpy.context.scene.frame_set(0)
    return imported


def get_or_make_collection(name):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def move_to_collection(obj, coll):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def group_fixture(name, category, parts, parent_coll="Fixtures", *,
                  rests_on=None, attached_to=None, support_part=None):
    """Bundle a wall/ceiling fixture's part-objects (frame bars, radiator fins, pen tray, faceplate,
    louvre slats …) into ONE named, hide-as-a-unit group — the fixture analogue of `_wrap_handle`
    for furniture. It creates an empty handle `name` (carrying room_id/category custom props),
    parents every part under it (keeping world pose), and moves the whole set into a per-fixture
    collection nested under `parent_coll`. In the viewer / Blender outliner / exported glTF the
    fixture then selects, hides and toggles as a SINGLE object — e.g. `Whiteboard0`, `Radiator0`,
    `Shelf0` — instead of a loose pile of boxes you'd have to hide one at a time.

    `parts` = the list of objects the box helpers returned (drop the returns of each `_wall_box(...)`
    / `_uv_box(...)` call for this one fixture into a list and pass it in). Returns the empty handle.
    """
    parts = [p for p in parts if p is not None]
    grp = bpy.data.objects.new(name, None)
    grp.empty_display_size = 0.05
    grp["room_id"] = name
    grp["category"] = category
    grp["fixture"] = True
    # SIMULATION READINESS. A physics engine needs to know what holds what up: a mug rests on a
    # desk that rests on the floor, a whiteboard is bolted to a wall. Geometry alone cannot say
    # which — two boxes touching is not the same as one supporting the other, and a prop authored
    # a millimetre proud of its surface is indistinguishable from one floating. So the author
    # states it, and it travels with the object into `room_layout.json` where a simulator (or a
    # QC pass) can read it back. `rests_on` = the surface it stands on; `attached_to` = what it is
    # fixed to when it is not resting on anything (a wall, a ceiling).
    if rests_on:
        grp["rests_on"] = rests_on
    if attached_to:
        grp["attached_to"] = attached_to
    if support_part:
        grp["support_part"] = support_part
    coll = get_or_make_collection(name)
    # nest this fixture's own collection under the shared Fixtures parent, so the outliner shows
    # Whiteboard0 / Radiator0 / … as tidy sub-groups rather than every part loose in one bucket.
    if parent_coll:
        try:
            parent = get_or_make_collection(parent_coll)
            if coll.name not in {c.name for c in parent.children}:
                try:
                    bpy.context.scene.collection.children.unlink(coll)
                except Exception:  # not linked at the scene root (already nested) — fine
                    pass
                parent.children.link(coll)
        except Exception:  # nesting is cosmetic; never let it break the build
            pass
    coll.objects.link(grp)
    for p in parts:
        move_to_collection(p, coll)
        p.parent = grp
        p.matrix_parent_inverse = grp.matrix_world.inverted()
    return grp


def mesh_descendants(obj):
    out, stack = [], [obj]
    while stack:
        o = stack.pop()
        if o != obj and o.get("room_id"):
            continue  # supported objects belong to their own handle, not their support's bounds
        if o.type == "MESH":
            out.append(o)
        stack.extend(o.children)
    return out


def world_bbox(meshes):
    mn = Vector((1e9, 1e9, 1e9))
    mx = Vector((-1e9, -1e9, -1e9))
    for o in meshes:
        if o.type != "MESH":
            continue
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            for i in range(3):
                mn[i] = min(mn[i], w[i])
                mx[i] = max(mx[i], w[i])
    return mn, mx


# ============================================================================
# Assembly primitives (walls / openings / box-fit) — proven geometry
# ============================================================================
def _floor_triangles(floors):
    """World-space XY triangles of the floor plate, for deciding which side of a wall is inside."""
    tris = []
    for f in floors:
        me = f.data
        for poly in me.polygons:
            vs = [(f.matrix_world @ me.vertices[i].co).xy for i in poly.vertices]
            for k in range(1, len(vs) - 1):
                tris.append((vs[0], vs[k], vs[k + 1]))
    return tris


def _on_floor(pt, tris):
    for a, b, c in tris:
        den = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y)
        if abs(den) < 1e-12:
            continue
        u = ((b.y - c.y) * (pt.x - c.x) + (c.x - b.x) * (pt.y - c.y)) / den
        v = ((c.y - a.y) * (pt.x - c.x) + (a.x - c.x) * (pt.y - c.y)) / den
        if u >= -1e-6 and v >= -1e-6 and u + v <= 1.0 + 1e-6:
            return True
    return False


def thicken_walls(walls, room_center, target=WALL_THICK, floor_tris=None):
    """Extrude each wall's OUTER face outward only, keeping the interior face
    where RoomPlan put it. Interior footprint is unchanged -> inside corners stay
    clean; the added thickness/overlap goes to the outside, out of view.

    Which face is "outer" is decided by the FLOOR PLATE, not by the room centroid. The centroid
    test is right for a convex room and wrong exactly where it matters: a wall in the concave notch
    of an L-shaped plan sits on the far side of the centroid from its own interior, so the extrusion
    goes inward and the wall grows 10 cm into the room. On tea_room that is Wall7 and Wall8, and it
    swallows whatever is installed against them — the dishwasher the layout pass had just fitted
    into that corner ends up 2 cm inside the wall, with the pass reporting the room clean because
    the box it settled IS clear of the wall line it was given.

    `floor_tris` is optional: without it this falls back to the centroid test, so a caller that has
    no floor polygon behaves as before rather than failing.
    """
    n = 0
    for o in walls:
        vs = o.data.vertices
        if not vs:
            continue
        lo = [min(v.co[i] for v in vs) for i in range(3)]
        hi = [max(v.co[i] for v in vs) for i in range(3)]
        M3 = o.matrix_world.to_3x3()
        col = [M3.col[i].length for i in range(3)]
        world_ext = [(hi[i] - lo[i]) * col[i] for i in range(3)]
        t = min(range(3), key=lambda i: world_ext[i])  # wall-normal (thinnest)
        cur = world_ext[t]
        if cur < 1e-6 or cur >= target:
            continue
        add_local = (target - cur) / col[t]
        mid = (lo[t] + hi[t]) / 2.0
        axis_local = Vector((0.0, 0.0, 0.0))
        axis_local[t] = 1.0
        n_world = (M3 @ axis_local).normalized()
        centre = o.matrix_world.translation
        sign, _source = outward_sign(centre, n_world, room_center, floor_tris or (), target)
        outward_plus = sign > 0
        for v in vs:
            if outward_plus and v.co[t] > mid:
                v.co[t] += add_local
            elif (not outward_plus) and v.co[t] < mid:
                v.co[t] -= add_local
        o.data.update()
        n += 1
    return n


def opening_to_wall(usdz):
    """{opening_prim: wall_prim} from the usdz directory structure."""
    z = zipfile.ZipFile(usdz)
    mp = {}
    for nm in z.namelist():
        g = re.search(r"Walls/(Wall\d+)/([A-Za-z0-9]+)\.usda$", nm)
        if g and g.group(2) != g.group(1):
            mp[g.group(2)] = g.group(1)
    return mp


def _unit_cube_mesh(name):
    verts = [
        (-0.5, -0.5, -0.5),
        (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5),
        (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5),
        (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5),
        (-0.5, 0.5, 0.5),
    ]
    faces = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    return me


def cut_opening(opening_name, wall_name):
    op = bpy.data.objects.get(opening_name)
    wall = bpy.data.objects.get(wall_name)
    if op is None or wall is None:
        return False
    M = op.matrix_world.copy()
    cols = [M.to_3x3().col[i] for i in range(3)]
    thin = min(range(3), key=lambda i: cols[i].length)
    nc = [cols[i].normalized() * CUT_DEPTH if i == thin else cols[i] * CUT_MARGIN for i in range(3)]
    L4 = Matrix(
        (
            (nc[0].x, nc[1].x, nc[2].x, 0.0),
            (nc[0].y, nc[1].y, nc[2].y, 0.0),
            (nc[0].z, nc[1].z, nc[2].z, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )
    )
    cutter = bpy.data.objects.new(
        "cutter_" + opening_name, _unit_cube_mesh("cutter_" + opening_name)
    )
    bpy.context.scene.collection.objects.link(cutter)
    cutter.matrix_world = Matrix.Translation(M.translation) @ L4
    mod = wall.modifiers.new("cut_" + opening_name, "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    # Blender 4.x offers ("FAST", "EXACT"); 5.x replaced FAST with ("FLOAT", "EXACT", "MANIFOLD").
    # Pick the cheap solver available on this build rather than hardcoding a name that raises
    # TypeError on the other major version.
    _solvers = {i.identifier for i in mod.bl_rna.properties["solver"].enum_items}
    mod.solver = next((s for s in ("FAST", "FLOAT", "EXACT") if s in _solvers), "EXACT")
    bpy.context.view_layer.objects.active = wall
    wall.hide_viewport = False
    ok = True
    # Back up the wall mesh + its footprint so a misbehaving EXACT boolean (which occasionally shreds
    # a wall down to a thin strip -> the wall "goes missing") can be rolled back to the solid wall.
    saved = wall.data.copy()
    bpy.context.view_layer.update()
    dim0 = max(wall.dimensions)
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.context.view_layer.update()
        # if the cut collapsed the wall (largest span < 60% of the original), the boolean misfired —
        # keep the solid wall instead (the door/window GLB still sits in the opening visually).
        if dim0 > 1e-6 and max(wall.dimensions) < 0.6 * dim0:
            print(f"  ! cut {opening_name} collapsed {wall_name} "
                  f"({max(wall.dimensions):.2f}<{0.6*dim0:.2f}m) — keeping solid wall")
            wall.data = saved
            ok = False
        else:
            bpy.data.meshes.remove(saved)
    except Exception as e:
        print(f"  ! boolean failed {opening_name}->{wall_name}: {e}")
        wall.modifiers.remove(mod)
        wall.data = saved
        ok = False
    bpy.data.objects.remove(cutter, do_unlink=True)
    return ok


def box_frame(box):
    """(center, up, wdir, ddir, W, D, H) of a box's world OBB."""
    cols = [box.matrix_world.to_3x3().col[i] for i in range(3)]
    L = [c.length for c in cols]
    vert = max(range(3), key=lambda i: abs(cols[i].normalized().z))
    horis = [i for i in range(3) if i != vert]
    # box-LOCAL X = width, Y = depth — keep that order (the box already carries the
    # RoomPlan yaw). Do NOT reorder by length: doing so flips the orientation of any
    # object whose depth > width. Orientation must come from the box, not aspect ratio.
    wi, di = horis[0], horis[1]
    up = cols[vert].normalized()
    if up.z < 0:
        up = -up
    wdir = Vector((cols[wi].x, cols[wi].y, 0.0))
    if wdir.length < 1e-6:
        wdir = Vector((1.0, 0.0, 0.0))
    wdir.normalize()
    return box.matrix_world.translation.copy(), up, wdir, up.cross(wdir), L[wi], L[di], L[vert]


# ============================================================================
# Camera math (ARKit capture cameras)
# ============================================================================
def pose_matrix(flat16):
    """cameraPoseARFrame: row-major 4x4 camera->world (translation last column)."""
    return Matrix((flat16[0:4], flat16[4:8], flat16[8:12], flat16[12:16]))


def measure_C(imported):
    """Recover the ARKit(Y-up) -> Blender(Z-up) transform the USD importer used."""
    root = bpy.data.objects.get("room")
    if root is not None and root.type == "EMPTY":
        return root.matrix_world.copy()
    return Matrix.Rotation(math.radians(90), 4, "X")


def make_camera(name, M_blender, intr, render_w, render_h):
    fx, _, cx, _, fy, cy, _, _, _ = intr
    cd = bpy.data.cameras.new(name)
    cd.sensor_fit = "HORIZONTAL"
    cd.sensor_width = SENSOR_W
    cd.lens = fx * SENSOR_W / render_w
    cd.shift_x = (render_w * 0.5 - cx) / render_w
    cd.shift_y = (cy - render_h * 0.5) / render_w
    cd.clip_start = 0.01
    cd.clip_end = 100.0
    cd.display_size = 0.15
    obj = bpy.data.objects.new(name, cd)
    obj.matrix_world = M_blender
    return obj


def detect_resolution(scan_dir, sample_intrinsics):
    jpgs = sorted(f for f in os.listdir(scan_dir) if f.startswith("frame_") and f.endswith(".jpg"))
    if jpgs:
        img = bpy.data.images.load(os.path.join(scan_dir, jpgs[0]), check_existing=True)
        w, h = int(img.size[0]), int(img.size[1])
        bpy.data.images.remove(img)
        if w > 0 and h > 0:
            return w, h
    fx, _, cx, _, fy, cy, _, _, _ = sample_intrinsics
    return int(round(cx * 2)), int(round(cy * 2))


def srgb_to_linear(c):
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c)


# ============================================================================
# The scene
# ============================================================================
class RoomScene:
    GROUND_CATS = ("chair", "table", "sofa", "desk", "stool", "bench", "bed")

    def __init__(
        self, scan_dir=SCAN_DIR, assets_dir=ASSETS_DIR, out_glb=OUT_GLB, build_cameras=BUILD_CAMERAS
    ):
        self.scan_dir = scan_dir
        self.assets_dir = assets_dir
        self.out_glb = out_glb
        self.build_cameras_flag = build_cameras
        self.objects = {}  # id -> record (see _register)
        self.cameras = []  # list of camera records
        self.render_w = self.render_h = None
        self.C = None
        self._added_counts = {}

    # ---------------------------------------------------------------- build
    def build(self):
        print("=== RoomScene.build (blank) ===")
        clear_scene()
        imported, mapping = self._load_shell()

        shell = get_or_make_collection(COLL_SHELL)
        walls = [o for o in imported if o.type == "MESH" and re.match(r"Wall\d+$", o.name)]
        floors = [o for o in imported if o.type == "MESH" and category_of(o.name) == "floor"]
        # the per-object placement boxes (everything that isn't a wall/floor/opening) —
        # any of these still a MESH after placement means "no GLB was generated for it".
        object_box_names = [
            o.name
            for o in imported
            if o.type == "MESH" and o not in walls and o not in floors and o.name not in mapping
        ]
        for o in walls + floors:
            move_to_collection(o, shell)

        room_center = sum((o.matrix_world.translation for o in walls), Vector()) / max(
            len(walls), 1
        )
        n_thick = thicken_walls(walls, room_center, floor_tris=_floor_triangles(floors))
        print(f"  thickened {n_thick} walls outward to ~{WALL_THICK} m")

        n_cut = sum(cut_opening(op, wall) for op, wall in mapping.items())
        print(f"  cut {n_cut}/{len(mapping)} openings {mapping}")

        placed = self._place_all_assets()
        print(f"  placed {placed} asset instances")

        # Drop any object box that never received a GLB. A filled box was removed and
        # replaced by an EMPTY handle of the same name; an unfilled one is still a MESH.
        # We DON'T keep it as a placeholder: RoomPlan over-detects, and an empty slot
        # (nothing generated, or a spurious detection) should simply be absent.
        dropped = []
        for nm in object_box_names:
            ob = bpy.data.objects.get(nm)
            if ob is not None and ob.type == "MESH":
                bpy.data.objects.remove(ob, do_unlink=True)
                dropped.append(nm)
        if dropped:
            print(f"  dropped {len(dropped)} empty object box(es) (no GLB generated): {dropped}")

        if self.build_cameras_flag:
            n_cam = self._build_cameras()
            print(f"  built {n_cam} capture cameras")

        self._flatten_shell()
        self._apply_blank_shell()  # neutral grey shell, no ceiling/materials/fixtures
        self.index()
        print(f"  indexed {len(self.objects)} objects, {len(self.cameras)} cameras")

        # Snap floor-standing furniture's visible mesh to the floor (vertical only;
        # keeps canonical XY). Fixes assets whose mesh bottom sits off the box floor
        # (e.g. a TRELLIS chair whose legs dip below the box origin).
        ng = self._ground_visual_furniture()
        self.index()
        print(f"  floor-grounded {ng} visible piece(s)")

        # bake shell transforms LAST so local coords == world metres (downstream)
        self._apply_shell_transforms()
        return self

    def _load_shell(self):
        """Rebuild the shell; returns (imported objects, opening->wall map). In priority order:
        1. the SHELL embedded in this file (what the agent edits), 2. room_shell.json,
        3. room.usdz as a fallback."""
        g = globals().get("SHELL")
        if isinstance(g, dict) and g.get("walls"):
            return self._build_shell_from_compact(g)
        for d in (self.assets_dir, self.scan_dir):
            sj = os.path.join(d, "room_shell.json")
            if os.path.exists(sj):
                return self._build_shell_from_compact(json.load(open(sj)))
        usdz = find_usdz(self.scan_dir)
        return import_usd(usdz), opening_to_wall(usdz)

    def _box_obj(self, name, center, xd, xl, yd, yl, zl):
        """One box: a unit cube, axis-aligned in local space, scaled and rotated into world by a
        matrix. xd/yd are the horizontal direction vectors; xl/yl/zl the lengths along x, y and
        vertical. box_frame can recover the OBB from these."""
        me = _unit_cube_mesh(name)
        ob = bpy.data.objects.new(name, me)
        ob.matrix_world = Matrix(
            (
                (xd.x * xl, yd.x * yl, 0.0, center[0]),
                (xd.y * xl, yd.y * yl, 0.0, center[1]),
                (0.0, 0.0, zl, center[2]),
                (0.0, 0.0, 0.0, 1.0),
            )
        )
        bpy.context.scene.collection.objects.link(ob)
        return ob

    def _wall_free_spans(self, wall_name):
        """The runs along this wall NOT taken by a door or window: [(s,e),...] in metres, measured
        from the wall's start. Horizontal fixtures (skirting, trunking, radiators) should only be
        laid within these, so they break at each opening instead of smearing across a Door or
        Window."""
        S = globals().get("SHELL") or {}
        w = (S.get("walls") or {}).get(wall_name)
        if not w:
            return []
        sx, sy = w["start"]
        ex, ey = w["end"]
        length = math.hypot(ex - sx, ey - sy)
        blocks = sorted(
            (op["offset"] - op["width"] / 2.0, op["offset"] + op["width"] / 2.0)
            for op in (S.get("openings") or {}).values()
            if op.get("wall") == wall_name
        )
        spans, cur = [], 0.0
        for b0, b1 in blocks:
            if b0 > cur + 1e-3:
                spans.append((cur, b0))
            cur = max(cur, b1)
        if cur < length - 1e-3:
            spans.append((cur, length))
        return spans

    def _build_shell_from_compact(self, S):
        """Rebuild the geometry from the semantic SHELL: walls as start/end points, openings as
        wall + offset + width/height + sill, objects as centre + size + yaw."""
        fz, cz = S["floor_z"], S["ceiling_z"]
        H = cz - fz
        imported = []

        def wall_dirs(w):
            s = Vector((w["start"][0], w["start"][1], 0.0))
            e = Vector((w["end"][0], w["end"][1], 0.0))
            d = e - s
            ln = d.length
            d = d.normalized() if ln > 1e-9 else Vector((1.0, 0.0, 0.0))
            return s, e, d, Vector((-d.y, d.x, 0.0)), ln

        for name, w in S["walls"].items():  # walls: a thin box, thickened afterwards
            s, e, d, n, ln = wall_dirs(w)
            mid = (s + e) / 2
            th = max(w.get("thickness", 0.001), 0.001)
            imported.append(self._box_obj(name, (mid.x, mid.y, (fz + cz) / 2), d, ln, n, th, H))

        for name, od in S["objects"].items():  # object boxes: centre / size / yaw
            r = math.radians(od["yaw"])
            xd = Vector((math.cos(r), math.sin(r), 0.0))
            yd = Vector((-math.sin(r), math.cos(r), 0.0))
            sz = od["size"]
            imported.append(self._box_obj(name, od["center"], xd, sz[0], yd, sz[1], sz[2]))

        mapping = {}
        for name, op in S["openings"].items():  # openings: offset along wall + size + sill -> cutter box
            wall = op["wall"]
            if wall not in S["walls"]:
                continue
            s, e, d, n, ln = wall_dirs(S["walls"][wall])
            pos = s + d * op["offset"]
            zc = fz + op["sill"] + op["height"] / 2.0
            imported.append(
                self._box_obj(name, (pos.x, pos.y, zc), d, op["width"], n, 0.02, op["height"])
            )
            mapping[name] = wall

        if S.get("floor"):  # the floor polygon, kept faithful
            f = S["floor"]
            me = bpy.data.meshes.new("Floor0")
            me.from_pydata([tuple(v) for v in f["verts"]], [], [tuple(x) for x in f["faces"]])
            me.update()
            ob = bpy.data.objects.new("Floor0", me)
            bpy.context.scene.collection.objects.link(ob)
            imported.append(ob)

        rm = S.get("root_matrix")
        if rm:  # lets measure_C recover the camera calibration
            root = bpy.data.objects.new("room", None)
            root.matrix_world = Matrix((rm[0:4], rm[4:8], rm[8:12], rm[12:16]))
            bpy.context.scene.collection.objects.link(root)

        print(
            f"  shell <- SHELL(compact): {len(S['walls'])} walls, {len(S['openings'])} "
            f"openings, {len(S['objects'])} objects (no usdz)"
        )
        return imported, mapping

    def _asset_plan(self):
        """-> [(glb_abspath, [box_name, ...])] from manifest.json, else glob."""
        manifest = os.path.join(self.assets_dir, "manifest.json")
        plan = []
        if os.path.exists(manifest):
            data = json.load(open(manifest))
            for a in data.get("assets", []):
                glb = os.path.join(self.assets_dir, a["glb"])
                # Render workers pass the original scan pack. Use this room's
                # compiled final objects, exactly as the normal build does.
                final_glb = os.path.join(HERE, "..", "room_preview", "Object",
                                         a.get("object", "") + ".glb")
                if os.path.isfile(final_glb):
                    glb = final_glb
                targets = a.get("represents_prims") or [a.get("maps_to_prim")]
                plan.append((glb, [t for t in targets if t]))
        else:
            import glob

            d = os.path.join(self.assets_dir, "glb")
            d = d if os.path.isdir(d) else self.assets_dir
            for f in sorted(glob.glob(os.path.join(d, "*.glb"))):
                base = os.path.splitext(os.path.basename(f))[0].split("__")[-1]
                m = re.match(r"Wall\d+_(Door|Window|Opening)_(\d+)$", base) or re.match(
                    r"ChairCluster(\d+)$", base
                )
                tgt = (
                    f"{m.group(1)}{m.group(2)}"
                    if m and m.lastindex == 2
                    else f"Chair{m.group(1)}"
                    if m
                    else base
                )
                plan.append((f, [tgt]))
        return plan

    def _place_all_assets(self):
        placed = 0
        for glb, targets in self._asset_plan():
            if not os.path.exists(glb):
                print(f"  MISSING {glb}")
                continue
            for box_name in targets:
                if self._place_in_box(glb, box_name):
                    placed += 1
        return placed

    def _place_in_box(self, glb_path, box_name):
        """Fit a GLB into a RoomPlan box, replace the box with a named empty
        handle (Table0, Chair2, Door0, ...) holding the asset meshes."""
        box = bpy.data.objects.get(box_name)
        if box is None:
            return False
        cat = category_of(box_name)
        is_open = box_name.startswith(OPENING_PREFIXES)
        imported = import_glb(glb_path)
        meshes = [o for o in imported if o.type == "MESH"]
        if not meshes:
            return False
        roots = [o for o in imported if o.parent not in imported]

        amn, amx = world_bbox(meshes)
        asize = amx - amn
        acenter = (amn + amx) / 2.0
        aw, ad, ah = max(asize.x, 1e-6), max(asize.y, 1e-6), max(asize.z, 1e-6)
        center, up, wdir, ddir, W, D, H = box_frame(box)
        height_len = (H * OPENING_FILL) / ah if is_open else H / ah

        # Map the asset's OWN axes straight onto the box axes — asset X(width)->box width,
        # asset Y(depth)->box depth — and let the box's yaw set the facing. No aspect-ratio
        # swap: the asset is already built in its canonical frame, so swapping here is what
        # rotated correctly-built objects 90° / backwards.
        dirX, dirY = wdir, ddir
        if box_name == "Door2":
            # Entrance is left-hinged when seen from the hall (frames 0/6).
            # Reflect width only: keep its inward-facing hardware and swing.
            dirX = -dirX
        if is_open:
            sxlen = (W * OPENING_FILL) / aw
            # A hinged leaf must retain its width when it rotates into depth.
            sylen = sxlen
        else:
            sxlen, sylen = W / aw, D / ad

        colX, colY, colZ = dirX * sxlen, dirY * sylen, up * height_len
        L4 = Matrix(
            (
                (colX.x, colY.x, colZ.x, 0.0),
                (colX.y, colY.y, colZ.y, 0.0),
                (colX.z, colY.z, colZ.z, 0.0),
                (0.0, 0.0, 0.0, 1.0),
            )
        )
        Mw = Matrix.Translation(center) @ L4 @ Matrix.Translation(-acenter)
        # A door STANDS on the floor, so its bottom is a hard constraint. OPENING_FILL scales
        # about the box CENTRE, which buries the bottom rail by half the fill (10 cm on a 2.06 m
        # door at 1.10) and lifts the top by the same. Push it back up so the bottom lands exactly
        # on the box floor and the whole overlap goes to the header, where it is wanted — it
        # covers the cut rim. Windows are centred in their opening, so they keep the symmetric fit.
        if cat == "door":
            Mw = Matrix.Translation(up * (H * (OPENING_FILL - 1.0) / 2.0)) @ Mw
        for r in roots:
            r.matrix_world = Mw @ r.matrix_world

        if cat == "chair" and CHAIR_YAW:
            Rz = Matrix.Rotation(math.radians(CHAIR_YAW), 4, up)
            Yw = Matrix.Translation(center) @ Rz @ Matrix.Translation(-center)
            for r in roots:
                r.matrix_world = Yw @ r.matrix_world

        # WINDOWS: the recon glass is dark/tinted and there is nothing bright behind the
        # opening, so it renders as a black void instead of a daylit window. Add a bright
        # emissive "daylight" card BEHIND the glass (away from the room) so it reads as a lit
        # window. This does NOT touch the object_init window GLB — it's a separate card.
        if cat == "window" and DAYLIGHT_CARD:
            self._add_daylight_card(center, wdir, up, ddir, W, H)

        # the placeholder box is consumed; free its name for the asset handle
        bpy.data.objects.remove(box, do_unlink=True)
        coll = get_or_make_collection(COLL_OPEN if is_open else COLL_FURN)
        handle = self._wrap_handle(
            box_name, cat, roots, imported, coll, source=os.path.basename(glb_path)
        )
        return handle is not None

    def _room_centroid(self):
        """Approx room centre = centroid of wall-mesh object locations (cached)."""
        if getattr(self, "_room_c", None) is not None:
            return self._room_c
        pts = [
            o.matrix_world.translation
            for o in bpy.data.objects
            if o.type == "MESH" and category_of(o.name) == "wall"
        ]
        self._room_c = (sum(pts, Vector()) / len(pts)) if pts else Vector()
        return self._room_c

    def _add_daylight_card(self, center, wdir, up, ddir, W, H):
        """A bright emissive plane just BEHIND a window opening (side away from the room),
        sized a touch larger than the hole, so tinted recon glass reads as a lit window."""
        n = ddir.normalized()
        if n.dot(center - self._room_centroid()) < 0:  # face AWAY from the room
            n = -n
        loc = center + n * 0.06
        bpy.ops.mesh.primitive_plane_add(size=1, location=loc)
        card = bpy.context.active_object
        card.name = "daylight_card"
        R = Matrix(
            (
                (wdir.x, up.x, n.x, 0.0),
                (wdir.y, up.y, n.y, 0.0),
                (wdir.z, up.z, n.z, 0.0),
                (0.0, 0.0, 0.0, 1.0),
            )
        )
        card.matrix_world = Matrix.Translation(loc) @ R
        card.scale = (W * 1.15, H * 1.15, 1.0)
        m = bpy.data.materials.new("daylight")
        m.use_nodes = True
        nt = m.node_tree
        for node in list(nt.nodes):
            nt.nodes.remove(node)
        emis = nt.nodes.new("ShaderNodeEmission")
        emis.inputs["Color"].default_value = (0.92, 0.95, 1.0, 1.0)
        emis.inputs["Strength"].default_value = 4.0
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(emis.outputs["Emission"], out.inputs["Surface"])
        card.data.materials.append(m)
        move_to_collection(card, get_or_make_collection(COLL_OPEN))
        return card

    def _wrap_handle(self, name, cat, roots, all_objs, coll, source=None):
        """Create a named empty parent for an asset and move it into a collection."""
        handle = bpy.data.objects.new(name, None)
        handle.empty_display_size = 0.1
        handle["room_id"] = name
        handle["category"] = cat
        if source:
            handle["source_glb"] = source
        coll.objects.link(handle)
        for r in roots:
            r.parent = handle
            r.matrix_parent_inverse = handle.matrix_world.inverted()
        for o in all_objs:
            move_to_collection(o, coll)
        return handle

    def _build_cameras(self):
        frame_jsons = sorted(
            f for f in os.listdir(self.scan_dir) if re.match(r"frame_\d+\.json$", f)
        )
        if not frame_jsons:
            return 0
        first = json.load(open(os.path.join(self.scan_dir, frame_jsons[0])))
        self.render_w, self.render_h = detect_resolution(self.scan_dir, first["intrinsics"])
        self.C = measure_C(list(bpy.data.objects))
        coll = get_or_make_collection(COLL_CAM)
        self.cameras = []
        for fname in frame_jsons:
            data = json.load(open(os.path.join(self.scan_dir, fname)))
            idx = data.get("frame_index", int(re.search(r"\d+", fname).group()))
            cam = make_camera(
                f"cam_{idx:05d}",
                self.C @ pose_matrix(data["cameraPoseARFrame"]),
                data["intrinsics"],
                self.render_w,
                self.render_h,
            )
            coll.objects.link(cam)
            mw = cam.matrix_world
            fx, _, cx, _, fy, cy, _, _, _ = data["intrinsics"]
            self.cameras.append(
                {
                    "name": cam.name,
                    "frame_index": idx,
                    "image": f"frame_{idx:05d}.jpg",
                    "position": [round(v, 4) for v in mw.translation],
                    "forward": [
                        round(v, 4) for v in (mw.to_3x3() @ Vector((0, 0, -1))).normalized()
                    ],
                    "up": [round(v, 4) for v in (mw.to_3x3() @ Vector((0, 1, 0))).normalized()],
                    "intrinsics": {
                        "fx": round(fx, 3),
                        "fy": round(fy, 3),
                        "cx": round(cx, 3),
                        "cy": round(cy, 3),
                    },
                    "lens_mm": round(fx * SENSOR_W / self.render_w, 4),
                    "resolution": [self.render_w, self.render_h],
                }
            )
        bpy.context.scene.camera = bpy.data.objects.get("cam_00000")
        return len(self.cameras)

    def _flatten_shell(self):
        """Bake the USD import hierarchy away: unparent the shell meshes (keeping
        world transform) and delete the leftover RoomPlan group empties, leaving a
        flat, readable graph — shell meshes + named asset handles + cameras."""
        shell = get_or_make_collection(COLL_SHELL)
        for o in [
            o
            for o in bpy.data.objects
            if o.type == "MESH" and re.match(r"(Wall|Floor)\d+$", o.name)
        ]:
            mw = o.matrix_world.copy()
            o.parent = None
            o.matrix_world = mw
            move_to_collection(o, shell)

        def managed(o):  # asset-internal empties have a handle ancestor
            p = o
            while p:
                if p.get("room_id"):
                    return True
                p = p.parent
            return False

        for o in list(bpy.data.objects):
            if o.type == "EMPTY" and not managed(o):
                bpy.data.objects.remove(o, do_unlink=True)
        for c in list(bpy.data.collections):
            if not c.objects and not c.children:
                bpy.data.collections.remove(c)

    def _apply_shell_transforms(self):
        """Bake each shell mesh's transform so its local coords equal world metres."""
        shell = [
            o
            for o in bpy.data.objects
            if o.type == "MESH" and re.match(r"(Wall|Floor|Ceiling)\d+$", o.name)
        ]
        if not shell:
            return
        bpy.ops.object.select_all(action="DESELECT")
        for o in shell:
            o.hide_set(False)
            o.select_set(True)
        bpy.context.view_layer.objects.active = shell[0]
        try:
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        except Exception as e:
            print(f"  ! transform_apply on shell failed: {e}")
        bpy.ops.object.select_all(action="DESELECT")

    def _apply_blank_shell(self):
        """The blank baseline: a single neutral-grey material on every Wall*/Floor0,
        no ceiling, no textures. Wall/floor/ceiling materials are the harness's job,
        so this deterministic baseline is deliberately blank + identical across scenes."""
        mat = self._solid_mat("Shell_Blank", BLANK_SHELL_SRGB, rough=0.9)
        n = 0
        for o in bpy.data.objects:
            if o.type == "MESH" and (re.match(r"Wall\d+$", o.name) or o.name == "Floor0"):
                o.data.materials.clear()
                o.data.materials.append(mat)
                n += 1
        return n

    def _solid_mat(self, name, srgb, rough=0.5, metal=0.0):
        mat = bpy.data.materials.get(name)
        if mat is not None:
            return mat
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        b = nt.nodes.new("ShaderNodeBsdfPrincipled")
        nt.links.new(b.outputs["BSDF"], out.inputs["Surface"])
        lin = srgb_to_linear(srgb)
        b.inputs["Base Color"].default_value = (*lin, 1.0)
        b.inputs["Roughness"].default_value = rough
        if "Metallic" in b.inputs:
            b.inputs["Metallic"].default_value = metal
        return mat

    # ---------------------------------------------------------------- index
    def _register(self, rid, cat, meshes, source=None, handle=None,
                  rests_on=None, attached_to=None, support_part=None):
        mn, mx = world_bbox(meshes)
        self.objects[rid] = {
            "id": rid,
            "category": cat,
            "handle": handle or rid,
            "center": [round((mn[i] + mx[i]) / 2, 4) for i in range(3)],
            "bbox_min": [round(mn[i], 4) for i in range(3)],
            "bbox_max": [round(mx[i], 4) for i in range(3)],
            "size": [round(mx[i] - mn[i], 4) for i in range(3)],
            "top_z": round(mx.z, 4),
            "placeable_surface": cat in PLACEABLE_CATS,
            "rests_on": rests_on,
            "attached_to": attached_to,
            "support_part": support_part,
            "source_glb": source,
        }

    def index(self):
        """(Re)build the object registry from the live scene graph."""
        self.objects = {}
        for o in bpy.data.objects:  # shell meshes
            if o.type == "MESH" and re.match(r"(Wall|Floor|Ceiling)\d+$", o.name):
                self._register(o.name, category_of(o.name), [o])
        for o in bpy.data.objects:  # asset handles
            if o.type == "EMPTY" and o.get("room_id"):
                self._register(
                    o["room_id"],
                    o.get("category", category_of(o.name)),
                    mesh_descendants(o),
                    source=o.get("source_glb"),
                    handle=o.name,
                    rests_on=o.get("rests_on"),
                    attached_to=o.get("attached_to"),
                    support_part=o.get("support_part"),
                )
        return self.objects

    def _ground_visual_furniture(self):
        """Snap each FLOOR-STANDING piece's VISIBLE mesh bottom to the floor.
        Vertical-only, so tucked-under chairs stay tucked; wall items keep height."""
        floor = self.objects.get("Floor0")
        if floor is None:
            return 0
        bpy.context.view_layer.update()  # critical: refresh the depsgraph, or we read stale
        # world coordinates from before the parenting
        floor_z = floor["top_z"]
        n = 0
        for rec in self.objects.values():
            if rec.get("category") not in self.GROUND_CATS:
                continue
            h = bpy.data.objects.get(rec["handle"])
            if h is None:
                continue
            meshes = [o for o in h.children_recursive if o.type == "MESH"]
            if not meshes:
                continue
            mn, _ = world_bbox(meshes)
            dz = floor_z - mn.z
            if abs(dz) > 1e-3:
                h.location.z += dz
                n += 1
        bpy.context.view_layer.update()
        return n

    # ---------------------------------------------------------------- queries
    def by_category(self, cat):
        return sorted(r["id"] for r in self.objects.values() if r["category"] == cat)

    def tables(self):
        return self.by_category("table")

    def chairs(self):
        return self.by_category("chair")

    def walls(self):
        return self.by_category("wall")

    def openings(self):
        return sorted(
            r["id"] for r in self.objects.values() if r["category"] in ("door", "window", "opening")
        )

    def placeable_surfaces(self):
        return sorted(r["id"] for r in self.objects.values() if r["placeable_surface"])

    def get(self, rid):
        return self.objects.get(rid)

    def bbox(self, rid):
        r = self.objects[rid]
        return Vector(r["bbox_min"]), Vector(r["bbox_max"])

    def top_surface(self, rid):
        """Top surface of an object, for placing things on it."""
        r = self.objects[rid]
        cx, cy, _ = r["center"]
        return {
            "z": r["top_z"],
            "center": [cx, cy, r["top_z"]],
            "size": [r["size"][0], r["size"][1]],
        }

    # ---------------------------------------------------------------- edits
    def place_on(
        self, target_id, glb_path, offset=(0.0, 0.0), yaw_deg=0.0, max_footprint_frac=0.5, name=None
    ):
        """Place a GLB on top of target_id's surface (bottom rests on the top face),
        centered + offset in the surface plane. Returns the new object id."""
        if target_id not in self.objects:
            raise ValueError(f"unknown object '{target_id}'")
        top = self.top_surface(target_id)
        tx, ty = top["center"][0] + offset[0], top["center"][1] + offset[1]
        tz = top["z"]

        imported = import_glb(glb_path)
        meshes = [o for o in imported if o.type == "MESH"]
        roots = [o for o in imported if o.parent not in imported]
        mn, mx = world_bbox(meshes)
        size = mx - mn
        ctr = (mn + mx) / 2.0

        scale = 1.0
        if max_footprint_frac:
            sw = (top["size"][0] * max_footprint_frac) / max(size.x, 1e-6)
            sd = (top["size"][1] * max_footprint_frac) / max(size.y, 1e-6)
            scale = min(1.0, sw, sd)

        M = (
            Matrix.Translation((tx, ty, tz))
            @ Matrix.Rotation(math.radians(yaw_deg), 4, "Z")
            @ Matrix.Scale(scale, 4)
            @ Matrix.Translation((-ctr.x, -ctr.y, -mn.z))
        )
        for r in roots:
            r.matrix_world = M @ r.matrix_world

        k = self._added_counts.get(target_id, 0)
        self._added_counts[target_id] = k + 1
        rid = name or f"{target_id}_item{k}"
        cat = category_of(os.path.basename(glb_path))
        self._wrap_handle(
            rid,
            cat,
            roots,
            imported,
            get_or_make_collection(COLL_ADDED),
            source=os.path.basename(glb_path),
        )
        bpy.context.view_layer.update()  # settle parenting before reading bbox
        self._register(
            rid,
            cat,
            mesh_descendants(bpy.data.objects[rid]),
            source=os.path.basename(glb_path),
            handle=rid,
        )
        print(f"  placed '{rid}' on '{target_id}' (scale {scale:.2f}) at z={tz:.3f}")
        return rid

    def render_from(self, frame_index, out_path, res_div=1):
        """Render the assembled scene from capture camera <frame_index>."""
        cam = bpy.data.objects.get(f"cam_{frame_index:05d}")
        if cam is None:
            raise SystemExit(f"camera cam_{frame_index:05d} not built")
        sc = bpy.context.scene
        sc.camera = cam
        if self.render_w:
            sc.render.resolution_x, sc.render.resolution_y = self.render_w, self.render_h
        sc.render.resolution_percentage = max(1, int(100 / res_div))
        sc.render.filepath = out_path
        bpy.ops.render.render(write_still=True)
        sc.render.resolution_percentage = 100
        print(f"  rendered cam_{frame_index:05d} -> {out_path}")

    # ---------------------------------------------------------------- output
    def summary(self):
        print("=== ROOM SCENE (blank) ===")
        cats = {}
        for r in self.objects.values():
            cats.setdefault(r["category"], []).append(r["id"])
        for cat in sorted(cats):
            print(f"  {cat:9s}: {', '.join(sorted(cats[cat]))}")
        if self.placeable_surfaces():
            print("  placeable surfaces (top z):")
            for rid in self.placeable_surfaces():
                print(
                    f"    {rid:9s} z={self.objects[rid]['top_z']:.3f} "
                    f"center={self.objects[rid]['center']}"
                )
        print(
            f"  cameras: {len(self.cameras)} (render res {self.render_w}x{self.render_h})"
            if self.cameras
            else "  cameras: none"
        )

    def export_glb(self):
        self.bind_supports()
        self.index()
        os.makedirs(os.path.dirname(self.out_glb), exist_ok=True)
        bpy.ops.object.select_all(action="DESELECT")
        for o in bpy.data.objects:
            if o.type in {"MESH", "EMPTY"} and not o.hide_get():
                o.select_set(True)
        bpy.ops.export_scene.gltf(
            filepath=self.out_glb,
            export_format="GLB",
            use_selection=True,
            export_apply=True,
            export_animations=True,
            export_cameras=False,
            export_lights=False,
        )
        print(f"  EXPORTED -> {self.out_glb} ({os.path.getsize(self.out_glb) // 1024} KB)")

    def bind_supports(self):
        """Carry supported objects with their named support part, keeping the rest pose.

        An articulated support is deliberately NOT guessed: the author must name its
        supporting part (e.g. the moving desktop). The export validator rejects ambiguity.
        """
        handles = {o.get("room_id"): o for o in bpy.data.objects if o.get("room_id")}
        def support_depth(o, seen=None):
            seen=set() if seen is None else seen
            if o.name in seen:raise ValueError('Support cycle: '+o.name)
            target=o.get('rests_on') or o.get('attached_to')
            if target not in handles:return 0
            return 1+support_depth(handles[target],seen|{o.name})
        for child in sorted(handles.values(),key=support_depth):
            target = child.get("rests_on") or child.get("attached_to")
            if not target:
                continue
            support = handles.get(target) or bpy.data.objects.get(target)
            if support is None:
                raise ValueError(f"{child.name}: unknown support {target}")
            part = child.get("support_part")
            parent = bpy.data.objects.get(part) if part else support
            if parent is None or (parent != support and parent not in support.children_recursive):
                raise ValueError(f"{child.name}: support_part {part} is not part of {target}")
            if parent == child or parent in child.children_recursive:
                raise ValueError(f"support cycle involving {child.name}")
            if child.parent == parent:
                continue
            if child.animation_data:
                raise ValueError(f"{child.name}: cannot reparent an independently animated object")
            world = child.matrix_world.copy()
            if child.get("fixture"):
                # glTF nodes have TRS, not Blender's arbitrary parent inverse.
                # Bake each newly authored member into the support's local frame;
                # retain the source hierarchy and animation untouched.
                members=[(m,m.matrix_world.copy()) for m in child.children]
                child.parent=parent
                child.matrix_parent_inverse=Matrix.Identity(4)
                child.matrix_basis=Matrix.Identity(4)
                for m,mworld in members:
                    local=parent.matrix_world.inverted() @ mworld
                    m.matrix_parent_inverse=Matrix.Identity(4)
                    if m.type=='MESH':
                        m.data.transform(local)
                        m.matrix_basis=Matrix.Identity(4)
                    else:
                        m.matrix_basis=local
            else:
                child.parent = parent
                child.matrix_parent_inverse = parent.matrix_world.inverted()
                child.matrix_basis = world
            bpy.context.view_layer.update()

    def export_layout(self, path=None):
        path = path or os.path.join(os.path.dirname(self.out_glb), "room_layout.json")
        mn, mx = world_bbox([o for o in bpy.data.objects if o.type == "MESH"])
        doc = {
            "scene": os.path.basename(self.scan_dir),
            "coordinate_frame": {
                "up": "Z",
                "units": "meters",
                "note": "Blender Z-up; ARKit Y-up mapped by C=Rx(+90).",
            },
            "bounds": {
                "min": [round(v, 4) for v in mn],
                "max": [round(v, 4) for v in mx],
                "size": [round(mx[i] - mn[i], 4) for i in range(3)],
            },
            "collections": {
                "Room_Shell": "walls + floor (blank neutral grey)",
                "Furniture": "tables, chairs, ...",
                "Openings": "doors, windows",
                "ARKit_Cameras": "capture cameras",
                "Added": "objects placed after build (e.g. items on tables)",
            },
            "note": "Blank-sheet assembly: geometry placed on a neutral shell. "
            "Materials, ceiling and wall fixtures are added by the harness.",
            "objects": [self.objects[k] for k in sorted(self.objects)],
            "cameras": self.cameras,
        }
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=2)
        # The generated preview inventory must follow the authored SHELL: a
        # rejected scan opening is not a missing articulated asset at export.
        manifest_path = os.path.join(os.path.dirname(path), "manifest.json")
        if os.path.isfile(manifest_path):
            manifest = json.load(open(manifest_path))
            manifest["assets"] = [a for a in manifest.get("assets", [])
                                  if any(t in self.objects for t in
                                         (a.get("represents_prims") or
                                          [a.get("maps_to_prim") or a.get("object")]))]
            with open(manifest_path, "w") as fh:
                json.dump(manifest, fh, indent=2)
        print(f"  LAYOUT   -> {path} ({len(self.objects)} objects, {len(self.cameras)} cameras)")
        return path

    def export_blend(self, path=None):
        """Save the fully assembled scene as a .blend project, so it opens directly for
        inspection or editing."""
        path = path or os.path.join(os.path.dirname(self.out_glb), "Room.blend")
        try:
            bpy.ops.wm.save_as_mainfile(filepath=path)
            print(f"  BLEND    -> {path} ({os.path.getsize(path) // 1024} KB)")
        except Exception as e:
            print(f"  ! failed to save .blend: {e}")
        return path

    def copy_self(self):
        if SELF_PATH and os.path.exists(SELF_PATH):
            import shutil

            dst = os.path.join(os.path.dirname(self.out_glb), "build_room.py")
            if os.path.abspath(dst) != SELF_PATH:
                shutil.copyfile(SELF_PATH, dst)
                print(f"  COPIED   -> {dst}")


# ============================================================================
# main
# ============================================================================
def main():
    scene = RoomScene().build()
    author_bedroom(scene)
    add_bedroom_details(scene)
    qc_openings(scene)
    finish_photo_repairs(scene)
    scene.summary()
    scene.export_glb()
    scene.export_layout()
    scene.export_blend()
    scene.copy_self()
    print("DONE")
    return scene


# ===========================================================================
# COMPACT SHELL of THIS room — walls / openings / object boxes, fully editable.
# Edit these numbers to edit the scene (see Room.md). _load_shell() reads SHELL first.
# ===========================================================================
# Measured bedroom reconstruction. Coordinates q=(X,Y,height above floor)
# in a frame rotated +10.67 degrees from Blender XY. Evidence in _scratch/run_001.
RA = math.radians(10.67)
RC, RS = math.cos(RA), math.sin(RA)

def rp(x,y,h):
    return Vector((RC*x-RS*y,RS*x+RC*y,SHELL['floor_z']+h))

def rq(p):
    return Vector((RC*p.x+RS*p.y,-RS*p.x+RC*p.y,p.z-SHELL['floor_z']))

def rmat(name,color,rough=.6,metal=0):
    m=bpy.data.materials.new(name);m.diffuse_color=(*srgb_to_linear(color),1);m.use_nodes=True
    p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=m.diffuse_color;p.inputs['Roughness'].default_value=rough;p.inputs['Metallic'].default_value=metal
    return m

def _solid_mat(name, color, rough=.6, metal=0):
    return rmat(name, color, rough, metal)

def _tex_mat(name, slug, color, scale=1., normal_strength=.25, rough=.6):
    """Captured PBR on existing meshes; missing maps use the call site's solid fallback.

    Planar fixtures already have metric UVs. Curved rope/thread and the printed
    cover use explicit scale factors for their existing normalized UVs. Standard
    Mapping nodes preserve this scale in both Blender and the exported glTF.
    """
    paths = {suffix: os.path.join(HERE, 'materials', slug+'_'+suffix+'.jpg')
             for suffix in ('diff', 'rough', 'normal')}
    if not all(os.path.isfile(path) for path in paths.values()):
        return None
    try:
        images = {suffix: bpy.data.images.load(path, check_existing=True)
                  for suffix, path in paths.items()}
        if any(not image.size[0] for image in images.values()):
            return None
    except (OSError, RuntimeError):
        return None
    mat = _solid_mat(name, color, rough)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get('Principled BSDF')
    coord = nodes.new('ShaderNodeTexCoord')
    mapping = nodes.new('ShaderNodeMapping')
    mapping.vector_type = 'POINT'
    mapping.inputs['Scale'].default_value = (scale, scale, 1.) if isinstance(scale, (int, float)) else scale
    links.new(coord.outputs['UV'], mapping.inputs['Vector'])
    for suffix, target in [('diff', 'Base Color'), ('rough', 'Roughness'), ('normal', 'Normal')]:
        tex = nodes.new('ShaderNodeTexImage')
        tex.image = images[suffix]
        links.new(mapping.outputs[0], tex.inputs['Vector'])
        if suffix != 'diff':
            tex.image.colorspace_settings.name = 'Non-Color'
        if suffix == 'normal':
            normal = nodes.new('ShaderNodeNormalMap')
            normal.inputs['Strength'].default_value = normal_strength
            links.new(tex.outputs['Color'], normal.inputs['Color'])
            links.new(normal.outputs['Normal'], bsdf.inputs[target])
        else:
            links.new(tex.outputs['Color'], bsdf.inputs[target])
    return mat

def rpbr(slug,scale=1,rough=None):
    m=rmat(slug,(.7,.7,.7));n=m.node_tree.nodes;l=m.node_tree.links;p=n.get('Principled BSDF')
    uv=n.new('ShaderNodeTexCoord');mapping=n.new('ShaderNodeVectorMath');mapping.operation='SCALE';mapping.inputs[3].default_value=scale;l.new(uv.outputs['UV'],mapping.inputs[0])
    for suffix,target in [('diff','Base Color'),('rough','Roughness'),('normal','Normal')]:
        t=n.new('ShaderNodeTexImage');t.image=bpy.data.images.load(os.path.join(HERE,'materials',slug+'_'+suffix+'.jpg'),check_existing=True);l.new(mapping.outputs[0],t.inputs['Vector'])
        if suffix!='diff':t.image.colorspace_settings.name='Non-Color'
        if suffix=='normal':
            norm=n.new('ShaderNodeNormalMap');norm.inputs['Strength'].default_value=.25;l.new(t.outputs['Color'],norm.inputs['Color']);l.new(norm.outputs[0],p.inputs[target])
        elif suffix!='rough' or rough is None:l.new(t.outputs['Color'],p.inputs[target])
    if rough is not None:p.inputs['Roughness'].default_value=rough
    return m

def ruv(o):
    if o.type!='MESH':return
    uv=o.data.uv_layers.active or o.data.uv_layers.new(name='MetricUV')
    for p in o.data.polygons:
        normal=p.normal;axis=max(range(3),key=lambda i:abs(normal[i]));axes=[i for i in range(3) if i!=axis]
        for li in p.loop_indices:
            v=o.data.vertices[o.data.loops[li].vertex_index].co
            uv.data[li].uv=(v[axes[0]],v[axes[1]])

def rbox(name,center,size,mat,bevel=0):
    bpy.ops.mesh.primitive_cube_add(size=1,location=rp(*center));o=bpy.context.object;o.name=name;o.rotation_euler.z=RA;o.dimensions=size
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    if mat:o.data.materials.append(mat)
    ruv(o)
    if bevel:
        mod=o.modifiers.new('Soft manufactured edges','BEVEL');mod.width=bevel;mod.segments=3
        bpy.context.view_layer.objects.active=o;bpy.ops.object.modifier_apply(modifier=mod.name)
    return o

def rcyl(name,center,radius,depth,mat,axis=(0,0,1),vertices=48):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices,radius=radius,depth=depth,location=rp(*center));o=bpy.context.object;o.name=name
    worldaxis=Vector((RC*axis[0]-RS*axis[1],RS*axis[0]+RC*axis[1],axis[2]));o.rotation_euler=worldaxis.to_track_quat('Z','Y').to_euler()
    if mat:o.data.materials.append(mat)
    for p in o.data.polygons:p.use_smooth=len(p.vertices)==4
    return o

def rball(name,center,size,mat):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32,ring_count=20,location=rp(*center));o=bpy.context.object;o.name=name;o.rotation_euler.z=RA;o.scale=size
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(mat)
    for p in o.data.polygons:p.use_smooth=True
    return o

def rtube(name,coords,r,mat,closed=False):
    # Continuous mesh tube; sewn loops use a cyclic spline without duplicate caps.
    if closed and coords[0]==coords[-1]:coords=coords[:-1]
    cu=bpy.data.curves.new(name,'CURVE');cu.dimensions='3D';cu.resolution_u=2;cu.bevel_depth=r;cu.bevel_resolution=2;cu.use_fill_caps=not closed
    s=cu.splines.new('POLY');s.use_cyclic_u=closed;s.points.add(len(coords)-1)
    for p,co in zip(s.points,coords):p.co=(*rp(*co),1)
    o=bpy.data.objects.new(name,cu);bpy.context.collection.objects.link(o);cu.materials.append(mat)
    bpy.context.view_layer.objects.active=o;o.select_set(True);bpy.ops.object.convert(target='MESH');o.select_set(False);return o

def rlight(name,pos,energy,color,size,target):
    d=bpy.data.lights.new(name,'AREA');d.energy=energy;d.color=color;d.shape='DISK';d.size=size;d.specular_factor=0.0
    o=bpy.data.objects.new(name,d);bpy.context.collection.objects.link(o);o.location=rp(*pos);o.visible_glossy=False;o.visible_camera=False;o.rotation_euler=(rp(*target)-o.location).to_track_quat('-Z','Y').to_euler();return o

def repair_scan_islands(o):
    # Preserve all triangles: seat isolated neural reconstruction flecks onto the
    # nearest main upholstery surface, then weld only coincident seam vertices.
    import bmesh
    from mathutils.kdtree import KDTree
    bm=bmesh.new();bm.from_mesh(o.data);bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=.00003)
    bm.verts.ensure_lookup_table();seen=set();islands=[]
    for v in bm.verts:
        if v in seen:continue
        stack=[v];seen.add(v);part=[]
        while stack:
            u=stack.pop();part.append(u)
            for e in u.link_edges:
                w=e.other_vert(u)
                if w not in seen:seen.add(w);stack.append(w)
        islands.append(part)
    islands.sort(key=len,reverse=True);main=islands[0];kd=KDTree(len(main))
    for i,v in enumerate(main):kd.insert(v.co,i)
    kd.balance();repairs=[]
    for island in islands[1:]:
        if len(island)>250:continue
        d,v,near=min((kd.find(v.co)[2],v,kd.find(v.co)[0]) for v in island)
        if d<.12:
            delta=near-v.co
            for vv in island:vv.co+=delta
            repairs.append((len(island),round(d,5)))
    bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=.00005);bm.to_mesh(o.data);bm.free();print('BED_SCAN_SEAM_REPAIRS',repairs)

def author_bedroom(scene):
    white=rmat('Warm white smooth emulsion',(.92,.91,.89),.84)
    ceiling=rmat('Ceiling smooth white paint',(.93,.925,.91),.85)
    black=rmat('Charcoal skirting and rubber',(.045,.042,.038),.5)
    bronze=rmat('Satin champagne socket plates',(.50,.46,.37),.36,.45)
    plastic=rmat('Warm white appliance plastic',(.88,.87,.83),.34)
    steel=rmat('Fan grille dark steel',(.39,.38,.35),.4,.65)
    # Frames 15/53/55/106: satin floor and polished stone retain directional
    # window reflections. Keep the saved albedo pixels and all support meshes.
    floor=rpbr('bedroom_oak_floor',1.5,rough=.26)
    # Round-02 frames 55/67: reduce broken sky highlights while preserving
    # the photographed rail reflections and the saved marble albedo pixels.
    stone=rpbr('window_marble',.75,rough=.075)
    linen=rpbr('taupe_curtains',3)
    for material,normal_strength,coat,coat_rough in [(floor,.16,.18,.20),(stone,.012,.12,.06)]:
        bsdf=material.node_tree.nodes.get('Principled BSDF')
        bsdf.inputs['IOR'].default_value=1.5
        bsdf.inputs['Coat Weight'].default_value=coat
        bsdf.inputs['Coat Roughness'].default_value=coat_rough
        for node in material.node_tree.nodes:
            if node.type=='NORMAL_MAP':node.inputs['Strength'].default_value=normal_strength
    for o in bpy.data.objects:
        if re.match(r'Wall\d+$',o.name):o.data.materials.clear();o.data.materials.append(white)
        if o.name=='Floor0':
            o.data.materials.clear();o.data.materials.append(floor)
            uv=o.data.uv_layers.active or o.data.uv_layers.new(name='FloorMetric')
            for p in o.data.polygons:
                for li in p.loop_indices:
                    q=rq(o.matrix_world@o.data.vertices[o.data.loops[li].vertex_index].co);uv.data[li].uv=(q.x,q.y)
    # Structural ceiling uses the measured floor outline and ceiling elevation.
    floorob=bpy.data.objects['Floor0'];co=floorob.copy();co.data=floorob.data.copy();bpy.context.collection.objects.link(co);co.name='Ceiling0'
    for v in co.data.vertices:v.co.z=SHELL['ceiling_z']+(SHELL['floor_z']-v.co.z)*800
    co.data.materials.clear();co.data.materials.append(ceiling)
    # All source object supports are explicit; no source nodes or joints are discarded.
    for nm in ['Bed0','Storage0','Storage1']:bpy.data.objects[nm]['rests_on']='Floor0'
    for nm,wall in [('Door0','Wall2'),('Door2','Wall6'),('Window0','Wall3')]:bpy.data.objects[nm]['attached_to']=wall
    # Depth frame 74: cabinet width ~.27 m; cubby .423 m; rear shelf .532 m.
    for ob in mesh_descendants(bpy.data.objects['Storage1']):
        invm=ob.matrix_world.inverted()
        for v in ob.data.vertices:
            q=rq(ob.matrix_world@v.co)
            if .06<q.z<=.35:q.z=.06+(q.z-.06)*(.42-.06)/(.35-.06)
            elif .35<q.z<.543:q.z=.42+(q.z-.35)*(.543-.42)/(.543-.35)
            v.co=invm@rp(*q)
    repair_scan_islands(bpy.data.objects['geometry_0'])
    bedmesh=bpy.data.objects['geometry_0'];bi=bedmesh.matrix_world.inverted()
    for v in bedmesh.data.vertices:
        q=rq(bedmesh.matrix_world@v.co)
        # Mattress .61 m and base .30 m; only the rear headboard reaches 1.34 m.
        q.z=q.z*.55 if q.z<=1.14 else .627+(q.z-1.14)*3.565
        # Frame 88/74 side probes: mattress spans local X 1.868..3.681 m.
        blend=max(0.,min(1.,(q.z-.62)/.15))
        q.x=1.86+(q.x-1.86)*(.9479167+.0520833*blend)
        if q.z>1.285:q.z=1.285
        v.co=bi@rp(*q)

    leather=rpbr('ivory_leather',4,rough=.57)
    bedmesh.data.materials.append(leather);mi=len(bedmesh.data.materials)-1
    for poly in bedmesh.data.polygons:
        points=[rq(bedmesh.matrix_world@bedmesh.data.vertices[j].co) for j in poly.vertices]
        if (sum(q.z for q in points)/len(points)>.59 and min(q.y for q in points)>3.90) or max(q.z for q in points)<.30:poly.material_index=mi
    sheet=rpbr('pink_sheet',.6,rough=.8)
    for node in sheet.node_tree.nodes:
        if node.type=='TEX_IMAGE' and node.image and node.image.name.startswith('pink_sheet_normal'):
            node.image=bpy.data.images.load(os.path.join(HERE,'materials','pink_sheet_capture_normal.png'),check_existing=True)
            node.image.colorspace_settings.name='Non-Color'
        if node.type=='TEX_IMAGE' and node.image and node.image.name.startswith('pink_sheet_diff'):
            node.image=bpy.data.images.load(os.path.join(HERE,'materials','pink_sheet_captured_wrinkles.jpg'),check_existing=True)
        if node.type=='NORMAL_MAP':node.inputs['Strength'].default_value=1.2
    uvn=sheet.node_tree.nodes.new('ShaderNodeUVMap');uvn.uv_map='BedMetric'
    for node in sheet.node_tree.nodes:
        if node.type=='VECT_MATH':sheet.node_tree.links.new(uvn.outputs['UV'],node.inputs[0])
    metric=bedmesh.data.uv_layers.new(name='BedMetric')
    bedmesh.data.materials.append(sheet);si=len(bedmesh.data.materials)-1
    for poly in bedmesh.data.polygons:
        points=[rq(bedmesh.matrix_world@bedmesh.data.vertices[j].co) for j in poly.vertices]
        if min(q.z for q in points)>.30 and max(q.z for q in points)<.64 and min(q.y for q in points)<3.95:poly.material_index=si
        for li in poly.loop_indices:
            q=rq(bedmesh.matrix_world@bedmesh.data.vertices[bedmesh.data.loops[li].vertex_index].co)
            metric.data[li].uv=(q.x,q.y)
    # The source window includes sill, curtains and railing. Extend its actual marble
    # slab to the measured 0.62 m depth, leaving its articulated sashes intact.
    win=bpy.data.objects['Window_Static_Frame_Sill_Rail_Curtains'];inv=win.matrix_world.inverted()
    slots={s.name:i for i,s in enumerate(win.material_slots)}
    for name,mat in [('Pale_Marble_Sill',stone),('Beige_Linen_Curtain',linen),('Warm_Offwhite_Plaster',white)]:
        if name in slots:win.material_slots[slots[name]].material=mat
    ids=set(j for p in win.data.polygons if p.material_index==slots['Pale_Marble_Sill'] for j in p.vertices)
    qs=[rq(win.matrix_world@win.data.vertices[j].co) for j in ids];x0=min(q.x for q in qs);x1=max(q.x for q in qs)
    for j in ids:
        q=rq(win.matrix_world@win.data.vertices[j].co);q.x=3.965+(q.x-x0)/(x1-x0)*.62;win.data.vertices[j].co=inv@rp(*q)
    # Diffuse veining rectified from capture 58; polish settings are set above.
    for node in stone.node_tree.nodes:
        if node.type=='TEX_IMAGE' and node.image and node.image.name.startswith('window_marble_diff'):
            node.image=bpy.data.images.load(os.path.join(HERE,'materials','photographed_marble_veins.jpg'),check_existing=True)
    uv=win.data.uv_layers.active
    for poly in win.data.polygons:
        if poly.material_index==slots['Pale_Marble_Sill']:
            for li in poly.loop_indices:
                q=rq(win.matrix_world@win.data.vertices[win.data.loops[li].vertex_index].co)
                uv.data[li].uv=((q.y-1.315)/1.4,(q.x-3.965)/.62)
    sill_top=max(rq(win.matrix_world@win.data.vertices[j].co).z for j in ids);sill_bottom=min(rq(win.matrix_world@win.data.vertices[j].co).z for j in ids)
    # Plastered masonry under the deep sill: a real support reaching the floor.
    base=rbox('Window bench plastered masonry',(4.275,2.692,sill_bottom/2),(.62,2.935,sill_bottom),white)
    group_fixture('WindowBench','built_in',[base],rests_on='Floor0')
    # The photographed drapes are full gathered fabric, not flat slatted panels.
    # Preserve the top attachment to the source track and relax the folds below it.
    ci=slots['Beige_Linen_Curtain']
    cids=set(j for p in win.data.polygons if p.material_index==ci for j in p.vertices)
    cqs=[rq(win.matrix_world@win.data.vertices[j].co) for j in cids]
    ctop=max(q.z for q in cqs);cbot=min(q.z for q in cqs)
    xmean=sum(q.x for q in cqs)/len(cqs)
    for j in cids:
        q=rq(win.matrix_world@win.data.vertices[j].co)
        f=max(0.,min(1.,(ctop-q.z)/(ctop-cbot)))**.45
        q.x=xmean+(q.x-xmean)*(1+(1.0 if q.y<2.69 else 1.5)*f)-(.01 if q.y<2.69 else .10)*f
        q.y+=.035*f if q.y<2.69 else -.035*f
        win.data.vertices[j].co=inv@rp(*q)
    # Seat the source fixed glazing into its existing rubber bead (5 mm correction).
    glassids=set(j for p in win.data.polygons if p.material_index==slots['Clear_Glazing'] for j in p.vertices)
    for j in glassids:
        q=rq(win.matrix_world@win.data.vertices[j].co);q.x-=.005;win.data.vertices[j].co=inv@rp(*q)
    # Thin black skirting, interrupted at the real door openings.
    for wall,w in SHELL['walls'].items():
        a=Vector(w['start']);b=Vector(w['end']);L=(b-a).length;u=(b-a)/L
        n=Vector((-u.y,u.x));mid=(a+b)/2
        # Use the shell's outward extrusion convention to keep the trim inside.
        cw=collision_walls(SHELL)[wall];lo,hi=cw['solid_interval'];inside=-1 if abs(hi)>abs(lo) else 1
        spans=[(0,L)]
        for op in SHELL['openings'].values():
            if op['wall']==wall and op['type']=='door':
                l=op['offset']-op['width']/2-.03;r=op['offset']+op['width']/2+.03
                spans=[(aa,bb) for s,e in spans for aa,bb in [(s,min(e,l)),(max(s,r),e)] if bb>aa]
        parts=[]
        for i,(s,e) in enumerate(spans):
            c=a+u*((s+e)/2)+n*(inside*.006)
            ob=rbox(wall+' skirting '+str(i),(0,0,.018),(e-s,.012,.036),black)
            ob.location=Vector((c.x,c.y,SHELL['floor_z']+.018));ob.rotation_euler.z=math.atan2(u.y,u.x);parts.append(ob)
        if parts:group_fixture(wall+'Skirting','trim',parts,attached_to=wall)
    # Tray ceiling, photographed at 2.38 m underside, 2.80 m center ceiling.
    for name,c,s in [('HeadboardSoffit',(2.954,3.96,2.59),(3.263,.4,.46)),('WindowSoffit',(4.27,2.682,2.59),(.63,2.156,.46)),('BedsideSoffit',(2.954,1.414,2.59),(3.263,.38,.46)),('WardrobeSoffit',(1.52,2.682,2.59),(.395,2.156,.46)),('HallCeiling',(.485,1.745,2.59),(1.665,1.077,.46))]:
        ob=rbox(name+' plaster',c,s,ceiling);group_fixture(name,'ceiling_soffit',[ob],attached_to='Ceiling0')
    # Small stepped lip at the inside of the tray, fixed directly to each soffit.
    for i,(c,s,sup) in enumerate([((2.95,3.752,2.405),(2.46,.018,.035),'HeadboardSoffit'),((3.946,2.69,2.405),(.018,2.12,.035),'WindowSoffit'),((2.95,1.613,2.405),(2.46,.018,.035),'BedsideSoffit'),((1.726,2.69,2.405),(.018,2.12,.035),'WardrobeSoffit')]):
        group_fixture('TrayLip'+str(i),'moulding',[rbox('Tray lip '+str(i),c,s,ceiling)],attached_to=sup)
    # Main round luminaire, visible and on in the scan.
    emit=rmat('Warm LED opal diffuser',(.98,.95,.86),.3);p=emit.node_tree.nodes.get('Principled BSDF');p.inputs['Emission Color'].default_value=(1,.82,.59,1);p.inputs['Emission Strength'].default_value=.25
    parts=[rcyl('Ceiling luminaire mount',(2.57,2.28,2.79),.20,.06,plastic),rcyl('Ceiling luminaire opal',(2.57,2.28,2.755),.195,.01,emit)]
    parts.append(rlight('Ceiling LED 3200K',(2.57,2.28,2.74),8,(1,.96,.90),.65,(2.57,2.28,0)))
    group_fixture('CeilingLight','light',parts,attached_to='Ceiling0')
    # Frames 27/62/133: the two headboard downlights are unlit gray recesses.
    # Keep their existing meshes and soffit contacts; decouple them from the
    # main luminaire's emissive opal material and retain the inactive light nodes.
    unlit_lens=rmat('Headboard downlight unlit lens',(.48,.47,.45),.42)
    unlit_trim=rmat('Headboard downlight painted trim',(.80,.79,.77),.55)
    for i,(x,y,sup) in enumerate([(2.1,3.96,'HeadboardSoffit'),(3.35,3.96,'HeadboardSoffit'),(2.1,1.42,'BedsideSoffit'),(3.35,1.42,'BedsideSoffit'),(.4,1.74,'HallCeiling')]):
        parts=[rcyl('Downlight trim '+str(i),(x,y,2.357),.043,.008,unlit_trim if i<2 else plastic),rcyl('Downlight lens '+str(i),(x,y,2.352),.027,.002,unlit_lens if i<2 else emit)]
        parts.append(rlight('Recessed LED '+str(i),(x,y,2.347),0.0 if i<2 else .35,(1,.97,.93),.12,(x,y,0)))
        group_fixture('Downlight'+str(i),'light',parts,attached_to=sup)
    # Round-02 pairs 0/27/62/74/76/78: lift neutral diffuse daylight while
    # keeping the stronger window key responsible for the headboard gradient.
    # The LDR exterior image is a view plate, not calibrated HDR
    # illumination; its cyan ground strip should not tint every diffuse surface.
    # Keep the saved image for camera/glossy rays and correct only its diffuse
    # lighting contribution. No replacement exterior imagery or emissive cards.
    world=bpy.context.scene.world;world.use_nodes=True
    wn=world.node_tree.nodes;wl=world.node_tree.links
    background=wn.get('Background')
    background.inputs['Strength'].default_value=1.0
    env=wn.new('ShaderNodeTexEnvironment')
    env.image=bpy.data.images.load(os.path.join(HERE,'materials','captured_exterior_environment.jpg'),check_existing=True)
    wl.new(env.outputs['Color'],background.inputs['Color'])
    daylight_color=wn.new('ShaderNodeHueSaturation')
    daylight_color.label='Neutralize LDR cyan cast in diffuse daylight only'
    daylight_color.inputs['Saturation'].default_value=.15
    wl.new(env.outputs['Color'],daylight_color.inputs['Color'])
    daylight_tint=wn.new('ShaderNodeMixRGB');daylight_tint.blend_type='MULTIPLY'
    daylight_tint.inputs[0].default_value=1.0
    daylight_tint.inputs[2].default_value=(1.0,.98,.95,1.0)
    wl.new(daylight_color.outputs['Color'],daylight_tint.inputs[1])
    diffuse_sky=wn.new('ShaderNodeBackground');diffuse_sky.inputs['Strength'].default_value=.80
    wl.new(daylight_tint.outputs['Color'],diffuse_sky.inputs['Color'])
    path=wn.new('ShaderNodeLightPath');mix=wn.new('ShaderNodeMixShader')
    wl.new(path.outputs['Is Diffuse Ray'],mix.inputs[0])
    wl.new(background.outputs['Background'],mix.inputs[1])
    wl.new(diffuse_sky.outputs['Background'],mix.inputs[2])
    wl.new(mix.outputs[0],wn.get('World Output').inputs['Surface'])
    # Duct outlet and its photographed white air deflector above wardrobe.
    parts=[rbox('Duct outlet backing',(1.724,2.59,2.58),(.012,.92,.22),black)]
    for y in [2.13,3.05]:parts.append(rbox('Duct edge',(1.731,y,2.58),(.025,.018,.25),plastic))
    for h in [2.47,2.69]:parts.append(rbox('Duct rail',(1.731,2.59,h),(.025,.92,.018),plastic))
    for j in range(7):parts.append(rbox('Duct louvre '+str(j),(1.733,2.59,2.49+j*.028),(.025,.89,.009),bronze))
    parts.append(rbox('Air deflector',(1.765,2.73,2.59),(.045,.64,.19),plastic,.018))
    group_fixture('AirConditionerOutlet','vent',parts,attached_to='WardrobeSoffit')
    # Sockets/switches: faceplates contact wall; visible inserts contact faceplates.
    def socket(name,x,y,h,wall,front,width=.086,kind='socket'):
        # Wall0: front -Y; Wall5: +Y; Wall2: -Y.
        plate=rbox(name+' plate',(x,y,h),(width,.012,.086),plastic if kind=='switch' else bronze,.004);parts=[plate]
        for dx in ([-width*.25,width*.25] if width>.12 else [0]):
            if kind=='switch':parts.append(rbox(name+' rocker',(x+dx,y+front*.008,h),(.032,.006,.064),plastic,.002))
            else:
                for sx,sz in [(-.013,.006),(.013,.006),(0,-.017)]:parts.append(rbox(name+' receptacle',(x+dx+sx,y+front*.0065,h+sz),(.004,.001,.010),black))
        group_fixture(name,'electrical',parts,attached_to=wall)
    socket('HeadboardSocketLeft',1.51,4.153,.80,'Wall0',-1,.17)
    socket('HeadboardSocketRight',3.75,4.153,.78,'Wall0',-1)
    socket('BedChargerSocket',3.75,4.153,.38,'Wall0',-1)
    socket('FanWallSocket',2.48,1.222,.88,'Wall5',1)
    socket('LowPowerCluster',2.53,1.222,.28,'Wall5',1,.26)
    socket('EntranceSwitches',.92,2.2778,1.36,'Wall2',-1,.086,'switch')
    socket('OppositeSwitches',.82,1.213,1.23,'Wall5',1,.17,'switch')
    # Pedestal fan: measured 1.21 m overall height, 0.29 m head, 0.30 m base.
    parts=[rcyl('Fan weighted base',(3.29,1.45,.018),.155,.036,plastic),rcyl('Fan stem',(3.29,1.45,.47),.013,.904,plastic)]
    parts.append(rbox('Fan control neck',(3.29,1.45,.925),(.042,.042,.10),plastic,.009))
    parts.append(rcyl('Fan motor housing',(3.27,1.45,1.075),.137,.105,plastic,axis=(1,0,0)))
    # Open grille rings and ribs, all touching the housing rim.
    axisx=3.213
    for j in range(1,37):
        r=.137*j/37
        coords=[(axisx,1.45+r*math.cos(t),1.075+r*math.sin(t)) for t in [k*2*math.pi/64 for k in range(65)]]
        parts.append(rtube('Fan concentric grille '+str(j),coords,.00065,steel))
    for j in range(36):
        t=j*math.pi/18;parts.append(rtube('Fan grille spoke '+str(j),[(axisx,1.45,1.075),(axisx,1.45+.137*math.cos(t),1.075+.137*math.sin(t))],.0008,steel))
    parts.append(rcyl('Fan centre badge',(axisx-.002,1.45,1.075),.022,.006,plastic,axis=(1,0,0)))
    parts.append(rcyl('Fan dark blade cavity',(3.216,1.45,1.075),.129,.003,rmat('Fan blade shadow',(.48,.47,.44),.6),axis=(1,0,0)))
    parts.append(rtube('Fan rolled white grille rim',[(3.214,1.45+.139*math.cos(k*2*math.pi/80),1.075+.139*math.sin(k*2*math.pi/80)) for k in range(81)],.005,plastic))
    # Frame 52 shows the head directed diagonally into the room, with a slight upward tilt.
    parts.append(rcyl('Fan rear vent backing',(3.324,1.45,1.075),.105,.003,steel,axis=(1,0,0)))
    for j in range(-11,12):
        z=j*.008;half=math.sqrt(max(.001,.105**2-z*z))
        parts.append(rbox('Fan rear vent bar '+str(j),(3.327,1.45,1.075+z),(.004,2*half,.004),plastic))
    # Side-wall ventilation slots follow the real drum surface; each is seated
    # directly on the housing, and rotates with the existing head assembly.
    for j in range(52):
        t=j*2*math.pi/52
        yy=1.45+.1374*math.cos(t);zz=1.075+.1374*math.sin(t)
        parts.append(rtube('Fan housing ventilation slot '+str(j),[(3.284,yy,zz),(3.313,yy,zz)],.0016,steel))
    pivot=rp(3.27,1.45,1.075)
    turn=Matrix.Translation(pivot) @ Matrix.Rotation(math.pi/4,4,'Z') @ Matrix.Rotation(.18,4,'Y') @ Matrix.Translation(-pivot)
    for ob in parts[3:]:ob.matrix_world=turn@ob.matrix_world
    parts.append(rcyl('Fan visible tilt hinge',(3.29,1.45,.963),.033,.067,plastic,axis=(0,1,0)))
    parts.append(rcyl('Fan tilt hinge screw',(3.29,1.415,.963),.010,.004,steel,axis=(0,1,0)))
    parts.append(rbox('Fan control sleeve',(3.29,1.45,.79),(.028,.037,.24),plastic,.008))
    parts.append(rbox('Fan control label',(3.29,1.430,.82),(.018,.002,.055),bronze,.002))
    group_fixture('PedestalFan','fan',parts,rests_on='Floor0')
    # Cable goes down the fan stem, along floor to a wall adapter.
    parts=[rbox('Fan wall adapter',(2.59,1.248,.28),(.042,.04,.074),plastic,.005)]
    parts.append(rtube('Fan mains cord',[(2.59,1.27,.25),(2.62,1.29,.10),(2.78,1.30,.006),(3.12,1.35,.006),(3.31,1.45,.03)],.003,black))
    group_fixture('FanPowerLead','cable',parts,attached_to='LowPowerCluster')
    # Sill notebook and white remote, each grounded to its true support mesh.
    bookcloth=rmat('Pale warm notebook cover',(.81,.80,.73),.62)
    book=rbox('Dark notebook',(4.20,3.76,sill_top+.011),(.22,.155,.022),bookcloth,.003);book.rotation_euler.z+=.15
    group_fixture('SillNotebook','book',[book],rests_on='Window0',support_part=win.name)
    remote=rbox('Remote control',(4.20,3.76,sill_top+.033),(.13,.042,.022),plastic,.009);remote.rotation_euler.z+=.15
    parts=[remote]
    for j in range(4):parts.append(rbox('Remote button '+str(j),(4.163+j*.023,3.76,sill_top+.045),(.012,.016,.002),bronze,.001))
    group_fixture('SillRemote','remote',parts,rests_on='SillNotebook')
    # Open fabric basket on the marble, with an actual cavity and rope handles.
    canvas=rpbr('yellow_canvas',4);parts=[rcyl('Basket bottom',(4.29,1.58,sill_top+.006),.157,.012,canvas)]
    N=64;vs=[];fs=[]
    for rad,h in [(.157,sill_top+.006),(.169,sill_top+.365),(.164,sill_top+.365),(.152,sill_top+.012)]:
        vs.extend([tuple(rp(4.29+rad*math.cos(i*2*math.pi/N),1.58+rad*math.sin(i*2*math.pi/N),h)) for i in range(N)])
    for k in range(3):
        for i in range(N):j=(i+1)%N;fs.append((k*N+i,k*N+j,(k+1)*N+j,(k+1)*N+i))
    me=bpy.data.meshes.new('Sewn basket wall');me.from_pydata(vs,[],fs);me.update();ob=bpy.data.objects.new('Yellow canvas basket wall',me);bpy.context.collection.objects.link(ob);me.materials.append(canvas);ruv(ob);parts.append(ob)
    rope=(_tex_mat('Ivory cotton rope','basket_cotton_rope',(.88,.87,.83),scale=(1.89,.296,1.),normal_strength=.3)
          or _solid_mat('Ivory cotton rope',(.88,.87,.83),.34))
    for sy in [-1,1]:
        coords=[(4.29+sy*(.155+.035*math.sin(t)),1.58+.065*math.cos(t),sill_top+.30-.08*math.sin(t)) for t in [i*math.pi/24 for i in range(25)]]
        parts.append(rtube('Basket rope handle',coords,.007,rope))
    lining=(_tex_mat('Basket warm ivory fabric lining','basket_ivory_lining',(.69,.66,.57),scale=4,normal_strength=.25)
            or _solid_mat('Basket warm ivory fabric lining',(.69,.66,.57),.95))
    me.materials.append(lining)
    for poly in me.polygons:
        if poly.index>=2*N:poly.material_index=1
    parts.append(rtube('Basket sewn rolled rim',[(4.29+.167*math.cos(k*2*math.pi/80),1.58+.167*math.sin(k*2*math.pi/80),sill_top+.365) for k in range(81)],.004,canvas))
    group_fixture('YellowBasket','basket',parts,rests_on='Window0',support_part=win.name)
    bpy.context.scene.render.engine='CYCLES';bpy.context.scene.cycles.samples=32
    bpy.context.scene.view_settings.view_transform='AgX'
    bpy.context.scene.view_settings.exposure=0.0
    bpy.context.view_layer.update();scene.index()


def qc_take_faces(source, name, face_indices):
    """Separate existing parts, preserving their UVs and captured PBR materials."""
    import bmesh
    keep=set(face_indices)
    ob=source.copy();ob.data=source.data.copy();ob.name=name
    ob.animation_data_clear();bpy.context.collection.objects.link(ob)
    ob.parent=source;ob.matrix_parent_inverse=Matrix.Identity(4);ob.matrix_basis=Matrix.Identity(4)
    bpy.context.view_layer.update()
    for mesh,remove in [(ob.data,lambda i:i not in keep),(source.data,lambda i:i in keep)]:
        bm=bmesh.new();bm.from_mesh(mesh);bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm,geom=[f for f in bm.faces if remove(f.index)],context='FACES')
        bm.to_mesh(mesh);bm.free();mesh.update()
    return ob


def qc_openings(scene):
    """Photo-supported corrections only; no furniture or wall plane is moved."""
    import bmesh
    bpy.context.scene.frame_set(0);bpy.context.view_layer.update()
    # Frames 0, 6 and 125 show ONE entrance leaf parked against Wall4.
    # There is no second portal through Wall4. Door2 keeps its source jambs
    # and butt hinges; replace only the incorrect recessed pull/flush panel.
    leaf=next(o for o in mesh_descendants(bpy.data.objects['Door2']) if o.animation_data)
    wood=next(m for m in leaf.data.materials if m.name.startswith('Oak_Veneer_Honey_Leaf'))
    wi=list(leaf.data.materials).index(wood)
    ids={j for p in leaf.data.polygons if p.material_index==wi for j in p.vertices}
    qs=[rq(leaf.matrix_world@leaf.data.vertices[j].co) for j in ids]
    lo=Vector([min(q[i] for q in qs) for i in range(3)])
    hi=Vector([max(q[i] for q in qs) for i in range(3)])
    old={i for i,m in enumerate(leaf.data.materials) if m==wood or 'Pull' in m.name or 'Cup_Shadow' in m.name}
    bm=bmesh.new();bm.from_mesh(leaf.data)
    bmesh.ops.delete(bm,geom=[f for f in bm.faces if f.material_index in old],context='FACES')
    bm.to_mesh(leaf.data);bm.free()
    metal=rmat('Entrance satin bronze lever',(.30,.28,.23),.30,.85)
    groove=rmat('Entrance recessed oak inlay',(.23,.20,.15),.55)
    mid=(lo+hi)/2;sz=hi-lo;gx=hi.x-.135;gap=.004
    parts=[]
    for a,b in [(lo.x,gx-gap/2),(gx+gap/2,hi.x)]:
        panel=rbox('Entrance oak leaf panel',((a+b)/2,mid.y,mid.z),(b-a,sz.y,sz.z),wood,.001)
        # Same metric veneer UV scale as the finalized entrance-door source.
        uv=panel.data.uv_layers.active
        for poly in panel.data.polygons:
            for li in poly.loop_indices:
                co=leaf.parent.matrix_world.inverted()@panel.matrix_world@panel.data.vertices[panel.data.loops[li].vertex_index].co
                uv.data[li].uv=(.46+co.x*1.3,.18+co.z*.22)
        parts.append(panel)
    parts.append(rbox('Entrance narrow recessed inlay',(gx,mid.y,mid.z),(gap,sz.y-.006,sz.z),groove))
    hx=hi.x-.065
    for side in [-1,1]:
        yy=lo.y if side<0 else hi.y
        parts.append(rcyl('Entrance lever rose',(hx,yy+side*.003,1.00),.018,.006,metal,axis=(0,1,0)))
        parts.append(rcyl('Entrance lever neck',(hx,yy+side*.019,1.00),.007,.028,metal,axis=(0,1,0)))
        parts.append(rtube('Entrance lever grip',[(hx,yy+side*.034,1.00),(hx-.087,yy+side*.034,1.00)],.006,metal))
        parts.append(rcyl('Entrance key escutcheon',(hx,yy+side*.003,.925),.015,.006,metal,axis=(0,1,0)))
    for ob in parts:
        ob.parent=leaf;ob.matrix_parent_inverse=leaf.matrix_world.inverted()
        move_to_collection(ob,get_or_make_collection(COLL_OPEN))
    # Photographed rest is open. The clip includes both fully closed and open
    # poses, using the original vertical hinge, with no detached hardware.
    leaf.animation_data_clear();leaf.rotation_mode='XYZ'
    for frame,angle in [(0,-90),(1,-90),(40,0),(80,-90),(120,-90)]:
        leaf.rotation_euler.z=math.radians(angle)
        leaf.keyframe_insert(data_path='rotation_euler',index=2,frame=frame)
    leaf['hinge_side']='left from outside hallway; opens into room'
    leaf['reference_pose']='open against Wall4, frames 0/6/125'
    leaf.animation_data.action.name='Entrance_Open_Closed_Open'

    win=bpy.data.objects['Window_Static_Frame_Sill_Rail_Curtains']
    cy=rq(win.matrix_world.translation).y
    # Stitch: central picture light ~1.98 m, matching narrow side casements.
    # Keep the outer reveal, marble bench and SHELL opening in place.
    def width_map(y):
        d=abs(y-cy);sign=1 if y>=cy else -1
        knots=[(0,0),(.7803,.995),(.965,1.175),(1.291,1.291)]
        for (a,b),(c,dst) in zip(knots,knots[1:]):
            if d<=c:return cy+sign*(b+(d-a)*(dst-b)/(c-a))
        return y
    def head_map(z):
        return 1.84+(z-1.84)*(.515/.415) if z>1.84 else z
    sashes=[o for o in win.children if o.name.startswith('Casement_')]
    for ob in [win]+sashes:
        inv=ob.matrix_world.inverted()
        eligible={j for p in ob.data.polygons
                  if any(t in ob.data.materials[p.material_index].name for t in
                         ('Aluminium','Rubber_Gasket','Clear_Glazing')) for j in p.vertices}
        for j in eligible:
            q=rq(ob.matrix_world@ob.data.vertices[j].co)
            # The separate curtain track is the highest aluminium component.
            if ob==win and q.z>2.27:
                q.y=cy+(q.y-cy)*1.13;q.x-=.12;q.z+=.074
            else:q.y=width_map(q.y);q.z=head_map(q.z)
            ob.data.vertices[j].co=inv@rp(*q)
        for m in ob.data.materials:
            if 'Powdercoated_Aluminium' in m.name:
                m.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value=.9
            if 'Clear_Glazing' in m.name:
                p=m.node_tree.nodes.get('Principled BSDF')
                p.inputs['Transmission Weight'].default_value=1
                p.inputs['Roughness'].default_value=.008
                p.inputs['Base Color'].default_value=(.98,.99,1,1)
                if hasattr(m,'use_raytrace_refraction'):m.use_raytrace_refraction=True
    # Outer-edge hinges leave the inner handles where the photographs show them.
    # The existing opposite-handed clips now swing outward, clear of the guard.
    for ob in sashes:
        mw=ob.matrix_world.copy();origin=rq(mw.translation)
        side=1 if origin.y>cy else -1
        inner=width_map(origin.y);outer=cy+side*1.175
        # Move the two little hinge barrels, preserving the handle assemblies.
        bm=bmesh.new();bm.from_mesh(ob.data);seen=set()
        for v in bm.verts:
            if v in seen:continue
            stack=[v];seen.add(v);component=[]
            while stack:
                vv=stack.pop();component.append(vv)
                for e in vv.link_edges:
                    w=e.other_vert(vv)
                    if w not in seen:seen.add(w);stack.append(w)
            qs=[rq(mw@v.co) for v in component]
            span=max(q.z for q in qs)-min(q.z for q in qs)
            if .060<span<.10 and abs(sum(q.y for q in qs)/len(qs)-inner)<.018:
                delta=mw.inverted().to_3x3()@(rp(0,outer-inner,0)-rp(0,0,0))
                for v in component:v.co+=delta
        bm.to_mesh(ob.data);bm.free()
        origin.y=outer;newmw=mw.copy();newmw.translation=rp(*origin)
        ob.data.transform(newmw.inverted()@mw);ob.matrix_world=newmw
        ob['mechanism']='outer-edge side-hung outward casement; inner-edge handle'
        # Imported independent NLA strips can leave one sash muted. Give each
        # leaf an explicit, matching closed/open clip at its corrected pivot.
        ob.animation_data_clear();ob.rotation_mode='XYZ'
        for frame,f in [(0,0),(1,0),(40,1),(80,1),(120,0)]:
            ob.rotation_euler.z=side*math.radians(60)*f
            ob.keyframe_insert(data_path='rotation_euler',index=2,frame=frame)
        ob['joint_origin']=list(ob.location)
        ob.animation_data.action.name=ob.name+'_Closed_Open'
    # The guard is in FRONT of the central glass, not behind it. Retain its
    # detailed rails/pickets; remove only unsupported side-light pickets.
    faces=[p.index for p in win.data.polygons if 'Charcoal_Painted_Steel' in win.data.materials[p.material_index].name]
    rail=qc_take_faces(win,'Window interior safety guard',faces)
    for m in rail.data.materials:
        if 'Charcoal_Painted_Steel' in m.name:
            m.node_tree.nodes.get('Principled BSDF').inputs['Metallic'].default_value=.9
    bm=bmesh.new();bm.from_mesh(rail.data);seen=set();remove=[]
    for v in bm.verts:
        if v in seen:continue
        stack=[v];seen.add(v);component=[]
        while stack:
            vv=stack.pop();component.append(vv)
            for e in vv.link_edges:
                w=e.other_vert(vv)
                if w not in seen:seen.add(w);stack.append(w)
        qs=[rq(rail.matrix_world@v.co) for v in component]
        span=max(q.y for q in qs)-min(q.y for q in qs)
        if span<.03 and abs(sum(q.y for q in qs)/len(qs)-cy)>.78:
            remove.extend(component);continue
        inv=rail.matrix_world.inverted()
        for v,q in zip(component,qs):
            q.x-=.112
            q.y=cy+(q.y-cy)*(.995/1.2776) if span>1 else width_map(q.y)
            v.co=inv@rp(*q)
    bmesh.ops.delete(bm,geom=remove,context='VERTS');bm.to_mesh(rail.data);bm.free()
    # Short stand-off mounts connect the photographed guard to the mullions;
    # shifting the guard roomward must not leave an unsupported floating rail.
    qs=[rq(rail.matrix_world@v.co) for v in rail.data.vertices]
    rx=(min(q.x for q in qs)+max(q.x for q in qs))/2
    fx=rq(win.matrix_world@Vector((0,.023,0))).x
    steel=next(m for m in rail.data.materials if 'Charcoal_Painted_Steel' in m.name)
    for side in [-1,1]:
        for h in [min(q.z for q in qs)+.008,max(q.z for q in qs)-.009]:
            mount=rbox('Guard mullion stand-off',((rx+fx)/2,cy+side*.995,h),(abs(fx-rx),.018,.014),steel,.001)
            mount.parent=win;mount.matrix_parent_inverse=win.matrix_world.inverted()
            move_to_collection(mount,get_or_make_collection(COLL_OPEN))
    # Unjoin the existing photographed fabric into two sliding panels, keeping
    # the woven materials/folds. Full-open stacks park outside the outer frame.
    for side,label in [(-1,'Right'),(1,'Left')]:
        faces=[p.index for p in win.data.polygons
               if any(t in win.data.materials[p.material_index].name for t in ('taupe_curtains','Ivory_Sheer'))
               and (rq(win.matrix_world@win.data.vertices[p.vertices[0]].co).y-cy)*side>0]
        curtain=qc_take_faces(win,'Curtain_'+label,faces)
        inv=curtain.matrix_world.inverted()
        for v in curtain.data.vertices:
            q=rq(curtain.matrix_world@v.co);q.y+=side*.18;q.x-=.06
            q.z=.67+(q.z-.67)*(2.355-.67)/(2.2815-.67)
            v.co=inv@rp(*q)
        xs=[v.co.x for v in curtain.data.vertices];xmin,xmax=min(xs),max(xs)
        pivot=Vector(((xmin+xmax)/2,0,0));curtain.data.transform(Matrix.Translation(-pivot))
        curtain.location=pivot
        # Source X runs along the wall. Convert the parking position to that axis.
        park=inv@rp(4.58538,cy+side*1.40,.6126)
        travel=park.x-pivot.x
        world_width=(xmax-xmin)*win.matrix_world.to_3x3().col[0].length
        for frame,f in [(0,0),(1,0),(40,1),(80,1),(120,0)]:
            curtain.location.x=pivot.x+travel*f
            curtain.scale.x=1+(.09/world_width-1)*f
            curtain.keyframe_insert(data_path='location',index=0,frame=frame)
            curtain.keyframe_insert(data_path='scale',index=0,frame=frame)
        curtain['articulation_type']='prismatic';curtain['articulation_axis']=[1.,0.,0.]
        curtain['limit_min']=min(0.,travel);curtain['limit_max']=max(0.,travel)
        curtain['mechanism']='slides and gathers on upper track; full-open stack clears frame'
        curtain.animation_data.action.name='Curtain_'+label+'_Slide_Open'
    bpy.context.scene.frame_set(0);bpy.context.view_layer.update();scene.index()


def support_bvh(name):
    from mathutils.bvhtree import BVHTree
    o=bpy.data.objects[name]
    vertices=[o.matrix_world@v.co for v in o.data.vertices]
    return BVHTree.FromPolygons(vertices,[tuple(p.vertices) for p in o.data.polygons],all_triangles=False)

def support_height(tree,x,y,ceiling=1.6):
    p,normal,idx,distance=tree.ray_cast(rp(x,y,ceiling),Vector((0,0,-1)),3)
    if p is None:raise ValueError('No finite support below '+str((x,y)))
    return p.z-SHELL['floor_z']

def textile_patch(name,tree,cx,cy,width,length,amp,mat,mode='mound'):
    nx,ny=64,80;vs=[];fs=[]
    for j in range(ny+1):
        v=j/ny;yy=cy+(v-.5)*length
        for i in range(nx+1):
            u=i/nx;xx=cx+(u-.5)*width
            if mode=='throw':
                xx=cx+(u-.5)*width*(.68+.26*math.sin(v*10)+.12*math.cos(v*22))+.10*math.sin(v*6)
                yy=cy+(v-.5)*length+.025*math.sin(u*12+v*20)*math.sin(math.pi*v)
                h=amp*(.5+.5*math.sin(u*25+v*13))**2*math.sin(math.pi*u)*math.sin(math.pi*v)
                h+=.035*math.sin(math.pi*u)*math.sin(math.pi*v)*(1+math.cos(v*35+u*7))
            else:
                xx+=.025*math.sin(v*18+u*10)*math.sin(math.pi*u)
                yy+=.025*math.sin(u*16+v*8)*math.sin(math.pi*v)
                h=amp*(math.sin(math.pi*u)*math.sin(math.pi*v))**.6
                h*=.45+.55*(.5+.5*math.sin(u*24+v*15))
                h+=.015*math.sin(u*60+v*40)**2*math.sin(math.pi*u)*math.sin(math.pi*v)
            z=support_height(tree,xx,yy)+.0007+h
            vs.append(tuple(rp(xx,yy,z)))
    for j in range(ny):
        for i in range(nx):
            k=j*(nx+1)+i;fs.append((k,k+1,k+nx+2,k+nx+1))
    me=bpy.data.meshes.new(name);me.from_pydata(vs,[],fs);me.update();o=bpy.data.objects.new(name,me);bpy.context.collection.objects.link(o);me.materials.append(mat)
    uv=me.uv_layers.new(name='FabricMetric')
    for p in me.polygons:
        p.use_smooth=True
        for li in p.loop_indices:
            k=me.loops[li].vertex_index;uv.data[li].uv=((k%(nx+1))/nx*width,(k//(nx+1))/ny*length)
    bpy.context.view_layer.objects.active=o
    mod=o.modifiers.new('Hemmed cloth thickness','SOLIDIFY');mod.thickness=.004;mod.offset=1
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return o

def captured_textile(name,tree,pbr):
    data=CAPTURE_TEXTILES[name];verts=[]
    for (x,y,h),weight in zip(data['vertices'],data['contact_weight']):
        y=min(y,3.91)
        base=support_height(tree,x,y,.90)
        relief=max(0.,min(.26,h-.61))*weight
        verts.append(tuple(rp(x,y,base+.0005+relief)))
    me=bpy.data.meshes.new(name+' measured drape');me.from_pydata(verts,[],data['faces']);me.update()
    # Delaunay is in image coordinates; orient all surface faces upwards.
    import bmesh
    bm=bmesh.new();bm.from_mesh(me);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
    if sum(f.normal.z for f in bm.faces)<0:bmesh.ops.reverse_faces(bm,faces=list(bm.faces))
    bm.to_mesh(me);bm.free()
    o=bpy.data.objects.new(name+' photographed folds',me);bpy.context.collection.objects.link(o)
    mat=pbr.copy();mat.name=name+' captured cloth with PBR weave'
    # Blue clothing and duvet have isolated capture albedos. The sage texture
    # contains pink mattress pixels, so retain its clean woven fabric albedo.
    if name!='SageThrow':
        for node in mat.node_tree.nodes:
            if node.type=='TEX_IMAGE' and node.image and node.image.colorspace_settings.name!='Non-Color':
                node.image=bpy.data.images.load(os.path.join(HERE,'materials',name+'_capture_texture.jpg'),check_existing=True)
                for link in list(mat.node_tree.links):
                    if link.to_node==node and link.to_socket==node.inputs['Vector']:mat.node_tree.links.remove(link)
    me.materials.append(mat);uv=me.uv_layers.new(name='CaptureUV')
    for poly in me.polygons:
        poly.use_smooth=True
        for li in poly.loop_indices:uv.data[li].uv=data['uv'][me.loops[li].vertex_index]
    bpy.context.view_layer.objects.active=o
    sub=o.modifiers.new('Soft continuous fabric folds','SUBSURF');sub.levels=2
    bpy.ops.object.modifier_apply(modifier=sub.name)
    # Subdivision must not bridge below the actual mattress. Re-seat the hem
    # and retain the interior folds, ray-casting the finite source geometry.
    for v in o.data.vertices:
        q=rq(o.matrix_world@v.co)
        base=support_height(tree,q.x,q.y,.90)
        if q.z<base+.002:q.z=base+.0005
        if name=='SageThrow':
            # Low folds are compressed against the mattress over finite areas,
            # not just isolated hem vertices. Keep the elevated folds above them.
            gap=q.z-base
            if gap<.035:q.z=base+.0005
            else:
                raised=min(1.,(gap-.035)/.035)
                q.z+=raised*(.025*math.sin(38*q.x+12*q.y)**2+.012*math.sin(23*q.y-9*q.x)**2)
        v.co=o.matrix_world.inverted()@rp(*q)
    mod=o.modifiers.new('Fabric thickness','SOLIDIFY');mod.thickness=.003;mod.offset=1;bpy.ops.object.modifier_apply(modifier=mod.name)
    return o


def add_headboard_padding(bed):
    """Visible sewn upholstery with a closed back seated on the source bed."""
    xmin,xmax,zmin,zmax=1.855,3.765,.585,1.30
    nx,nz=196,72
    grid=[];hits=[]
    direction=Vector((-RS,RC,0))
    for j in range(nz+1):
        h=zmin+(zmax-zmin)*j/nz
        # Round the outline, including the enclosing wings' upper corners.
        radius=.055
        edge=min(h-zmin,zmax-h)
        inset=radius-math.sqrt(max(0.,radius*radius-(radius-min(radius,edge))**2))
        for k in range(nx+1):
            x=xmin+inset+(xmax-xmin-2*inset)*k/nx
            hit,_,_,_=bed.ray_cast(rp(x,3.88,h),direction,.35)
            y=rq(hit).y if hit is not None else None
            # At mattress height, use only actual rear headboard intersections.
            if y is not None and y<3.90:y=None
            grid.append((x,h,y))
            if y is not None and .66<h<1.25:hits.append(y)
    if not hits:raise ValueError('Headboard upholstery requires finite source contact')
    # A smooth sewn face must not copy the source mesh's jagged lower outline.
    # The depth datum is measured from the existing front, not a guessed support.
    face_y=min(hits)-.004
    rear_y=sorted(hits)[len(hits)//2]
    def front(x,h):
        v=max(0.,min(1.,(h-.60)/.635))
        phase=max(0.,min(7.,(x-1.95)/1.72*7))
        channel=abs(math.sin(math.pi*phase))**.65
        envelope=max(0.,math.sin(math.pi*v))**.4
        padded=.032*channel*envelope
        # The integrated shallow crown replaces the detached cylindrical roll.
        crown=.024*math.exp(-((h-1.265)/.030)**2)
        wing=max(math.exp(-((x-1.90)/.043)**2),
                 math.exp(-((x-3.72)/.043)**2))
        wing_depth=.070*wing*max(0.,math.sin(math.pi*(h-zmin)/(zmax-zmin)))**.28
        wrinkles=.0018*channel*envelope*(
            math.sin(117*x+31*h+2*math.sin(19*h))+
            .45*math.sin(193*x-43*h))*(.3+.7*abs(2*v-1)**3)
        return face_y-.012-padded-crown-wing_depth+wrinkles
    vs=[];faces=[]
    for x,h,y in grid:vs.append(tuple(rp(x,front(x,h),h)))
    n=len(vs)
    # This is the back of the visible padding, not separate support geometry.
    # A 1 mm seating allowance intersects the existing headboard at sampled hits.
    for x,h,y in grid:
        back=max((y if y is not None else rear_y)+.001,front(x,h)+.004)
        vs.append(tuple(rp(x,back,h)))
    for j in range(nz):
        for k in range(nx):
            a=j*(nx+1)+k;b=a+1;c=a+nx+2;d=a+nx+1
            faces.append((a,b,c,d));faces.append((n+d,n+c,n+b,n+a))
    boundary=list(range(nx+1))
    boundary += [j*(nx+1)+nx for j in range(1,nz+1)]
    boundary += [nz*(nx+1)+k for k in range(nx-1,-1,-1)]
    boundary += [j*(nx+1) for j in range(nz-1,0,-1)]
    for a,b in zip(boundary,boundary[1:]+boundary[:1]):
        faces.append((a,n+a,n+b,b))
    me=bpy.data.meshes.new('Continuous sewn headboard padding')
    me.from_pydata(vs,[],faces);me.update()
    ob=bpy.data.objects.new('Headboard stitched channels and rounded wings',me)
    bpy.context.collection.objects.link(ob)
    leather=rpbr('ivory_leather',4,rough=.57)
    me.materials.append(leather)
    uv=me.uv_layers.new(name='HeadboardMetric')
    for poly in me.polygons:
        poly.use_smooth=True
        for li in poly.loop_indices:
            x,h,_=grid[me.loops[li].vertex_index%n]
            uv.data[li].uv=(x,h)
    parts=[ob]
    # Small continuous piping follows the curved face and both enclosing wings.
    coords=[]
    for cx,cz,a0 in [(3.69,1.23,0),(1.93,1.23,90),
                     (1.93,.65,180),(3.69,.65,270)]:
        for step in range(17):
            a=math.radians(a0+step*90/16)
            x=cx+.045*math.cos(a);h=cz+.045*math.sin(a)
            coords.append((x,h))
    outline=coords;coords=[]
    for (xa,ha),(xb,hb) in zip(outline,outline[1:]+outline[:1]):
        steps=max(1,math.ceil(math.hypot(xb-xa,hb-ha)/.008))
        for j in range(steps):
            x=xa+(xb-xa)*j/steps;h=ha+(hb-ha)*j/steps
            coords.append((x,front(x,h)-.001,h))
    parts.append(rtube('Continuous headboard padded perimeter',coords,.0025,leather,closed=True))
    for i in range(1,7):
        x=1.95+1.72*i/7
        coords=[(x,front(x,h)-.0006,h) for h in [.61+j*.62/64 for j in range(65)]]
        parts.append(rtube('Headboard vertical seam '+str(i),coords,.0013,leather))
    coords=[(x,front(x,1.235)-.0007,1.235) for x in [1.94+j*1.74/112 for j in range(113)]]
    parts.append(rtube('Headboard crown seam',coords,.0015,leather))
    group_fixture('HeadboardUpholstery','upholstery',parts,attached_to='Bed0')
    return front


def add_bedroom_details(scene):
    white=bpy.data.materials['Warm white appliance plastic'];black=bpy.data.materials['Charcoal skirting and rubber'];bronze=bpy.data.materials['Satin champagne socket plates']
    sage=rpbr('sage_throw',3);ivory=rpbr('ivory_duvet',3);blue=rpbr('blue_clothes',4);plush=rpbr('white_plush',4)
    red=(_tex_mat('Rabbit red knitted scarf','rabbit_red_knit',(.78,.008,.024),scale=3,normal_strength=.35)
         or _solid_mat('Rabbit red knitted scarf',(.78,.008,.024),.95))
    thread=(_tex_mat('Rabbit charcoal embroidery','rabbit_embroidery',(.045,.042,.038),scale=.25,normal_strength=.25)
            or _solid_mat('Rabbit charcoal embroidery',(.045,.042,.038),.5))
    for node in ivory.node_tree.nodes:
        if node.type=='TEX_IMAGE' and node.image and node.image.name.startswith('ivory_duvet_diff'):
            node.image=bpy.data.images.load(os.path.join(HERE,'materials','duvet_floral_diffuse.jpg'),check_existing=True)
    bed=support_bvh('geometry_0')
    for name,params,mat in [('SageThrow',(2.53,2.77,.66,1.50,.065,'throw'),sage),('CreamDuvet',(2.25,3.61,.64,.49,.25,'mound'),ivory),('BlueClothes',(3.18,3.66,.43,.34,.14,'mound'),blue)]:
        x,y,w,l,h,mode=params;o=captured_textile(name,bed,mat)
        group_fixture(name,'textile',[o],rests_on='Bed0')
    # Thin pillow partly visible beneath the gathered cream duvet.
    # Its independent contact is found from the original mattress, not a guessed Z.
    # Round-01 pairs 62/78/80/133: one enclosed padded face, seven softly
    # bulging channels and rounded wings. Its rear skin follows the retained
    # neural headboard; floor, mattress and all textile contacts are untouched.
    headboard_front=add_headboard_padding(bed)
    # The cabinet has articulated drawers. Items rest on the exact fixed carcass.
    st=support_bvh('Storage1_Carcass');x,y=1.64,4.065;top=support_height(st,x,y,1.2)
    print('MEASURED_SUPPORT Nightstand raised shelf',top)
    # Plush rabbit; contiguous soft components rest through the seated body.
    parts=[rball('Rabbit seated body',(x,y,top+.085),(.085,.068,.085),plush),rball('Rabbit head',(x,y-.005,top+.205),(.083,.061,.080),plush)]
    for side in [-1,1]:
        paw=rball('Rabbit foot',(x+side*.045,y-.125,top+.032),(.034,.065,.032),plush)
        # Soft paws flatten under the paperback; its underside rests on these patches.
        for v in paw.data.vertices:v.co.z=min(v.co.z,.027)
        parts.append(paw)
        parts.append(rball('Rabbit arm',(x+side*.078,y-.012,top+.10),(.029,.033,.065),plush))
        ear=rball('Rabbit floppy ear',(x+side*.070,y-.045,top+.220),(.026,.027,.070),plush);ear.rotation_euler.y=-side*.75;parts.append(ear)
    # Facial stitches placed on the ellipsoid front surface.
    parts.extend([rball('Rabbit left eye',(x-.023,y-.063,top+.221),(.005,.004,.013),thread),rball('Rabbit right eye',(x+.025,y-.063,top+.217),(.005,.004,.010),thread),rball('Rabbit nose',(x,y-.066,top+.191),(.010,.006,.007),thread)])
    for name,points in [('left',[(-.042,.251),(-.012,.233)]),('right',[(.009,.233),(.043,.247)])]:
        coords=[]
        for dx,dz in points:
            front=y-.005-.061*math.sqrt(max(.01,1-(dx/.083)**2-((dz-.205)/.080)**2))
            coords.append((x+dx,front-.001,top+dz))
        parts.append(rtube('Rabbit embroidered '+name+' brow',coords,.0032,thread))
    parts.append(rbox('Rabbit scarf wrap',(x,y-.018,top+.145),(.171,.12,.042),red,.014))
    parts.append(rbox('Rabbit scarf tail',(x-.068,y-.076,top+.088),(.05,.016,.125),red,.008))
    group_fixture('BedsideRabbit','toy',parts,rests_on='Storage1',support_part='Storage1_Carcass')
    # Book spans the rabbit's feet, as photographed. Real top is queried from mesh.
    bpy.context.view_layer.update()
    # Fixed ledge allows the book's rear edge to rest; its forward edge touches feet.
    bh=top+.068
    paper=(_tex_mat('Book paper','book_paper',(.88,.87,.83),scale=12,normal_strength=.12)
           or _solid_mat('Book paper',(.88,.87,.83),.8))
    book=rbox('White paperback with red cover',(x,y-.125,bh),(.205,.125,.018),paper,.001)
    # Tessellate the actual flat underside to represent the two finite paw contacts.
    # Geometry is unchanged; a coarse two-triangle cap undersamples these patches.
    import bmesh
    bm=bmesh.new();bm.from_mesh(book.data)
    bottom=[f for f in bm.faces if f.normal.z<-.99 and f.calc_area()>.001]
    edges=list({e for f in bottom for e in f.edges})
    bmesh.ops.subdivide_edges(bm,edges=edges,cuts=11,use_grid_fill=True)
    bm.to_mesh(book.data);bm.free()
    # Create photographed cover as a material if available.
    coverpath=os.path.join(HERE,'materials','bedside_book_cover.jpg')
    if os.path.exists(coverpath):
        m=(_tex_mat('Photographed paperback cover','book_paper',(1,1,1),scale=(2.448,1.488,1.),normal_strength=.06)
           or _solid_mat('Photographed paperback cover',(1,1,1)))
        n=m.node_tree.nodes;l=m.node_tree.links;t=n.new('ShaderNodeTexImage');t.image=bpy.data.images.load(coverpath);l.new(t.outputs['Color'],n.get('Principled BSDF').inputs['Base Color'])
        ob=rbox('Printed paperback cover',(x,y-.125,bh+.0095),(.204,.124,.001),m)
        uv=ob.data.uv_layers.active
        for p in ob.data.polygons:
            for li in p.loop_indices:
                co=ob.data.vertices[ob.data.loops[li].vertex_index].co;uv.data[li].uv=(co.x/.204+.5,co.y/.124+.5)
        parts=[book,ob]
    else:parts=[book]
    group_fixture('BedsidePaperback','book',parts,rests_on='BedsideRabbit')
    # Remote on the lower open cubby, bound to the fixed carcass (not either drawer).
    rx,ry=1.565,3.82;rh=support_height(st,rx,ry,.46)
    parts=[rbox('Bedside remote body',(rx,ry,rh+.011),(.051,.115,.022),white,.005)]
    parts.append(rbox('Bedside remote display',(rx,ry+.018,rh+.023),(.035,.035,.002),black))
    for j in range(3):parts.append(rbox('Bedside remote button '+str(j),(rx,ry-.015-j*.018,rh+.023),(.029,.008,.002),bronze))
    group_fixture('BedsideRemote','remote',parts,rests_on='Storage1',support_part='Storage1_Carcass')
    # Frame 74: the second white-framed black display alongside the slim remote.
    dx,dy=1.645,3.82;dh=support_height(st,dx,dy,.46)
    parts=[rbox('Bedside display white case',(dx,dy,dh+.009),(.066,.104,.018),white,.004),rbox('Bedside display dark screen',(dx,dy,dh+.019),(.051,.083,.002),black,.002)]
    group_fixture('BedsideDisplay','display',parts,rests_on='Storage1',support_part='Storage1_Carcass')
    # Pairs 62/74/78: inset metal follows the soft wing rather than the
    # angular neural edge. The narrow capsule is partly embedded in upholstery.
    chrome=rmat('Headboard polished silver trim',(.72,.72,.70),.22,.85)
    parts=[]
    for side,x0 in [('left',1.90),('right',3.72)]:
        outline=[]
        for j in range(17):
            a=math.pi*j/16
            outline.append((x0+.006*math.cos(a),1.195+.006*math.sin(a)))
        outline += [(x0-.006,1.195-j*.495/48) for j in range(1,49)]
        for j in range(1,17):
            a=math.pi+math.pi*j/16
            outline.append((x0+.006*math.cos(a),.70+.006*math.sin(a)))
        outline += [(x0+.006,.70+j*.495/48) for j in range(1,48)]
        coords=[(x,headboard_front(x,h)-.001,h) for x,h in outline]
        parts.append(rtube('Headboard '+side+' silver wing strip',coords,.003,chrome,closed=True))
    group_fixture('HeadboardSilverTrim','trim',parts,attached_to='HeadboardUpholstery')
    # Headboard charging lead and adapter visible in frames 60 and 88.
    parts=[rbox('Bed charger adapter',(3.75,4.128,.38),(.045,.045,.064),black,.004)]
    parts.append(rtube('Bed charging cord',[(3.75,4.103,.37),(3.79,4.09,.26),(3.82,4.08,.10),(3.84,4.04,.016),(3.80,4.01,.012)],.0025,black))
    group_fixture('BedChargingLead','cable',parts,attached_to='BedChargerSocket')
    # Frame 8: keys/toy fob hanging from the door lock, ~0.80 m above floor.
    leaf=next(o for o in mesh_descendants(bpy.data.objects['Door0']) if o.animation_data)
    lt=support_bvh(leaf.name)
    op=SHELL['openings']['Door0']
    lockx=-.3475+op['offset']-.207/.620*op['width']*OPENING_FILL
    hit,normal,idx,d=lt.ray_cast(rp(lockx,1.9,.966),Vector((-RS,RC,0)),.5)
    if hit is None:raise ValueError('Measured keychain lock misses the original door leaf')
    q=rq(hit);gold=rmat('Brass keychain',(.68,.44,.14),.28,.7)
    parts=[rtube('Keychain linked cord',[(q.x,q.y,q.z),(q.x,q.y-.012,q.z-.016),(q.x,q.y-.012,q.z-.105)],.0023,gold)]
    parts.append(rball('Gold key fob',(q.x,q.y-.012,q.z-.124),(.015,.012,.022),gold))
    parts.append(rball('Black key fob base',(q.x,q.y-.012,q.z-.156),(.014,.012,.014),black))
    parts.append(rball('Key fob white eye',(q.x-.005,q.y-.023,q.z-.154),(.004,.002,.004),white))
    group_fixture('DoorKeychain','keys',parts,attached_to='Door0',support_part=leaf.name)

    bpy.context.view_layer.update();scene.index()


def finish_photo_repairs(scene):
    """Visible finish corrections, preserving source meshes and support poses."""
    # Eevee and glTF need explicit alpha for thin clear panes; transmission
    # alone renders an opaque environment reflection without screen refraction.
    for mat in bpy.data.materials:
        if not mat.use_nodes:continue
        p=mat.node_tree.nodes.get('Principled BSDF')
        if not p:continue
        if 'Clear_Glazing' in mat.name:
            p.inputs['Alpha'].default_value=.025
            p.inputs['Transmission Weight'].default_value=0.0
            p.inputs['Roughness'].default_value=.015
            mat.diffuse_color=(.95,.98,1,.025)
            if hasattr(mat,'surface_render_method'):mat.surface_render_method='DITHERED'
            if hasattr(mat,'blend_method'):mat.blend_method='BLEND'
        if any(t in mat.name.lower() for t in ('honey','oak_veneer','oak veneer')):
            links=list(p.inputs['Base Color'].links)
            if links:
                source=links[0].from_socket
                hue=mat.node_tree.nodes.new('ShaderNodeHueSaturation')
                hue.inputs['Saturation'].default_value=.60
                hue.inputs['Value'].default_value=1.12
                mat.node_tree.links.new(source,hue.inputs['Color'])
                mat.node_tree.links.new(hue.outputs['Color'],p.inputs['Base Color'])
            p.inputs['Roughness'].default_value=.48
    # Keep all basket bottom vertices on the marble. A continuous deformation
    # gives cloth walls, rolled rim and handles the same slight photographed lean.
    basket=bpy.data.objects['YellowBasket']
    meshes=mesh_descendants(basket)
    bottom=min(rq(o.matrix_world@v.co).z for o in meshes for v in o.data.vertices)
    for ob in meshes:
        inv=ob.matrix_world.inverted()
        for v in ob.data.vertices:
            q=rq(ob.matrix_world@v.co);t=max(0.,min(1.,(q.z-bottom)/.365))
            a=math.atan2(q.y-1.58,q.x-4.29)
            q.x+=.045*t+.007*t*math.sin(3*a)
            q.y-=.016*t
            q.z+=.008*t*math.sin(2*a+.4)
            v.co=inv@rp(*q)
        for poly in ob.data.polygons:poly.use_smooth=True
    # The small hall return vent visible above the entrance is distinct from
    # the wardrobe air conditioner; its frame is mounted into the soffit.
    white=bpy.data.materials['Warm white appliance plastic']
    dark=bpy.data.materials['Charcoal skirting and rubber']
    parts=[rbox('Hall vent inset',(.15,2.07,2.359),(.34,.19,.008),dark)]
    for x in [-.025,.325]:parts.append(rbox('Hall vent edge',(x,2.07,2.355),(.012,.21,.012),white))
    for y in [1.97,2.17]:parts.append(rbox('Hall vent edge',(.15,y,2.355),(.35,.012,.012),white))
    for i in range(12):parts.append(rbox('Hall vent slot divider',(.0+i*.027,2.07,2.354),(.008,.185,.012),white))
    group_fixture('HallReturnVent','vent',parts,attached_to='HallCeiling')
    # Window-side key follows the broad opening (frames 15/62/67/133).
    # A larger key-to-fill ratio preserves the photographed contact shading
    # using the existing bed, cabinet and sill geometry. Keep the emitter
    # invisible in view/reflection/transmission paths;
    # the actual frame, railing and environment supply the sill reflections.
    # Round-02 frames 62/67/106/133: confine the emitter below the soffit.
    # Its rectangular extent follows the opening; the previous disk reached
    # above the opening, consistent with the observed bright tray strip.
    light=rlight('Soft window daylight',(4.68,2.65,1.53),135,(1.0,.965,.92),2.15,(2.2,3.1,1.0))
    light.data.shape='RECTANGLE'
    light.data.size_y=1.12
    if hasattr(light,'visible_camera'):light.visible_camera=False
    if hasattr(light,'visible_transmission'):light.visible_transmission=False
    # Carry window bounce into the hall without adding an invented fixture.
    cycles=bpy.context.scene.cycles
    cycles.max_bounces=max(cycles.max_bounces,12)
    cycles.diffuse_bounces=max(cycles.diffuse_bounces,6)
    cycles.glossy_bounces=max(cycles.glossy_bounces,6)
    cycles.transparent_max_bounces=max(cycles.transparent_max_bounces,16)
    bpy.context.view_layer.update();scene.index()


CAPTURE_TEXTILES = {"SageThrow":{"vertices":[[2.59697,3.49596,0.64389],[2.58486,3.46376,0.64059],[2.61558,3.4957,0.65058],[2.62442,3.49352,0.64975],[2.59538,3.46248,0.64524],[2.56762,3.42596,0.64117],[2.64087,3.503,0.65305],[2.56358,3.404,0.63271],[2.67079,3.51522,0.65287],[2.6712,3.5118,0.65156],[2.64022,3.47917,0.64736],[2.6056,3.44224,0.64647],[2.57798,3.41327,0.64169],[2.56306,3.39889,0.62859],[2.56349,3.38677,0.62156],[2.71005,3.5375,0.64725],[2.73483,3.5477,0.64401],[2.69176,3.50215,0.64631],[2.65585,3.46452,0.6451],[2.62219,3.42932,0.64338],[2.59577,3.40221,0.63758],[2.57806,3.38489,0.62632],[2.56018,3.36733,0.61561],[2.5575,3.36295,0.61477],[2.74932,3.55901,0.64231],[2.54498,3.33206,0.61293],[2.79185,3.58334,0.63605],[2.80535,3.58916,0.63414],[2.74745,3.5282,0.64406],[2.70892,3.48845,0.6433],[2.6734,3.45191,0.64163],[2.64076,3.41846,0.63899],[2.61374,3.39115,0.6334],[2.59198,3.36961,0.62484],[2.57059,3.34842,0.61655],[2.54342,3.32072,0.61305],[2.8386,3.61144,0.628],[2.53495,3.29957,0.61222],[2.88539,3.63863,0.62075],[2.82033,3.57119,0.63298],[2.76289,3.51182,0.64235],[2.7257,3.4742,0.64056],[2.69199,3.44027,0.63751],[2.65945,3.40751,0.63459],[2.63007,3.37809,0.6304],[2.60438,3.35263,0.62441],[2.58023,3.32876,0.61801],[2.55421,3.30279,0.61359],[2.52448,3.27268,0.61262],[2.52227,3.26468,0.61369],[2.90201,3.6271,0.61705],[2.47111,3.18894,0.64425],[2.90644,3.62588,0.61634],[2.85007,3.56887,0.62302],[2.7834,3.50076,0.63756],[2.74431,3.46179,0.6367],[2.71038,3.42821,0.63358],[2.67735,3.3955,0.63076],[2.6451,3.36354,0.62829],[2.61584,3.33468,0.62463],[2.58916,3.30849,0.61993],[2.56175,3.28146,0.61646],[2.51344,3.2319,0.62914],[2.9181,3.61489,0.61367],[2.44803,3.14474,0.65423],[2.92595,3.6113,0.61286],[2.87887,3.56497,0.61376],[2.81874,3.50492,0.62382],[2.76804,3.45454,0.6297],[2.72908,3.41629,0.62949],[2.69278,3.38073,0.62859],[2.65777,3.34642,0.62779],[2.62427,3.3136,0.62691],[2.59453,3.28465,0.6243],[2.56063,3.25123,0.62546],[2.49799,3.18743,0.6486],[2.45782,3.14716,0.6568],[2.93171,3.6,0.61175],[2.44063,3.11782,0.6526],[2.43,3.09633,0.6489],[2.94254,3.59358,0.61109],[2.89738,3.55002,0.61065],[2.85318,3.50733,0.61083],[2.80194,3.45731,0.61652],[2.74856,3.40498,0.62496],[2.70566,3.36325,0.62809],[2.66762,3.32643,0.62916],[2.63113,3.2911,0.6302],[2.59619,3.25727,0.63116],[2.55668,3.21864,0.63632],[2.50681,3.16919,0.65011],[2.47029,3.13342,0.65543],[2.44476,3.10895,0.65346],[2.94393,3.58367,0.61063],[2.42176,3.07752,0.64356],[2.41405,3.05941,0.638],[2.91271,3.53174,0.60944],[2.86938,3.49066,0.60891],[2.82487,3.4483,0.61024],[2.76489,3.39027,0.62246],[2.71747,3.34473,0.62827],[2.6769,3.306,0.63086],[2.63817,3.26902,0.63331],[2.60012,3.23263,0.63634],[2.56137,3.19544,0.64091],[2.5212,3.15672,0.64756],[2.485,3.12191,0.65243],[2.4556,3.09394,0.65318],[2.43229,3.0721,0.65006],[2.41623,3.0576,0.64185],[2.95584,3.56705,0.60968],[2.40545,3.04055,0.63332],[2.39702,3.02203,0.62869],[2.96753,3.55023,0.60886],[2.92755,3.51296,0.60851],[2.88277,3.47118,0.60867],[2.83804,3.42933,0.60998],[2.77787,3.37216,0.62207],[2.72941,3.32641,0.62835],[2.71506,3.31291,0.62957],[2.68721,3.28674,0.63185],[2.64735,3.24928,0.63493],[2.60942,3.21365,0.63776],[2.5715,3.17794,0.64161],[2.53306,3.14163,0.64686],[2.49708,3.10767,0.65138],[2.46739,3.07991,0.65219],[2.44062,3.05499,0.65158],[2.41741,3.03356,0.64893],[2.40494,3.02284,0.63834],[2.39321,3.01284,0.62742],[2.38607,3.00116,0.6263],[2.75694,3.34403,0.62578],[2.81057,3.38618,0.61572],[2.37208,2.97747,0.62667],[2.97941,3.53363,0.60791],[2.86191,3.42554,0.60832],[2.72511,3.29795,0.62988],[2.943,3.49479,0.60722],[2.89735,3.45288,0.60772],[2.69835,3.26839,0.63228],[2.65763,3.23076,0.63578],[2.6193,3.19537,0.63874],[2.58217,3.16107,0.6419],[2.54325,3.12493,0.64733],[2.50723,3.09154,0.65173],[2.47794,3.06465,0.65213],[2.4501,3.03911,0.65221],[2.42303,3.01427,0.65244],[2.40712,3.00032,0.64461],[2.39817,2.9932,0.63154],[2.3795,2.97648,0.62662],[2.90415,3.45561,0.60744],[2.36507,2.95493,0.62721],[2.94806,3.48686,0.60654],[2.99323,3.51889,0.60589],[2.7364,3.28421,0.62939],[2.70991,3.25052,0.63241],[2.66706,3.21153,0.63716],[2.62759,3.17568,0.64077],[2.59038,3.14189,0.64388],[2.55191,3.10683,0.64887],[2.51713,3.07522,0.65224],[2.48849,3.04942,0.65205],[2.46072,3.02441,0.65195],[2.43127,2.99771,0.65388],[2.40918,2.97804,0.65085],[2.39849,2.96931,0.63929],[2.38739,2.96018,0.62827],[2.36415,2.93928,0.62756],[2.35763,2.93227,0.62823],[2.74598,3.26886,0.62998],[2.35071,2.91043,0.62891],[2.71902,3.23042,0.63409],[2.67557,3.19156,0.63911],[2.63498,3.15528,0.64336],[2.59805,3.12236,0.64618],[2.56098,3.08924,0.65008],[2.52813,3.06,0.65196],[2.49971,3.03486,0.65148],[2.47174,3.0101,0.65139],[2.43993,2.98165,0.65497],[2.41245,2.95723,0.65605],[2.3968,2.94393,0.6485],[2.39012,2.93909,0.63406],[2.37338,2.92471,0.62801],[2.34845,2.90254,0.62922],[2.75325,3.25141,0.63201],[2.34396,2.88904,0.62955],[2.72665,3.20911,0.63668],[2.68353,3.17124,0.64137],[2.64221,3.13494,0.64599],[2.605,3.10235,0.64892],[2.57022,3.07191,0.65114],[2.53814,3.0439,0.65236],[2.50914,3.01867,0.65219],[2.48107,2.99423,0.65209],[2.44843,2.96554,0.65615],[2.41674,2.93765,0.66037],[2.39612,2.91996,0.65676],[2.38527,2.91129,0.64583],[2.37749,2.90545,0.6326],[2.3587,2.88937,0.62868],[2.33713,2.86788,0.63035],[2.76014,3.23374,0.63423],[2.33038,2.84709,0.63119],[2.76635,3.21559,0.63685],[2.73319,3.18701,0.6399],[2.69038,3.15009,0.6443],[2.64914,3.11451,0.64878],[2.61245,3.08297,0.65127],[2.57774,3.05313,0.65336],[2.54548,3.02545,0.65462],[2.51591,3.00014,0.65479],[2.48765,2.97597,0.65476],[2.45535,2.94808,0.65849],[2.4225,2.91964,0.6635],[2.39791,2.89865,0.66295],[2.37944,2.88316,0.65818],[2.36972,2.87561,0.64676],[2.36271,2.87053,0.63328],[2.34284,2.85369,0.63075],[2.32398,2.82688,0.63183],[2.77116,3.19633,0.64028],[2.73744,3.16313,0.64448],[2.69518,3.12735,0.64849],[2.65533,3.09363,0.652],[2.61909,3.06303,0.65412],[2.58425,3.0336,0.65623],[2.55142,3.00591,0.65782],[2.52101,2.9803,0.65854],[2.49248,2.9563,0.65867],[2.4618,2.93036,0.66112],[2.43083,2.90411,0.66461],[2.40355,2.88108,0.66605],[2.37977,2.86113,0.66546],[2.36223,2.8467,0.66046],[2.35402,2.84057,0.64823],[2.34587,2.83449,0.63608],[2.32833,2.82,0.63207],[2.3255,2.81061,0.63258],[2.77282,3.17454,0.64558],[2.74029,3.13834,0.64986],[2.70062,3.10543,0.65219],[2.66271,3.07398,0.65438],[2.62614,3.04364,0.65665],[2.58985,3.01348,0.65965],[2.55498,2.98451,0.6626],[2.52266,2.95769,0.66465],[2.49529,2.93512,0.66396],[2.46971,2.91407,0.66263],[2.43996,2.88935,0.66509],[2.41093,2.86521,0.66777],[2.38261,2.84164,0.67066],[2.36159,2.82441,0.66846],[2.35005,2.81544,0.65909],[2.34381,2.81109,0.64555],[2.33326,2.80296,0.63574],[2.32509,2.79282,0.63496],[2.77478,3.15325,0.65064],[2.74552,3.11591,0.65369],[2.70776,3.08519,0.65477],[2.67055,3.0549,0.65642],[2.63359,3.02477,0.65886],[2.59614,2.99416,0.66255],[2.55701,2.96208,0.66836],[2.51851,2.93047,0.67474],[2.48696,2.90473,0.67715],[2.46284,2.88527,0.67493],[2.4377,2.86492,0.67406],[2.41194,2.844,0.6743],[2.38482,2.82188,0.67626],[2.35993,2.80163,0.67715],[2.34013,2.78571,0.67457],[2.32712,2.77558,0.6669],[2.32016,2.77062,0.65445],[2.31595,2.76776,0.64473],[2.77842,3.13363,0.65462],[2.30218,2.73925,0.65837],[2.75211,3.09484,0.65662],[2.71652,3.06648,0.65627],[2.67965,3.03701,0.65762],[2.64138,3.00633,0.6608],[2.60278,2.97532,0.66516],[2.56283,2.94313,0.67143],[2.5218,2.90999,0.67948],[2.48521,2.8805,0.68545],[2.45653,2.85756,0.68662],[2.43064,2.83693,0.68645],[2.40431,2.81589,0.68726],[2.37853,2.79528,0.68832],[2.35152,2.77359,0.691],[2.32951,2.75608,0.69038],[2.31507,2.74489,0.68413],[2.30354,2.73613,0.6758],[2.29994,2.73405,0.66098],[2.78499,3.11664,0.65678],[2.7573,3.0853,0.6576],[2.29738,2.71881,0.66434],[2.73234,3.05648,0.6573],[2.72599,3.04847,0.65731],[2.6893,3.01968,0.65844],[2.64997,2.98868,0.66219],[2.61025,2.95731,0.66718],[2.57027,2.92569,0.67336],[2.53101,2.89461,0.68005],[2.49323,2.86468,0.68672],[2.4616,2.83975,0.68992],[2.42933,2.81424,0.69441],[2.39909,2.79036,0.69821],[2.37479,2.77133,0.69823],[2.3532,2.75448,0.69676],[2.33486,2.74029,0.69324],[2.32003,2.72896,0.6873],[2.30715,2.71923,0.6801],[2.29868,2.71311,0.66947],[2.70682,3.02747,0.65784],[2.29817,2.70326,0.66556],[2.67916,2.99702,0.66026],[2.65023,2.96585,0.66405],[2.61908,2.94054,0.66825],[2.57925,2.90961,0.6742],[2.54141,2.88024,0.67978],[2.50442,2.8515,0.68573],[2.46735,2.82265,0.69268],[2.43196,2.79511,0.69937],[2.40488,2.7742,0.70078],[2.38266,2.75718,0.69918],[2.36239,2.7417,0.6966],[2.34419,2.72786,0.69289],[2.32866,2.71615,0.68747],[2.31468,2.70568,0.68113],[2.30291,2.69698,0.67325],[2.62086,2.93463,0.66872],[2.3003,2.68897,0.66563],[2.59232,2.90439,0.6734],[2.58884,2.89409,0.67461],[2.55107,2.86531,0.68001],[2.51442,2.83737,0.68558],[2.47703,2.80881,0.6926],[2.44429,2.78386,0.69721],[2.41955,2.76518,0.69673],[2.39916,2.7499,0.69358],[2.37842,2.73432,0.69119],[2.35894,2.71972,0.6883],[2.34128,2.70653,0.68443],[2.3257,2.69497,0.67928],[2.31174,2.68468,0.67315],[2.30367,2.67579,0.66464],[2.59344,2.88602,0.67483],[2.56819,2.85617,0.67517],[2.53697,2.83293,0.67674],[2.50629,2.81007,0.67869],[2.47464,2.78644,0.6821],[2.44334,2.76304,0.68604],[2.42202,2.74731,0.6833],[2.40126,2.732,0.68064],[2.37869,2.71528,0.67987],[2.35764,2.6997,0.67846],[2.33836,2.68547,0.67613],[2.32167,2.67323,0.67214],[2.30867,2.66384,0.66544],[2.30698,2.66266,0.66368],[2.60232,2.87378,0.67108],[2.31014,2.64951,0.66285],[2.5958,2.85474,0.66333],[2.56388,2.8314,0.66502],[2.53293,2.80876,0.66681],[2.5014,2.78567,0.66976],[2.46811,2.76121,0.67478],[2.43975,2.74045,0.67697],[2.41654,2.72356,0.67596],[2.39418,2.70729,0.67486],[2.3723,2.69136,0.67392],[2.35055,2.67552,0.6734],[2.33206,2.66213,0.67075],[2.3164,2.65088,0.66619],[2.62092,2.86883,0.66088],[2.31376,2.63682,0.66161],[2.61247,2.84473,0.65896],[2.57825,2.82012,0.66201],[2.54512,2.79629,0.66513],[2.51349,2.77355,0.668],[2.48204,2.75091,0.67151],[2.45195,2.72925,0.6748],[2.42691,2.71131,0.67504],[2.40428,2.69513,0.67403],[2.38233,2.67944,0.67304],[2.3611,2.66427,0.672],[2.34179,2.65051,0.66991],[2.32456,2.63828,0.66657],[2.62881,2.85572,0.6578],[2.31753,2.62338,0.6623],[2.63281,2.83983,0.65728],[2.62368,2.83061,0.65825],[2.58891,2.80606,0.66153],[2.55517,2.78223,0.66494],[2.52349,2.75987,0.66772],[2.49291,2.73828,0.67048],[2.46327,2.71735,0.6733],[2.43757,2.69926,0.6739],[2.41413,2.68279,0.67339],[2.39152,2.6669,0.67281],[2.37083,2.65239,0.67126],[2.35166,2.63898,0.66897],[2.33312,2.62601,0.66661],[2.32117,2.60996,0.66307],[2.63528,2.82296,0.65775],[2.63389,2.81578,0.65821],[2.5995,2.79196,0.6611],[2.56598,2.76873,0.66423],[2.53453,2.74694,0.66671],[2.50399,2.72578,0.66931],[2.47448,2.70534,0.67189],[2.44794,2.68699,0.67297],[2.4235,2.6701,0.67312],[2.4006,2.6543,0.67267],[2.37984,2.64,0.67108],[2.36031,2.62657,0.669],[2.34135,2.61354,0.66691],[2.32434,2.60187,0.66365],[2.32513,2.59688,0.66354],[2.63785,2.80633,0.65814],[2.60954,2.77749,0.66103],[2.57597,2.75466,0.66407],[2.54436,2.73317,0.66655],[2.51454,2.7129,0.66851],[2.48498,2.6928,0.671],[2.45782,2.67436,0.67243],[2.43231,2.65704,0.67327],[2.40883,2.64112,0.67317],[2.38761,2.62677,0.67186],[2.36823,2.61368,0.6696],[2.3484,2.60027,0.66815],[2.33047,2.58817,0.6656],[2.32834,2.58337,0.66462],[2.64022,2.7897,0.65865],[2.61922,2.76281,0.6612],[2.58528,2.74015,0.66437],[2.55384,2.71918,0.66662],[2.52419,2.69941,0.66835],[2.4954,2.68021,0.67017],[2.46765,2.66171,0.6719],[2.44109,2.64399,0.67342],[2.41681,2.62783,0.67383],[2.3948,2.61319,0.67307],[2.37516,2.60016,0.67096],[2.35555,2.58714,0.66927],[2.33623,2.57431,0.66781],[2.33118,2.56973,0.66597],[2.64234,2.77307,0.6593],[2.33454,2.55657,0.66686],[2.62846,2.74787,0.66164],[2.59464,2.72572,0.66462],[2.56359,2.7054,0.6665],[2.53436,2.68628,0.66782],[2.50541,2.66733,0.66963],[2.47717,2.64885,0.67161],[2.44991,2.63101,0.67353],[2.42441,2.61432,0.67478],[2.40153,2.59938,0.67461],[2.38166,2.58643,0.67263],[2.36229,2.57381,0.6707],[2.34302,2.56124,0.66914],[2.64414,2.75637,0.66014],[2.33807,2.54362,0.66759],[2.64592,2.73983,0.66098],[2.63809,2.73324,0.66181],[2.60425,2.71149,0.66469],[2.573,2.69143,0.66659],[2.54378,2.67267,0.66781],[2.51505,2.65422,0.66936],[2.48691,2.63616,0.67115],[2.45936,2.61846,0.67317],[2.43267,2.60131,0.6752],[2.40841,2.58575,0.676],[2.38806,2.57272,0.67435],[2.36935,2.56076,0.67185],[2.35034,2.54859,0.67002],[2.34123,2.53053,0.6686],[2.64809,2.7237,0.66154],[2.64808,2.71887,0.66174],[2.61396,2.69736,0.66469],[2.58236,2.67745,0.66672],[2.55316,2.65907,0.66781],[2.52495,2.6413,0.6689],[2.49658,2.62342,0.67074],[2.46877,2.6059,0.67284],[2.44158,2.58876,0.67513],[2.41569,2.57245,0.67707],[2.39465,2.55922,0.67588],[2.37598,2.54749,0.67332],[2.35749,2.53588,0.67102],[2.34423,2.51746,0.66971],[2.65068,2.70798,0.66182],[2.62364,2.68324,0.66469],[2.59159,2.66344,0.66691],[2.56255,2.64549,0.66781],[2.53428,2.62802,0.66883],[2.50596,2.61052,0.67053],[2.47837,2.59346,0.67236],[2.45064,2.57632,0.67493],[2.42346,2.55951,0.67774],[2.40156,2.54599,0.67715],[2.38257,2.53428,0.67478],[2.3641,2.5229,0.67242],[2.34558,2.51147,0.67053],[2.65215,2.69171,0.6628],[2.34644,2.50401,0.67143],[2.63288,2.66889,0.66497],[2.60074,2.6494,0.66715],[2.57168,2.63179,0.66796],[2.54386,2.61493,0.66858],[2.51592,2.59799,0.66991],[2.48813,2.58114,0.67177],[2.46031,2.56427,0.67429],[2.4322,2.54722,0.67767],[2.40889,2.53309,0.67806],[2.38898,2.52103,0.67636],[2.37087,2.51007,0.67368],[2.35153,2.49836,0.67238],[2.65349,2.67551,0.66386],[2.34936,2.49112,0.67255],[2.64086,2.65382,0.66606],[2.60939,2.63511,0.66771],[2.58023,2.61777,0.66851],[2.553,2.60158,0.66863],[2.52506,2.58497,0.66985],[2.4978,2.56876,0.67124],[2.46979,2.5521,0.67378],[2.44176,2.53543,0.67699],[2.41632,2.52031,0.67888],[2.39549,2.50793,0.67782],[2.37706,2.49697,0.67536],[2.35813,2.48573,0.67369],[2.65399,2.65898,0.66544],[2.35277,2.47862,0.67325],[2.65456,2.64266,0.66694],[2.64852,2.63864,0.66733],[2.61804,2.62087,0.66825],[2.58939,2.60416,0.66862],[2.56233,2.58838,0.66854],[2.53473,2.57229,0.66943],[2.50711,2.55618,0.67095],[2.47941,2.54003,0.67317],[2.45116,2.52355,0.67642],[2.42398,2.50771,0.67951],[2.40216,2.49498,0.67914],[2.38319,2.48392,0.67706],[2.36509,2.47336,0.67469],[2.35664,2.4665,0.67356],[2.655,2.62644,0.66852],[2.65575,2.6233,0.66885],[2.62638,2.6065,0.66898],[2.59847,2.59054,0.66877],[2.57172,2.57524,0.66839],[2.54441,2.55962,0.66899],[2.51691,2.54389,0.67033],[2.48874,2.52779,0.67276],[2.46025,2.5115,0.67607],[2.43315,2.49601,0.679],[2.40983,2.48268,0.67968],[2.38978,2.47121,0.67836],[2.37148,2.46073,0.67611],[2.35914,2.45367,0.67495],[2.65627,2.61085,0.66954],[2.35995,2.44076,0.6765],[2.63494,2.59231,0.66954],[2.60753,2.57693,0.66893],[2.58084,2.56196,0.66843],[2.55377,2.54678,0.66877],[2.52624,2.53134,0.67003],[2.49769,2.51535,0.67263],[2.46993,2.49979,0.6753],[2.44333,2.48488,0.67774],[2.41886,2.47117,0.67917],[2.39654,2.45865,0.67951],[2.37708,2.44773,0.67812],[2.65867,2.59604,0.66983],[2.36152,2.42841,0.67742],[2.64359,2.57822,0.67004],[2.61666,2.5634,0.66904],[2.58995,2.54871,0.66846],[2.56238,2.53354,0.66906],[2.53426,2.5181,0.67064],[2.50645,2.50281,0.67262],[2.47899,2.48773,0.67497],[2.45278,2.47333,0.67702],[2.4278,2.45961,0.67873],[2.404,2.44653,0.68011],[2.38332,2.43515,0.6796],[2.36454,2.42481,0.67805],[2.659,2.58077,0.67023],[2.36295,2.41609,0.67844],[2.65189,2.56399,0.67074],[2.62501,2.54948,0.66964],[2.59808,2.53496,0.66914],[2.57044,2.52006,0.66971],[2.54269,2.50511,0.67094],[2.51464,2.49001,0.67301],[2.48716,2.47522,0.67528],[2.46081,2.46103,0.67734],[2.43645,2.44791,0.67851],[2.41192,2.43471,0.68034],[2.38967,2.42271,0.68097],[2.36985,2.41202,0.68019],[2.6588,2.56538,0.67096],[2.36394,2.40364,0.67979],[2.65802,2.54983,0.67205],[2.65898,2.54918,0.6722],[2.63182,2.53482,0.67123],[2.605,2.52063,0.6706],[2.57857,2.50666,0.67029],[2.55118,2.49219,0.6712],[2.5228,2.47722,0.6734],[2.49541,2.46277,0.67553],[2.46938,2.44904,0.67726],[2.4447,2.43601,0.67857],[2.42018,2.42308,0.6803],[2.39609,2.41037,0.68226],[2.37511,2.39928,0.68233],[2.36517,2.39145,0.68093],[2.65618,2.53392,0.67382],[2.63782,2.51982,0.67331],[2.61227,2.50656,0.67182],[2.58615,2.49302,0.67123],[2.55953,2.47924,0.67154],[2.53193,2.46496,0.67311],[2.50442,2.45075,0.67521],[2.47819,2.43719,0.677],[2.45264,2.42398,0.67886],[2.42802,2.41126,0.68058],[2.40376,2.39873,0.68257],[2.38133,2.38712,0.68369],[2.36738,2.37985,0.68129],[2.65492,2.51848,0.67521],[2.36959,2.36835,0.68163],[2.64433,2.50519,0.67504],[2.62003,2.4928,0.67268],[2.59481,2.47998,0.67143],[2.5677,2.46622,0.67199],[2.54074,2.45256,0.67303],[2.5137,2.43887,0.67471],[2.48687,2.42528,0.67682],[2.46056,2.41197,0.67914],[2.43574,2.39941,0.68093],[2.41152,2.38715,0.68281],[2.38847,2.37548,0.68432],[2.37075,2.36644,0.68219],[2.65445,2.50359,0.67609],[2.37059,2.35636,0.6829],[2.65153,2.49097,0.67629],[2.6284,2.47939,0.67312],[2.60384,2.46714,0.67137],[2.57734,2.45396,0.67144],[2.54969,2.44024,0.67285],[2.52262,2.42681,0.67446],[2.49597,2.4136,0.67635],[2.46874,2.40012,0.67923],[2.44335,2.38754,0.68135],[2.41929,2.37561,0.68302],[2.39583,2.36397,0.68477],[2.37599,2.3541,0.68422],[2.65434,2.48904,0.67673],[2.37303,2.34516,0.68302],[2.65494,2.47496,0.67691],[2.63663,2.46596,0.67365],[2.61233,2.45408,0.67166],[2.58667,2.44156,0.67109],[2.55922,2.42821,0.67227],[2.53208,2.41503,0.67382],[2.50496,2.40187,0.67594],[2.47749,2.38855,0.67889],[2.45117,2.3758,0.6816],[2.42736,2.36423,0.68301],[2.40387,2.35283,0.68469],[2.38289,2.34261,0.68494],[2.3754,2.334,0.68319],[2.65549,2.46099,0.67712],[2.64408,2.45221,0.67465],[2.62081,2.44104,0.67195],[2.5958,2.42907,0.67087],[2.56849,2.41607,0.67186],[2.54076,2.40288,0.67373],[2.51376,2.39005,0.67567],[2.48614,2.37695,0.67863],[2.45931,2.36423,0.68161],[2.43567,2.35298,0.68281],[2.41211,2.34178,0.68445],[2.38985,2.33119,0.68561],[2.37758,2.32285,0.68349],[2.6561,2.44718,0.67729],[2.65064,2.43812,0.67621],[2.6282,2.42754,0.67293],[2.60358,2.416,0.67154],[2.57658,2.4034,0.67225],[2.5495,2.39079,0.67359],[2.52245,2.3782,0.67547],[2.49484,2.36538,0.67833],[2.46816,2.353,0.6811],[2.44366,2.3416,0.68284],[2.42036,2.33076,0.6842],[2.39666,2.31974,0.68637],[2.38001,2.31188,0.68359],[2.65569,2.43303,0.6781],[2.3816,2.30063,0.68433],[2.65498,2.42314,0.67916],[2.6342,2.4135,0.67479],[2.61088,2.40276,0.67251],[2.58475,2.39081,0.67257],[2.55861,2.37887,0.67318],[2.53124,2.36641,0.67519],[2.50396,2.35401,0.67772],[2.47703,2.34178,0.68057],[2.45161,2.33022,0.68289],[2.42722,2.31914,0.68498],[2.40313,2.30819,0.68737],[2.38502,2.29985,0.68568],[2.65427,2.41859,0.67955],[2.38285,2.28933,0.68531],[2.63807,2.39863,0.678],[2.61661,2.3889,0.67449],[2.59208,2.37789,0.67344],[2.5662,2.36631,0.67382],[2.53884,2.35412,0.67574],[2.51152,2.34197,0.67821],[2.48478,2.33009,0.68084],[2.45802,2.31822,0.68405],[2.43277,2.307,0.68672],[2.4091,2.29647,0.68873],[2.38992,2.28785,0.68782],[2.65166,2.4038,0.68175],[2.38407,2.27811,0.68629],[2.64812,2.3888,0.68453],[2.63812,2.38232,0.6836],[2.61725,2.37303,0.67975],[2.59433,2.36291,0.67767],[2.56803,2.35141,0.67832],[2.54001,2.33922,0.68069],[2.51474,2.32818,0.68172],[2.49029,2.3175,0.6827],[2.46363,2.30593,0.68577],[2.43698,2.29439,0.68941],[2.4139,2.28433,0.69094],[2.39475,2.2759,0.68998],[2.38484,2.26681,0.68761],[2.64016,2.37219,0.6901],[2.62968,2.36278,0.69454],[2.59822,2.34942,0.69769],[2.57304,2.33861,0.69736],[2.55142,2.32925,0.69514],[2.53256,2.32102,0.69146],[2.51392,2.3129,0.68799],[2.49329,2.30399,0.68629],[2.46869,2.2935,0.68786],[2.44181,2.28212,0.69161],[2.41863,2.27224,0.69317],[2.39965,2.26406,0.69205],[2.3846,2.25746,0.6883],[2.38572,2.25565,0.68882],[2.62426,2.35268,0.70071],[2.62236,2.34408,0.70463],[2.58731,2.32961,0.71018],[2.56496,2.32015,0.70815],[2.54606,2.31206,0.70425],[2.52951,2.30491,0.69911],[2.51323,2.29788,0.69409],[2.49513,2.29014,0.69066],[2.47296,2.28084,0.69048],[2.44763,2.27032,0.69305],[2.42397,2.26047,0.69492],[2.4046,2.25231,0.69405],[2.38893,2.24561,0.69076],[2.38685,2.2447,0.68983],[2.62599,2.33953,0.7055],[2.38545,2.23297,0.69013],[2.62542,2.32971,0.70806],[2.58863,2.3149,0.7147],[2.56508,2.30519,0.71346],[2.54555,2.29703,0.71002],[2.52961,2.29026,0.70453],[2.51398,2.28361,0.69912],[2.49681,2.27639,0.69507],[2.47603,2.26782,0.69392],[2.45361,2.25864,0.69435],[2.42957,2.24887,0.69644],[2.40889,2.24039,0.69651],[2.3929,2.2337,0.69345],[2.63199,2.32814,0.70754],[2.38376,2.22126,0.69066],[2.62745,2.31508,0.7121],[2.59148,2.30094,0.71817],[2.56718,2.29116,0.71742],[2.54669,2.28281,0.71464],[2.5298,2.27582,0.70982],[2.51413,2.26929,0.7045],[2.49749,2.26242,0.70013],[2.47904,2.2549,0.69734],[2.45833,2.24655,0.69655],[2.43485,2.23721,0.69817],[2.41267,2.22837,0.69932],[2.39554,2.2214,0.69712],[2.38366,2.21637,0.69122],[2.63774,2.3167,0.70973],[2.38234,2.20977,0.69099],[2.63923,2.30415,0.71002],[2.59746,2.28823,0.71962],[2.57212,2.2783,0.7195],[2.54937,2.26932,0.71818],[2.53159,2.26215,0.71399],[2.51566,2.25566,0.70886],[2.49958,2.24913,0.70414],[2.48218,2.24214,0.70064],[2.4628,2.23446,0.69887],[2.43975,2.22549,0.70015],[2.41604,2.2163,0.70239],[2.3982,2.20923,0.70071],[2.38643,2.20433,0.69476],[2.66016,2.31134,0.70158],[2.38197,2.19878,0.69053],[2.68067,2.30506,0.69471],[2.648,2.2921,0.70982],[2.60544,2.2763,0.71979],[2.57815,2.2659,0.72085],[2.55451,2.25681,0.72008],[2.53558,2.24938,0.71663],[2.51968,2.24304,0.7115],[2.50298,2.23643,0.7072],[2.4853,2.22949,0.7039],[2.4665,2.22218,0.70172],[2.44388,2.21356,0.70267],[2.41935,2.20431,0.70546],[2.40116,2.19727,0.70403],[2.38975,2.19259,0.69784],[2.38219,2.18924,0.68888],[2.38248,2.18819,0.6894],[2.69716,2.2972,0.69038],[2.38307,2.17771,0.68821],[2.65352,2.27896,0.71161],[2.61414,2.26464,0.7195],[2.58515,2.2539,0.72156],[2.55996,2.24448,0.72174],[2.54078,2.23713,0.71844],[2.52456,2.23081,0.71352],[2.50676,2.22396,0.70996],[2.48838,2.21694,0.70714],[2.46967,2.20982,0.7049],[2.44834,2.20183,0.7049],[2.42468,2.19309,0.70701],[2.40528,2.18581,0.70645],[2.39307,2.18094,0.70087],[2.38503,2.17749,0.69231],[2.71354,2.28916,0.68617],[2.38318,2.16717,0.68739],[2.65714,2.26527,0.71454],[2.62003,2.25206,0.72095],[2.59092,2.24153,0.72304],[2.56563,2.2323,0.72323],[2.54599,2.22496,0.72022],[2.52905,2.21853,0.71577],[2.51104,2.21176,0.71234],[2.49217,2.20473,0.70985],[2.47303,2.19763,0.70791],[2.45318,2.19031,0.70683],[2.43128,2.18235,0.70762],[2.41041,2.17475,0.7081],[2.39664,2.16948,0.70367],[2.38753,2.16573,0.69596],[2.38305,2.15663,0.68676],[2.71876,2.27734,0.68876],[2.65829,2.25089,0.71893],[2.62525,2.23932,0.72279],[2.5973,2.22943,0.7241],[2.57168,2.22031,0.72446],[2.55166,2.213,0.72167],[2.53456,2.20666,0.7173],[2.51607,2.19988,0.71419],[2.49641,2.19275,0.71222],[2.47675,2.18565,0.71062],[2.45724,2.17861,0.70929],[2.4369,2.17134,0.70893],[2.41593,2.16388,0.70943],[2.4006,2.15823,0.70615],[2.38988,2.15404,0.69967],[2.384,2.15137,0.68966],[2.38359,2.1464,0.68563],[2.71777,2.26361,0.69506],[2.66383,2.23805,0.7206],[2.63256,2.22732,0.72332],[2.60396,2.21746,0.72497],[2.57796,2.20843,0.72553],[2.55812,2.20135,0.72258],[2.53991,2.1948,0.71893],[2.52046,2.18788,0.71643],[2.50038,2.18078,0.71473],[2.48083,2.17388,0.71304],[2.46171,2.16712,0.71142],[2.44214,2.16025,0.71048],[2.42217,2.15328,0.71021],[2.40492,2.14717,0.70832],[2.39341,2.14281,0.70244],[2.38705,2.14003,0.69284],[2.38544,2.13664,0.6835],[2.73649,2.25615,0.68952],[2.67601,2.22729,0.71827],[2.64058,2.21556,0.72341],[2.61122,2.20571,0.72546],[2.58518,2.1969,0.72598],[2.56462,2.18976,0.72345],[2.5453,2.18301,0.72049],[2.52513,2.17604,0.71846],[2.50488,2.16906,0.71685],[2.48511,2.16225,0.7153],[2.46664,2.15584,0.7132],[2.44714,2.14916,0.71218],[2.42767,2.1425,0.71152],[2.41056,2.13657,0.70949],[2.39856,2.13215,0.70397],[2.3916,2.12923,0.69484],[2.38778,2.12723,0.68339],[2.38719,2.12691,0.68144],[2.78194,2.25659,0.6682],[2.38995,2.11703,0.68448],[2.69618,2.21882,0.71116],[2.64874,2.20387,0.7234],[2.61915,2.1942,0.72552],[2.59308,2.18559,0.72598],[2.57179,2.1784,0.72388],[2.55143,2.17151,0.72156],[2.53065,2.16452,0.71989],[2.50977,2.15752,0.71868],[2.48976,2.1508,0.71727],[2.47077,2.1444,0.71551],[2.45173,2.138,0.71415],[2.4328,2.13166,0.71307],[2.41632,2.12604,0.71055],[2.40424,2.1217,0.70508],[2.39663,2.11863,0.69645],[2.39104,2.11614,0.68637],[2.82087,2.2545,0.65098],[2.39295,2.10728,0.6873],[2.84362,2.24732,0.64345],[2.71784,2.21058,0.70324],[2.65755,2.19238,0.72299],[2.62733,2.18278,0.72541],[2.60055,2.17418,0.72625],[2.57849,2.16694,0.72459],[2.5572,2.15995,0.72284],[2.53591,2.15298,0.72147],[2.51517,2.14619,0.72014],[2.4951,2.13962,0.71873],[2.47562,2.13323,0.71729],[2.45654,2.12698,0.71593],[2.43788,2.12087,0.71463],[2.42145,2.11539,0.71206],[2.40951,2.11117,0.70648],[2.40132,2.10799,0.69829],[2.39447,2.10518,0.6892],[2.39636,2.09771,0.68978],[2.78122,2.21755,0.67568],[2.74149,2.20265,0.69423],[2.66355,2.18013,0.72427],[2.63361,2.17085,0.72647],[2.60756,2.16267,0.7268],[2.58486,2.15543,0.7255],[2.56333,2.14854,0.72386],[2.54113,2.1415,0.72306],[2.52061,2.13494,0.72155],[2.50047,2.1285,0.72016],[2.48096,2.12226,0.71871],[2.46118,2.11597,0.71781],[2.44221,2.10992,0.71671],[2.42579,2.10458,0.71411],[2.41429,2.10056,0.70821],[2.40669,2.09759,0.69959],[2.39865,2.09451,0.69142],[2.74308,2.19535,0.69391],[2.40018,2.08831,0.69192],[2.67023,2.16811,0.72512],[2.63995,2.15899,0.72747],[2.61429,2.15112,0.72751],[2.59145,2.14402,0.72626],[2.56913,2.13709,0.72509],[2.54672,2.13017,0.72438],[2.52607,2.12374,0.72292],[2.50585,2.11744,0.72156],[2.48616,2.1113,0.7202],[2.46635,2.10516,0.71929],[2.4474,2.09927,0.71814],[2.43191,2.09429,0.71486],[2.42032,2.09033,0.70901],[2.4118,2.08717,0.70107],[2.40247,2.08381,0.69387],[2.69754,2.17182,0.71677],[2.40353,2.07884,0.69439],[2.68344,2.15782,0.72207],[2.6478,2.14756,0.72753],[2.62187,2.13982,0.72768],[2.59876,2.13283,0.72655],[2.57584,2.12593,0.7257],[2.55705,2.12027,0.72513],[2.55331,2.11915,0.72503],[2.53219,2.11275,0.72384],[2.51184,2.10658,0.72253],[2.49194,2.10055,0.72127],[2.47256,2.09467,0.72003],[2.45418,2.08906,0.71843],[2.43863,2.08418,0.71516],[2.42691,2.08027,0.70938],[2.41733,2.0769,0.70222],[2.40703,2.07336,0.69574],[2.67948,2.15607,0.72351],[2.4083,2.06977,0.69577],[2.57977,2.11943,0.72605],[2.67038,2.14287,0.725],[2.55735,2.10985,0.72556],[2.6563,2.13631,0.7272],[2.62906,2.12845,0.72809],[2.60573,2.12159,0.72704],[2.53907,2.102,0.72425],[2.51864,2.09596,0.72294],[2.49854,2.09003,0.72178],[2.47917,2.0843,0.72048],[2.46126,2.07895,0.71852],[2.44544,2.07412,0.71539],[2.43338,2.07021,0.70983],[2.42359,2.06684,0.70281],[2.41261,2.06321,0.69683],[2.60308,2.1186,0.72694],[2.41324,2.06078,0.697],[2.65989,2.12955,0.7274],[2.5588,2.09979,0.72524],[2.62659,2.11768,0.72802],[2.63648,2.11716,0.72834],[2.54671,2.09144,0.72415],[2.52558,2.0854,0.72324],[2.50516,2.07955,0.72225],[2.48599,2.07401,0.72077],[2.46814,2.0688,0.71872],[2.45302,2.06425,0.71507],[2.4407,2.06036,0.70966],[2.43013,2.05688,0.70319],[2.4176,2.05297,0.69834],[2.41794,2.05177,0.6984],[2.65157,2.11695,0.72856],[2.56052,2.08986,0.72475],[2.55397,2.08082,0.72428],[2.53219,2.07479,0.72376],[2.51129,2.06898,0.72304],[2.49251,2.06367,0.72126],[2.47524,2.05873,0.71877],[2.46003,2.05426,0.71514],[2.44776,2.05047,0.70968],[2.43686,2.04698,0.70342],[2.42279,2.04282,0.69967],[2.56187,2.07991,0.72449],[2.42433,2.03348,0.69858],[2.56013,2.06998,0.72513],[2.53825,2.06408,0.72463],[2.5178,2.05853,0.72357],[2.49902,2.05336,0.72175],[2.4818,2.04855,0.71919],[2.46708,2.0443,0.71518],[2.45487,2.0406,0.70964],[2.44298,2.03697,0.70408],[2.42903,2.03294,0.70022],[2.56169,2.0697,0.72521],[2.42552,2.02412,0.69776],[2.56089,2.05945,0.72633],[2.54407,2.05337,0.72564],[2.52412,2.04807,0.72421],[2.50561,2.04309,0.72216],[2.48852,2.03843,0.71948],[2.47416,2.03435,0.7152],[2.46175,2.03069,0.70977],[2.44819,2.02681,0.70538],[2.43408,2.02284,0.70162],[2.42639,2.01477,0.69717],[2.56144,2.04957,0.72658],[2.55075,2.04286,0.72607],[2.53074,2.03769,0.72464],[2.51207,2.03282,0.72267],[2.49497,2.02828,0.71995],[2.48084,2.02434,0.71548],[2.46781,2.02064,0.71047],[2.45304,2.01662,0.70692],[2.43875,2.01271,0.70327],[2.42821,2.00954,0.69711],[2.42799,2.00562,0.69603],[2.56286,2.03993,0.72625],[2.55949,2.03277,0.72516],[2.5387,2.0276,0.72418],[2.51899,2.02266,0.72285],[2.50186,2.01823,0.72011],[2.4876,2.01437,0.7157],[2.4741,2.01067,0.71099],[2.459,2.00668,0.70764],[2.44438,2.00281,0.70421],[2.43362,1.99967,0.69821],[2.43025,1.99665,0.69442],[2.56606,2.0307,0.72478],[2.56874,2.02277,0.72393],[2.54682,2.01754,0.72361],[2.52615,2.01256,0.72286],[2.5086,2.00818,0.72036],[2.49436,2.00441,0.71591],[2.48039,2.00071,0.71151],[2.46544,1.99686,0.70802],[2.45196,1.99329,0.70374],[2.44127,1.99023,0.69765],[2.43309,1.98783,0.69238],[2.56854,2.02137,0.72377],[2.43541,1.97895,0.69072],[2.57144,2.01216,0.72249],[2.55556,2.00758,0.72265],[2.53542,2.00284,0.72148],[2.51799,1.9986,0.71883],[2.50244,1.9947,0.71522],[2.48733,1.9909,0.71156],[2.47281,1.98722,0.70773],[2.45997,1.98384,0.70296],[2.44859,1.98072,0.69734],[2.43774,1.97771,0.69151],[2.43677,1.96997,0.68977],[2.58832,2.00533,0.71222],[2.60484,2.00429,0.69548],[2.58158,1.99914,0.69587],[2.55917,1.99417,0.69612],[2.52719,1.98768,0.70324],[2.49582,1.98134,0.71054],[2.48014,1.97757,0.70748],[2.46791,1.97437,0.70224],[2.45614,1.97126,0.69686],[2.4437,1.96807,0.69216],[2.43738,1.96093,0.68936],[2.68532,2.01123,0.65055],[2.69356,2.00653,0.64321],[2.66442,2.00055,0.64657],[2.63678,1.99485,0.64945],[2.59395,1.98686,0.66307],[2.51643,1.97363,0.70123],[2.49121,1.9685,0.70462],[2.47664,1.96502,0.70096],[2.46253,1.96163,0.69721],[2.44935,1.9584,0.69302],[2.43822,1.95199,0.6888],[2.70148,2.00346,0.6408],[2.6757,1.99019,0.64427],[2.64671,1.98448,0.64794],[2.61271,1.97809,0.65549],[2.55268,1.96799,0.68138],[2.51013,1.96051,0.69636],[2.48697,1.95588,0.69856],[2.47058,1.95225,0.69637],[2.45678,1.94902,0.69258],[2.44432,1.94599,0.68805],[2.69863,1.99276,0.64324],[2.67515,1.9881,0.64546],[2.65142,1.98343,0.64821],[2.62683,1.97868,0.65188],[2.58822,1.97193,0.66539],[2.53943,1.96378,0.68645],[2.50766,1.95814,0.69654],[2.48908,1.95441,0.69789],[2.475,1.95134,0.69633],[2.46308,1.94859,0.69342],[2.45191,1.94597,0.69012],[2.44178,1.9435,0.68622]],"faces":[[851,853,837],[5,4,11],[5,7,13],[13,23,25],[7,12,13],[12,5,11],[12,7,5],[23,34,25],[34,23,22],[1121,1110,1109],[853,834,837],[834,853,868],[1079,1078,1089],[4,1,0],[1,4,5],[1,13,0],[1,5,13],[131,151,134],[37,48,25],[48,37,47],[1027,1029,1012],[1123,1133,1134],[835,851,837],[834,835,837],[789,775,787],[867,853,851],[853,867,868],[2,6,3],[2,4,0],[4,2,3],[109,111,95],[35,37,25],[37,35,47],[34,35,25],[94,109,95],[508,621,493],[479,465,478],[480,467,466],[852,869,854],[637,635,649],[186,172,185],[172,170,185],[188,172,186],[151,153,134],[277,279,260],[297,279,296],[279,277,296],[110,113,114],[21,14,13],[14,21,22],[14,23,13],[23,14,22],[6,10,3],[1136,1146,1137],[1135,1125,1124],[1125,1135,1136],[1135,1146,1136],[1147,1157,1158],[1147,1158,1148],[1146,1147,1137],[1157,1147,1156],[1147,1146,1156],[1141,1140,1161],[1145,1155,1167],[1144,1145,1134],[1144,1154,1145],[1133,1144,1134],[1144,1165,1154],[1043,1042,1041],[1042,1029,1041],[1029,1042,1043],[1121,1122,1110],[1123,1122,1133],[1140,1150,1161],[1158,1159,1148],[1159,1138,1148],[1139,1159,1149],[1159,1139,1138],[801,789,787],[775,773,787],[838,852,854],[852,838,836],[960,959,976],[961,960,976],[835,850,851],[867,883,868],[1122,1112,1110],[1112,1122,1123],[1029,1026,1041],[1026,1029,1027],[1026,1027,1012],[677,689,690],[111,129,112],[129,130,112],[434,421,433],[406,420,421],[390,392,378],[421,419,433],[420,419,421],[406,419,420],[508,607,621],[294,278,276],[275,294,276],[258,275,276],[275,258,257],[580,567,578],[280,297,299],[280,279,297],[379,377,391],[393,379,391],[691,679,678],[887,886,903],[788,803,790],[719,705,717],[153,169,170],[170,169,185],[258,240,257],[259,277,260],[154,135,155],[138,135,154],[113,135,114],[135,138,114],[152,138,154],[259,242,241],[242,259,260],[65,77,66],[52,50,38],[52,53,50],[50,39,38],[53,39,50],[38,39,36],[77,81,66],[26,28,24],[1125,1114,1124],[1043,1056,1067],[1099,1098,1109],[1110,1099,1109],[1162,1151,1161],[1151,1141,1161],[1152,1151,1162],[1165,1166,1154],[1155,1166,1167],[1154,1166,1145],[1166,1155,1145],[1144,1143,1165],[1160,1150,1140],[1160,1139,1149],[1139,1160,1140],[1150,1160,1161],[1159,1160,1149],[789,802,804],[801,802,789],[757,771,759],[939,923,921],[833,835,834],[833,850,835],[1069,1068,1078],[1079,1069,1078],[938,940,922],[940,938,937],[994,1010,1012],[689,702,690],[109,128,111],[128,129,111],[506,508,493],[521,509,507],[538,535,537],[434,435,421],[435,434,448],[449,435,448],[449,463,451],[463,449,448],[463,465,451],[465,463,478],[348,360,361],[390,405,392],[405,406,392],[405,419,406],[579,577,590],[577,564,563],[607,620,621],[579,593,536],[362,348,361],[364,362,361],[376,364,361],[364,376,378],[376,390,378],[278,295,298],[295,278,294],[295,315,298],[494,481,480],[481,482,480],[482,481,494],[468,467,480],[482,468,480],[610,609,622],[609,635,622],[567,565,578],[592,580,578],[594,592,606],[592,594,580],[320,319,333],[319,320,303],[319,302,318],[302,319,303],[300,280,299],[297,300,299],[409,408,422],[408,409,407],[409,395,407],[464,452,450],[467,452,466],[452,464,466],[436,423,422],[423,409,422],[651,649,663],[651,637,649],[791,788,790],[647,636,634],[734,733,746],[730,719,717],[719,730,732],[202,203,188],[203,202,220],[202,188,186],[221,238,222],[221,203,220],[239,240,222],[238,239,222],[240,239,257],[152,139,138],[139,152,136],[242,224,241],[53,63,66],[63,65,66],[52,63,53],[63,52,65],[81,80,93],[80,81,77],[80,77,65],[96,110,114],[110,96,93],[96,81,93],[9,15,17],[9,10,6],[16,15,24],[15,16,17],[28,16,24],[39,27,36],[26,27,28],[1114,1113,1124],[1054,1056,1043],[1054,1065,1056],[1056,1065,1067],[1152,1163,1164],[1163,1152,1162],[1165,1153,1164],[1143,1153,1165],[1153,1152,1164],[744,745,732],[745,744,757],[730,744,732],[758,757,759],[758,745,757],[758,773,775],[773,758,759],[789,758,775],[745,758,732],[758,789,804],[758,719,732],[772,773,759],[771,772,759],[773,772,787],[959,942,941],[942,939,941],[942,923,939],[960,942,959],[819,834,804],[819,833,834],[902,885,901],[918,902,901],[885,884,901],[883,884,868],[884,885,868],[956,940,937],[956,974,958],[991,977,974],[974,975,958],[975,977,958],[977,975,974],[702,703,690],[705,703,717],[150,151,131],[130,150,131],[492,479,478],[479,492,493],[519,520,508],[534,549,536],[566,577,579],[577,566,564],[593,605,607],[605,620,607],[591,579,590],[591,593,579],[591,605,593],[522,607,508],[522,593,607],[593,522,536],[520,522,508],[534,522,520],[522,534,536],[315,317,298],[317,315,332],[334,317,332],[321,320,333],[350,337,349],[495,494,507],[495,482,494],[509,495,507],[608,610,595],[608,609,610],[594,608,595],[608,594,606],[301,316,318],[302,301,318],[301,300,316],[394,379,393],[394,393,407],[395,394,407],[452,437,450],[437,436,450],[437,423,436],[803,805,790],[818,805,803],[805,818,820],[637,623,635],[635,623,622],[623,610,622],[904,903,921],[904,887,903],[923,904,921],[869,870,854],[886,870,869],[887,870,886],[633,647,634],[621,633,634],[620,633,621],[648,650,636],[647,648,636],[676,677,664],[676,689,677],[791,777,788],[205,221,222],[221,205,203],[168,153,151],[168,169,153],[140,137,156],[137,140,120],[133,116,136],[118,119,100],[101,119,120],[119,101,100],[119,137,120],[116,115,136],[115,139,136],[204,206,207],[157,140,156],[1102,1113,1114],[1053,1043,1041],[1053,1054,1043],[1065,1076,1067],[1088,1087,1098],[1099,1088,1098],[817,819,804],[802,817,804],[819,817,833],[902,919,920],[919,902,918],[919,922,920],[919,938,922],[959,995,976],[995,959,941],[940,957,958],[957,956,958],[956,957,940],[1069,1058,1068],[1009,1010,994],[991,1009,994],[1009,1026,1012],[1010,1009,1012],[977,992,994],[992,991,994],[991,992,977],[92,107,79],[94,108,109],[108,94,79],[107,108,79],[75,51,62],[505,506,493],[492,505,493],[506,505,508],[505,519,508],[538,523,535],[535,523,521],[523,509,521],[553,565,567],[565,553,552],[549,550,536],[566,550,564],[550,549,563],[564,550,563],[550,579,536],[550,566,579],[347,334,332],[334,347,348],[347,360,348],[335,321,333],[363,350,349],[379,365,377],[365,363,377],[363,365,350],[648,662,650],[650,662,664],[662,676,664],[707,704,706],[707,693,704],[692,679,691],[704,692,691],[693,692,704],[747,734,746],[777,776,788],[223,224,207],[206,223,207],[224,223,241],[1087,1077,1086],[1077,1076,1086],[1076,1077,1067],[1088,1077,1087],[978,961,976],[982,1000,983],[999,1000,982],[1013,1000,999],[1058,1057,1068],[49,51,48],[51,49,62],[49,61,62],[49,48,47],[61,49,47],[553,551,552],[551,538,537],[337,336,349],[335,336,337],[336,335,333],[718,707,706],[720,733,734],[733,720,731],[720,718,731],[718,720,707],[776,774,788],[774,776,777],[117,132,118],[132,117,133],[133,117,116],[171,157,156],[189,187,204],[189,204,207],[1102,1111,1113],[1101,1111,1102],[1100,1101,1102],[1091,1100,1102],[1090,1079,1089],[1100,1090,1089],[1090,1100,1091],[1011,1014,996],[1014,1011,995],[1011,978,995],[1033,1030,1044],[1030,995,1044],[1030,1014,995],[995,993,976],[993,978,976],[978,993,995],[979,1011,996],[1011,979,978],[998,1013,999],[1017,1033,1032],[1017,1030,1033],[1000,1001,983],[1057,1066,1068],[1066,1057,1055],[78,91,92],[78,92,79],[551,539,538],[539,551,553],[821,805,820],[838,821,836],[821,820,836],[666,665,678],[679,666,678],[760,748,747],[760,747,746],[774,762,761],[762,774,777],[762,760,761],[760,762,748],[171,173,157],[173,171,187],[189,173,187],[1013,1018,1028],[998,1018,1013],[1018,1032,1028],[1018,1017,1032],[1014,1016,996],[1030,1016,1014],[1017,1016,1030],[1045,1034,1031],[1034,1019,1031],[1057,1046,1055],[1046,1045,1055],[1046,1034,1045],[1001,1015,1002],[1015,1019,1002],[1019,1015,1031],[1015,1001,1000],[76,78,64],[78,76,91],[51,76,64],[75,76,51],[322,335,337],[335,322,321],[665,652,663],[666,652,665],[652,651,663],[1132,1120,1131],[1132,1121,1120],[41,40,54],[41,54,55],[28,41,29],[41,28,40],[10,4,3],[4,10,11],[1147,1138,1137],[1138,1147,1148],[1060,1072,1061],[1072,1060,1071],[1072,1062,1061],[1062,1072,1073],[1074,1062,1073],[1063,1062,1074],[1086,1085,1096],[1097,1086,1096],[1121,1108,1120],[1108,1121,1109],[1098,1108,1109],[1108,1098,1097],[1108,1097,1096],[1108,1096,1107],[1132,1122,1121],[1122,1132,1133],[1138,1139,1127],[1139,1128,1127],[447,432,446],[432,447,433],[447,434,433],[434,447,448],[418,432,433],[419,418,433],[369,384,370],[384,369,383],[369,382,383],[382,369,368],[46,35,34],[35,46,47],[263,282,283],[263,283,264],[246,263,264],[263,246,245],[265,246,264],[247,246,265],[246,227,245],[227,246,228],[211,227,228],[227,211,210],[246,229,228],[229,246,247],[229,248,230],[229,247,248],[229,211,228],[211,229,212],[40,39,54],[39,53,54],[67,53,66],[53,67,54],[81,67,66],[67,81,82],[1062,1051,1061],[1061,1051,1050],[1051,1062,1063],[1051,1063,1052],[1075,1084,1085],[1084,1075,1074],[1075,1063,1074],[1075,1064,1063],[1151,1152,1142],[1141,1151,1142],[1129,1139,1140],[1139,1129,1128],[1143,1132,1131],[1142,1143,1131],[1132,1143,1133],[1143,1144,1133],[1072,1083,1073],[1083,1072,1082],[1083,1074,1073],[1083,1084,1074],[910,894,893],[894,910,911],[812,798,797],[798,812,813],[797,798,783],[798,784,783],[800,816,801],[816,800,815],[833,832,849],[850,833,849],[883,899,900],[899,883,882],[844,861,845],[861,844,860],[828,844,845],[844,828,827],[827,828,811],[828,812,811],[812,828,813],[828,829,813],[895,894,911],[912,895,911],[128,149,129],[149,128,148],[165,147,164],[147,165,148],[128,127,148],[127,147,148],[460,461,445],[461,446,445],[405,418,419],[418,405,404],[405,389,404],[405,390,389],[432,431,446],[446,431,445],[415,429,416],[429,430,416],[376,360,375],[360,376,361],[390,376,389],[376,375,389],[313,331,314],[331,313,330],[331,315,314],[315,331,332],[315,295,314],[295,294,314],[288,307,308],[307,288,287],[325,307,324],[307,325,308],[369,353,368],[353,369,354],[367,353,352],[353,367,368],[353,339,352],[339,353,340],[339,325,324],[325,339,340],[339,351,352],[351,339,338],[282,302,283],[302,303,283],[263,281,282],[281,263,262],[242,261,243],[261,242,260],[279,261,260],[280,261,279],[261,281,262],[281,261,280],[395,410,396],[410,395,409],[381,395,396],[395,381,380],[381,382,368],[367,381,368],[905,925,906],[905,924,925],[615,627,628],[627,615,614],[599,598,613],[599,613,614],[615,599,614],[599,615,600],[574,560,573],[560,559,573],[560,575,561],[575,560,574],[600,587,586],[587,600,601],[602,587,601],[587,602,588],[587,574,573],[586,587,573],[587,575,574],[575,587,588],[768,755,754],[768,769,755],[784,768,783],[768,784,769],[753,768,754],[768,753,767],[726,727,713],[727,714,713],[201,202,185],[202,186,185],[201,184,200],[184,201,185],[239,238,256],[257,239,256],[248,231,230],[249,231,248],[303,284,283],[284,303,304],[284,265,264],[283,284,264],[284,266,265],[266,284,285],[249,266,267],[266,249,248],[247,266,248],[266,247,265],[266,285,286],[266,286,267],[101,85,100],[85,101,86],[85,71,70],[71,85,86],[85,99,100],[99,85,84],[84,85,69],[69,85,70],[56,69,70],[57,56,70],[244,263,245],[263,244,262],[261,244,243],[244,261,262],[211,193,210],[193,192,210],[224,208,207],[208,224,225],[224,242,243],[224,243,225],[192,191,210],[191,209,210],[81,96,82],[96,97,82],[97,83,82],[83,97,98],[83,99,84],[99,83,98],[10,9,18],[9,17,18],[16,28,29],[17,16,29],[28,27,40],[27,39,40],[1072,1081,1082],[1081,1072,1071],[1087,1086,1097],[1098,1087,1097],[1119,1108,1107],[1108,1119,1120],[1152,1153,1142],[1153,1143,1142],[1104,1094,1093],[1094,1104,1105],[1094,1083,1082],[1093,1094,1082],[757,744,756],[744,743,756],[744,729,743],[744,730,729],[784,770,769],[770,784,785],[769,770,755],[755,770,756],[770,757,756],[770,771,757],[770,786,771],[786,770,785],[786,800,801],[786,801,787],[772,786,787],[786,772,771],[892,910,893],[910,892,909],[925,907,906],[907,925,926],[927,907,926],[907,927,908],[927,909,908],[927,928,909],[808,794,793],[794,808,809],[782,768,767],[768,782,783],[786,799,800],[799,786,785],[800,799,815],[799,814,815],[799,798,813],[814,799,813],[784,799,785],[798,799,784],[850,866,851],[866,867,851],[883,866,882],[866,883,867],[866,850,849],[865,866,849],[866,881,882],[881,866,865],[831,816,815],[816,831,832],[846,828,845],[828,846,829],[884,883,900],[901,884,900],[894,877,893],[877,876,893],[895,877,894],[877,895,878],[877,861,860],[876,877,860],[930,931,912],[930,912,911],[825,808,824],[808,825,809],[896,879,878],[895,896,878],[925,945,926],[945,925,944],[954,971,972],[971,954,953],[971,989,972],[989,971,988],[989,973,972],[973,989,990],[991,973,990],[973,991,974],[730,716,729],[716,730,717],[703,716,717],[716,703,702],[716,701,715],[716,702,701],[166,149,148],[165,166,148],[166,150,149],[150,166,167],[149,150,129],[129,150,130],[126,107,125],[107,106,125],[45,60,46],[60,45,59],[519,534,520],[534,519,533],[519,532,533],[532,519,518],[532,517,531],[517,532,518],[511,496,510],[496,511,497],[511,498,497],[511,512,498],[549,534,533],[548,549,533],[492,477,491],[477,492,478],[490,477,476],[477,490,491],[430,417,416],[431,417,430],[418,417,432],[417,431,432],[605,619,620],[619,605,604],[605,591,604],[604,591,590],[457,441,456],[441,457,442],[341,353,354],[353,341,340],[355,343,342],[343,355,356],[355,369,370],[369,355,354],[371,355,370],[355,371,356],[355,341,354],[341,355,342],[372,387,373],[387,372,386],[372,358,357],[372,373,358],[371,372,356],[356,372,357],[317,278,298],[317,378,222],[317,364,378],[362,317,348],[317,258,276],[240,317,222],[278,317,276],[317,240,258],[317,362,364],[317,334,348],[385,372,371],[372,385,386],[384,385,370],[385,371,370],[385,401,386],[385,400,401],[399,385,384],[385,399,400],[303,321,304],[320,321,303],[381,366,380],[366,381,367],[366,367,352],[351,366,352],[337,350,338],[350,351,338],[483,496,497],[484,483,497],[483,495,496],[495,483,482],[496,495,510],[495,509,510],[570,583,571],[571,583,584],[583,570,569],[582,583,569],[581,568,567],[580,581,567],[581,594,595],[594,581,580],[581,582,569],[568,581,569],[301,302,282],[281,301,282],[301,281,280],[300,301,280],[394,395,380],[379,394,380],[453,437,452],[437,453,438],[468,453,467],[453,452,467],[424,410,409],[423,424,409],[424,437,438],[437,424,423],[624,623,638],[638,623,637],[888,904,905],[904,888,887],[905,904,924],[904,923,924],[907,889,906],[889,907,890],[889,905,906],[889,888,905],[603,604,590],[589,603,590],[603,619,604],[619,603,618],[602,603,588],[603,589,588],[701,700,715],[700,714,715],[600,616,601],[615,616,600],[585,586,573],[572,585,573],[585,571,584],[585,572,571],[585,599,600],[585,600,586],[599,585,598],[598,585,584],[562,549,548],[549,562,563],[752,753,740],[752,740,739],[753,752,767],[767,752,766],[740,725,739],[725,740,726],[743,742,756],[742,755,756],[153,205,134],[172,205,170],[205,153,170],[203,205,188],[221,237,238],[237,221,220],[238,237,256],[237,255,256],[168,150,167],[150,168,151],[168,184,185],[169,168,185],[213,229,230],[229,213,212],[231,214,230],[214,213,230],[127,146,147],[146,127,126],[180,165,164],[180,181,165],[71,58,70],[58,57,70],[97,115,98],[115,116,98],[96,115,97],[115,96,114],[138,115,114],[139,115,138],[20,21,13],[12,20,13],[30,17,29],[17,30,18],[226,227,210],[209,226,210],[226,208,225],[208,226,209],[227,226,245],[226,244,245],[243,226,225],[244,226,243],[101,102,86],[86,102,87],[102,101,120],[121,102,120],[103,102,122],[102,121,122],[191,190,209],[190,208,209],[141,121,120],[140,141,120],[121,141,122],[141,142,122],[157,141,140],[141,157,158],[142,141,159],[141,158,159],[54,68,55],[67,68,54],[56,68,69],[68,56,55],[68,67,82],[83,68,82],[68,84,69],[68,83,84],[1080,1069,1079],[1069,1080,1070],[1080,1081,1071],[1080,1071,1070],[1102,1115,1103],[1115,1102,1114],[1126,1125,1136],[1126,1136,1137],[1138,1126,1137],[1126,1138,1127],[1126,1115,1114],[1126,1114,1125],[1126,1116,1115],[1116,1126,1127],[1128,1116,1127],[1117,1116,1128],[1104,1116,1105],[1116,1117,1105],[1115,1116,1103],[1116,1104,1103],[1063,1053,1052],[1064,1053,1063],[1040,1053,1041],[1053,1040,1052],[1065,1053,1064],[1053,1065,1054],[1075,1076,1064],[1076,1065,1064],[1086,1076,1085],[1076,1075,1085],[1130,1129,1140],[1141,1130,1140],[1130,1141,1142],[1130,1142,1131],[1120,1130,1131],[1119,1130,1120],[1118,1130,1119],[1130,1118,1129],[1117,1118,1105],[1118,1106,1105],[1129,1118,1128],[1118,1117,1128],[1118,1119,1107],[1106,1118,1107],[1085,1095,1096],[1084,1095,1085],[1096,1095,1107],[1095,1106,1107],[1083,1095,1084],[1094,1095,1083],[1106,1095,1105],[1095,1094,1105],[874,857,873],[857,874,858],[924,943,925],[925,943,944],[923,943,924],[942,943,923],[943,960,961],[943,942,960],[795,794,809],[795,809,810],[896,880,879],[880,896,897],[880,881,865],[880,865,864],[816,817,801],[817,802,801],[833,817,832],[817,816,832],[830,846,847],[846,830,829],[829,830,813],[830,814,813],[814,830,815],[830,831,815],[830,848,831],[848,830,847],[848,865,849],[865,848,864],[832,848,849],[831,848,832],[880,863,879],[863,880,864],[863,848,847],[848,863,864],[919,918,937],[938,919,937],[917,901,900],[917,918,901],[880,898,881],[898,880,897],[898,899,882],[881,898,882],[928,929,909],[929,910,909],[910,929,911],[929,930,911],[842,857,858],[857,842,841],[842,825,824],[842,824,841],[980,964,963],[964,980,981],[918,936,937],[917,936,918],[989,1007,990],[990,1007,1008],[1025,1040,1041],[1026,1025,1041],[1007,1025,1008],[1025,1007,1024],[1025,1009,1008],[1009,1025,1026],[1009,990,1008],[1009,991,990],[108,107,126],[127,108,126],[108,127,128],[108,128,109],[142,123,122],[123,142,143],[123,104,103],[123,103,122],[89,75,74],[75,89,90],[75,61,74],[61,75,62],[61,73,74],[73,61,60],[46,61,47],[60,61,46],[72,58,71],[58,72,59],[72,71,86],[72,86,87],[72,60,59],[72,73,60],[505,492,491],[505,491,504],[519,505,518],[518,505,504],[547,562,548],[562,547,561],[532,547,533],[547,548,533],[517,516,531],[516,530,531],[475,461,460],[461,475,476],[503,517,518],[503,518,504],[503,516,517],[516,503,502],[491,503,504],[490,503,491],[509,523,510],[523,524,510],[525,511,510],[524,525,510],[447,462,448],[462,463,448],[462,447,446],[461,462,446],[462,477,478],[463,462,478],[462,461,476],[477,462,476],[403,418,404],[403,417,418],[427,441,442],[428,427,442],[399,414,400],[414,399,413],[400,414,401],[401,414,415],[414,427,428],[427,414,413],[414,429,415],[414,428,429],[343,328,342],[342,328,327],[344,343,356],[344,356,357],[331,347,332],[346,347,331],[360,359,375],[375,359,374],[373,359,358],[359,373,374],[359,347,346],[347,359,360],[294,293,314],[293,313,314],[313,293,312],[293,292,312],[271,252,270],[252,271,253],[328,310,327],[310,328,311],[326,325,340],[341,326,340],[326,341,342],[326,342,327],[325,326,308],[326,309,308],[326,310,309],[310,326,327],[306,307,287],[306,287,286],[365,366,351],[350,365,351],[366,365,380],[365,379,380],[441,440,456],[440,455,456],[581,596,582],[596,581,595],[424,411,410],[411,424,425],[398,384,383],[398,399,384],[399,398,413],[398,412,413],[805,806,790],[806,791,790],[856,857,841],[840,856,841],[645,632,631],[632,645,646],[632,619,618],[631,632,618],[619,632,620],[632,633,620],[647,632,646],[633,632,647],[661,662,647],[662,648,647],[676,662,675],[662,661,675],[714,699,713],[700,699,714],[660,661,647],[660,647,646],[645,660,646],[660,645,659],[661,660,675],[660,674,675],[660,659,673],[674,660,673],[702,688,701],[688,702,689],[688,676,675],[676,688,689],[639,624,638],[624,639,625],[626,627,614],[613,626,614],[626,641,627],[641,626,640],[639,626,625],[626,639,640],[630,645,631],[645,630,644],[576,575,588],[589,576,588],[576,589,590],[577,576,590],[575,576,561],[576,562,561],[562,576,563],[576,577,563],[680,692,693],[692,680,679],[645,658,659],[658,645,644],[794,780,793],[780,779,793],[779,778,793],[778,792,793],[778,791,792],[778,777,791],[741,727,726],[740,741,726],[741,753,754],[753,741,740],[755,741,754],[742,741,755],[728,741,742],[741,728,727],[716,728,729],[728,716,715],[727,728,714],[714,728,715],[729,728,743],[728,742,743],[219,202,201],[202,219,220],[219,201,200],[218,219,200],[235,252,253],[252,235,234],[195,180,179],[180,195,196],[195,214,196],[214,195,213],[235,217,234],[217,235,218],[217,218,200],[199,217,200],[168,183,184],[183,168,167],[184,183,200],[183,199,200],[163,180,164],[180,163,179],[147,163,164],[146,163,147],[21,33,22],[33,34,22],[33,46,34],[33,45,46],[42,41,55],[56,42,55],[41,42,29],[42,30,29],[160,142,159],[142,160,143],[160,161,144],[160,144,143],[158,174,159],[174,175,159],[174,190,191],[174,191,175],[875,892,893],[876,875,893],[875,876,860],[859,875,860],[874,875,858],[875,859,858],[891,875,874],[875,891,892],[891,907,908],[907,891,890],[892,891,909],[909,891,908],[891,874,873],[890,891,873],[796,797,783],[782,796,783],[812,796,811],[796,812,797],[796,795,810],[796,810,811],[861,862,845],[862,846,845],[862,877,878],[877,862,861],[846,862,847],[862,863,847],[879,862,878],[863,862,879],[844,843,860],[843,859,860],[843,842,858],[859,843,858],[933,952,953],[933,953,934],[952,933,951],[933,932,951],[932,950,951],[950,932,931],[946,927,926],[945,946,926],[964,946,963],[946,945,963],[955,973,974],[956,955,974],[955,954,972],[973,955,972],[936,955,937],[955,956,937],[954,935,953],[953,935,934],[955,935,954],[935,955,936],[1049,1060,1061],[1049,1061,1050],[1038,1049,1050],[1049,1038,1037],[1049,1059,1060],[1059,1049,1048],[1060,1059,1071],[1071,1059,1070],[1059,1069,1070],[1059,1058,1069],[987,1006,988],[1006,987,1005],[1006,989,988],[1006,1007,989],[1039,1025,1024],[1025,1039,1040],[1039,1051,1052],[1040,1039,1052],[1051,1039,1050],[1039,1038,1050],[107,91,106],[92,91,107],[124,123,143],[144,124,143],[72,88,73],[88,72,87],[102,88,87],[88,102,103],[73,88,74],[88,89,74],[104,88,103],[89,88,104],[546,560,561],[547,546,561],[546,532,531],[546,547,532],[560,546,559],[546,545,559],[530,546,531],[545,546,530],[554,568,569],[555,554,569],[568,554,567],[554,553,567],[570,556,569],[556,555,569],[559,558,573],[558,572,573],[402,387,386],[401,402,386],[402,415,416],[402,401,415],[417,402,416],[403,402,417],[388,402,403],[402,388,387],[375,388,389],[388,375,374],[387,388,373],[373,388,374],[389,388,404],[388,403,404],[471,457,456],[471,472,457],[470,471,455],[455,471,456],[444,431,430],[431,444,445],[444,460,445],[444,459,460],[472,458,457],[458,472,473],[345,359,346],[359,345,358],[345,331,330],[345,346,331],[358,345,357],[345,344,357],[345,329,344],[329,345,330],[313,329,330],[329,313,312],[328,329,311],[311,329,312],[329,328,343],[344,329,343],[255,274,256],[273,274,255],[274,257,256],[274,275,257],[293,274,292],[274,273,292],[274,293,294],[275,274,294],[272,273,255],[272,255,254],[271,272,253],[253,272,254],[289,269,288],[269,289,270],[289,288,308],[309,289,308],[286,268,267],[287,268,286],[288,268,287],[269,268,288],[339,323,338],[323,339,324],[307,323,324],[306,323,307],[483,469,482],[469,468,482],[469,483,484],[470,469,484],[439,424,438],[424,439,425],[426,427,413],[412,426,413],[426,411,425],[411,426,412],[427,426,441],[426,440,441],[439,426,425],[426,439,440],[612,626,613],[626,612,625],[410,397,396],[411,397,410],[381,397,382],[397,381,396],[398,397,412],[397,411,412],[382,397,383],[397,398,383],[807,808,793],[792,807,793],[806,807,791],[791,807,792],[838,855,839],[855,838,854],[855,856,840],[855,840,839],[687,674,673],[686,687,673],[687,699,700],[699,687,686],[674,687,675],[687,688,675],[687,700,701],[688,687,701],[629,615,628],[629,616,615],[617,602,601],[616,617,601],[617,603,602],[603,617,618],[629,617,616],[617,629,630],[617,631,618],[617,630,631],[748,736,735],[736,748,749],[748,735,734],[747,748,734],[659,672,673],[658,672,659],[672,686,673],[672,685,686],[751,752,739],[738,751,739],[796,781,795],[781,796,782],[781,782,767],[781,767,766],[795,781,794],[781,780,794],[236,237,220],[219,236,220],[237,236,255],[255,236,254],[236,219,218],[235,236,218],[236,235,253],[236,253,254],[194,211,212],[194,193,211],[194,195,179],[194,179,178],[213,194,212],[195,194,213],[217,216,234],[216,233,234],[182,166,165],[181,182,165],[166,182,167],[182,183,167],[163,162,179],[179,162,178],[99,117,100],[117,118,100],[117,99,98],[116,117,98],[45,44,59],[44,58,59],[19,20,12],[19,12,11],[19,10,18],[10,19,11],[30,19,18],[31,19,30],[176,191,192],[191,176,175],[176,160,159],[175,176,159],[208,189,207],[190,189,208],[1090,1080,1079],[1090,1091,1080],[1081,1092,1082],[1092,1093,1082],[1092,1104,1093],[1104,1092,1103],[1080,1092,1081],[1091,1092,1080],[1092,1102,1103],[1092,1091,1102],[825,826,809],[809,826,810],[826,827,811],[810,826,811],[842,826,825],[843,826,842],[826,844,827],[826,843,844],[914,898,897],[898,914,915],[914,933,934],[915,914,934],[979,997,980],[997,979,996],[998,999,982],[998,982,981],[980,998,981],[997,998,980],[1036,1049,1037],[1049,1036,1048],[1047,1059,1048],[1059,1047,1058],[1036,1047,1048],[1047,1036,1035],[950,969,951],[969,950,968],[984,1001,1002],[1001,984,983],[899,916,900],[916,917,900],[898,916,899],[916,898,915],[916,936,917],[916,935,936],[916,915,934],[935,916,934],[1039,1023,1038],[1023,1039,1024],[1038,1023,1037],[1037,1023,1022],[1007,1023,1024],[1006,1023,1007],[1022,1023,1005],[1023,1006,1005],[89,105,90],[105,89,104],[91,105,106],[105,91,90],[123,105,104],[124,105,123],[106,105,125],[105,124,125],[523,539,524],[539,523,538],[474,475,460],[459,474,460],[474,458,473],[458,474,459],[474,489,475],[489,474,488],[503,489,502],[489,503,490],[489,490,476],[475,489,476],[489,501,502],[501,489,488],[545,544,559],[544,558,559],[557,570,571],[557,556,570],[544,557,558],[557,544,543],[572,557,571],[558,557,572],[529,545,530],[529,544,545],[474,487,488],[487,474,473],[487,501,488],[501,487,500],[472,487,473],[486,487,472],[499,487,486],[487,499,500],[512,499,498],[513,499,512],[514,499,513],[499,514,500],[499,485,498],[485,499,486],[485,484,497],[498,485,497],[485,471,470],[485,470,484],[471,485,472],[485,486,472],[429,443,430],[443,444,430],[428,443,429],[443,428,442],[444,443,459],[443,458,459],[457,443,442],[458,443,457],[292,291,312],[291,311,312],[273,291,292],[272,291,273],[250,231,249],[231,250,232],[250,249,267],[268,250,267],[284,305,285],[305,284,304],[285,305,286],[305,306,286],[454,453,468],[469,454,468],[454,470,455],[454,469,470],[453,454,438],[454,439,438],[440,454,455],[439,454,440],[611,596,595],[610,611,595],[611,623,624],[623,611,610],[611,624,625],[612,611,625],[597,611,612],[611,597,596],[597,598,584],[583,597,584],[597,583,582],[596,597,582],[598,597,613],[597,612,613],[821,806,805],[821,822,806],[821,838,839],[822,821,839],[823,822,839],[840,823,839],[824,823,841],[823,840,841],[823,807,806],[822,823,806],[808,823,824],[807,823,808],[888,871,887],[871,870,887],[871,855,854],[870,871,854],[630,643,644],[629,643,630],[680,666,679],[667,666,680],[723,722,737],[722,736,737],[778,762,777],[762,778,763],[748,762,749],[749,762,763],[672,684,685],[684,672,671],[750,736,749],[736,750,737],[750,751,738],[750,738,737],[765,781,766],[781,765,780],[752,765,766],[751,765,752],[197,180,196],[180,197,181],[145,126,125],[145,146,126],[145,124,144],[124,145,125],[145,163,146],[145,162,163],[161,145,144],[162,145,161],[42,43,30],[43,31,30],[43,56,57],[43,42,56],[58,43,57],[44,43,58],[32,19,31],[19,32,20],[32,43,44],[43,32,31],[20,32,21],[32,33,21],[33,32,45],[32,44,45],[162,177,178],[177,162,161],[194,177,193],[177,194,178],[160,177,161],[176,177,160],[193,177,192],[177,176,192],[157,173,158],[173,174,158],[174,173,190],[173,189,190],[931,913,912],[932,913,931],[913,895,912],[913,896,895],[933,913,932],[914,913,933],[896,913,897],[913,914,897],[962,945,944],[945,962,963],[943,962,944],[962,943,961],[962,980,963],[962,979,980],[978,962,961],[979,962,978],[1018,998,997],[1017,1018,997],[1016,997,996],[1016,1017,997],[1047,1046,1058],[1046,1057,1058],[1046,1047,1035],[1034,1046,1035],[930,949,931],[949,950,931],[927,947,928],[946,947,927],[971,970,988],[970,987,988],[970,971,953],[952,970,953],[970,952,951],[969,970,951],[76,75,90],[91,76,90],[540,525,524],[539,540,524],[554,540,553],[540,539,553],[541,554,555],[541,540,554],[514,515,500],[515,501,500],[515,516,502],[501,515,502],[516,515,530],[515,529,530],[515,528,529],[528,515,514],[528,514,513],[527,528,513],[544,528,543],[529,528,544],[310,290,309],[290,289,309],[290,271,270],[289,290,270],[290,310,311],[291,290,311],[290,272,271],[290,291,272],[252,251,270],[251,269,270],[251,252,234],[233,251,234],[251,268,269],[251,250,268],[250,251,232],[251,233,232],[322,337,338],[323,322,338],[322,323,306],[305,322,306],[321,322,304],[322,305,304],[857,872,873],[856,872,857],[872,889,890],[872,890,873],[855,872,856],[871,872,855],[889,872,888],[872,871,888],[627,642,628],[641,642,627],[642,629,628],[642,643,629],[639,653,640],[653,654,640],[653,668,654],[668,653,667],[652,638,637],[651,652,637],[652,666,667],[653,652,667],[652,639,638],[652,653,639],[735,721,734],[721,720,734],[736,721,735],[722,721,736],[685,698,686],[698,699,686],[684,698,685],[697,698,684],[764,749,763],[764,750,749],[764,778,779],[778,764,763],[750,764,751],[764,765,751],[780,764,779],[765,764,780],[216,215,233],[233,215,232],[215,214,231],[215,231,232],[214,215,196],[215,197,196],[215,198,197],[198,215,216],[198,217,199],[198,216,217],[183,198,199],[182,198,183],[198,182,181],[197,198,181],[1003,984,1002],[984,1003,985],[1021,1036,1037],[1021,1037,1022],[1021,1022,1005],[1004,1021,1005],[986,970,969],[970,986,987],[986,969,968],[986,968,985],[987,986,1005],[986,1004,1005],[1003,986,985],[986,1003,1004],[984,967,983],[967,966,983],[968,967,985],[967,984,985],[950,967,968],[949,967,950],[948,967,949],[967,948,966],[929,948,930],[948,949,930],[948,929,928],[947,948,928],[965,948,947],[948,965,966],[965,964,981],[982,965,981],[966,965,983],[965,982,983],[965,946,964],[965,947,946],[528,542,543],[542,528,527],[557,542,556],[542,557,543],[556,542,555],[542,541,555],[542,526,541],[526,542,527],[511,526,512],[525,526,511],[526,513,512],[526,527,513],[540,526,525],[541,526,540],[657,672,658],[672,657,671],[657,658,644],[643,657,644],[708,694,693],[707,708,693],[721,708,720],[720,708,707],[710,711,696],[711,697,696],[1020,1003,1002],[1019,1020,1002],[1020,1034,1035],[1034,1020,1019],[1020,1021,1004],[1003,1020,1004],[1036,1020,1035],[1021,1020,1036],[683,697,684],[697,683,696],[696,683,695],[683,682,695],[681,667,680],[681,668,667],[681,680,693],[694,681,693],[682,681,695],[681,694,695],[709,722,723],[710,709,723],[709,696,695],[709,710,696],[709,721,722],[709,708,721],[694,709,695],[708,709,694],[724,723,737],[738,724,737],[725,724,739],[724,738,739],[724,710,723],[724,711,710],[724,712,711],[712,724,725],[712,726,713],[712,725,726],[699,712,713],[698,712,699],[712,698,697],[711,712,697],[670,684,671],[670,683,684],[655,641,640],[654,655,640],[657,656,671],[656,670,671],[642,656,643],[656,657,643],[656,642,641],[655,656,641],[669,656,655],[656,669,670],[669,681,682],[681,669,668],[683,669,682],[670,669,683],[668,669,654],[669,655,654]],"uv":[[0.0,0.872146],[0.007801,0.85312],[0.007979,0.888128],[0.012766,0.890411],[0.012766,0.863014],[0.015603,0.834094],[0.015957,0.90411],[0.023404,0.815068],[0.023936,0.920091],[0.025532,0.917808],[0.025532,0.890411],[0.025532,0.863014],[0.025532,0.835616],[0.025532,0.808219],[0.031206,0.796043],[0.031915,0.936073],[0.038298,0.945205],[0.038298,0.917808],[0.038298,0.890411],[0.038298,0.863014],[0.038298,0.835616],[0.038298,0.808219],[0.038298,0.780822],[0.039007,0.777017],[0.039894,0.952055],[0.046809,0.757991],[0.047872,0.968037],[0.051064,0.972603],[0.051064,0.945205],[0.051064,0.917808],[0.051064,0.890411],[0.051064,0.863014],[0.051064,0.835616],[0.051064,0.808219],[0.051064,0.780822],[0.051064,0.753425],[0.055851,0.984018],[0.056596,0.740639],[0.06383,1.0],[0.06383,0.972603],[0.06383,0.945205],[0.06383,0.917808],[0.06383,0.890411],[0.06383,0.863014],[0.06383,0.835616],[0.06383,0.808219],[0.06383,0.780822],[0.06383,0.753425],[0.06383,0.726027],[0.066383,0.723288],[0.074468,0.999429],[0.07617,0.705936],[0.076596,1.0],[0.076596,0.972603],[0.076596,0.945205],[0.076596,0.917808],[0.076596,0.890411],[0.076596,0.863014],[0.076596,0.835616],[0.076596,0.808219],[0.076596,0.780822],[0.076596,0.753425],[0.076596,0.726027],[0.085106,0.998858],[0.085957,0.688584],[0.089362,1.0],[0.089362,0.972603],[0.089362,0.945205],[0.089362,0.917808],[0.089362,0.890411],[0.089362,0.863014],[0.089362,0.835616],[0.089362,0.808219],[0.089362,0.780822],[0.089362,0.753425],[0.089362,0.726027],[0.089362,0.69863],[0.095745,0.998288],[0.095745,0.671233],[0.10152,0.651011],[0.102128,1.0],[0.102128,0.972603],[0.102128,0.945205],[0.102128,0.917808],[0.102128,0.890411],[0.102128,0.863014],[0.102128,0.835616],[0.102128,0.808219],[0.102128,0.780822],[0.102128,0.753425],[0.102128,0.726027],[0.102128,0.69863],[0.102128,0.671233],[0.106383,0.997717],[0.107295,0.630789],[0.11307,0.610568],[0.114894,0.972603],[0.114894,0.945205],[0.114894,0.917808],[0.114894,0.890411],[0.114894,0.863014],[0.114894,0.835616],[0.114894,0.808219],[0.114894,0.780822],[0.114894,0.753425],[0.114894,0.726027],[0.114894,0.69863],[0.114894,0.671233],[0.114894,0.643836],[0.114894,0.616438],[0.117021,0.997146],[0.118845,0.590346],[0.12462,0.570124],[0.12766,0.996575],[0.12766,0.972603],[0.12766,0.945205],[0.12766,0.917808],[0.12766,0.890411],[0.12766,0.863014],[0.12766,0.853881],[0.12766,0.835616],[0.12766,0.808219],[0.12766,0.780822],[0.12766,0.753425],[0.12766,0.726027],[0.12766,0.69863],[0.12766,0.671233],[0.12766,0.643836],[0.12766,0.616438],[0.12766,0.589041],[0.12766,0.561644],[0.130395,0.549902],[0.131206,0.877473],[0.134752,0.901065],[0.13617,0.52968],[0.138298,0.996005],[0.138298,0.924658],[0.138298,0.854296],[0.140426,0.972603],[0.140426,0.945205],[0.140426,0.835616],[0.140426,0.808219],[0.140426,0.780822],[0.140426,0.753425],[0.140426,0.726027],[0.140426,0.69863],[0.140426,0.671233],[0.140426,0.643836],[0.140426,0.616438],[0.140426,0.589041],[0.140426,0.561644],[0.140426,0.534247],[0.141844,0.94825],[0.144985,0.514677],[0.14539,0.971842],[0.148936,0.995434],[0.148936,0.854711],[0.153191,0.835616],[0.153191,0.808219],[0.153191,0.780822],[0.153191,0.753425],[0.153191,0.726027],[0.153191,0.69863],[0.153191,0.671233],[0.153191,0.643836],[0.153191,0.616438],[0.153191,0.589041],[0.153191,0.561644],[0.153191,0.534247],[0.153191,0.506849],[0.153799,0.499674],[0.159574,0.855127],[0.162614,0.484671],[0.165957,0.835616],[0.165957,0.808219],[0.165957,0.780822],[0.165957,0.753425],[0.165957,0.726027],[0.165957,0.69863],[0.165957,0.671233],[0.165957,0.643836],[0.165957,0.616438],[0.165957,0.589041],[0.165957,0.561644],[0.165957,0.534247],[0.165957,0.506849],[0.165957,0.479452],[0.170213,0.855542],[0.171429,0.469667],[0.178723,0.835616],[0.178723,0.808219],[0.178723,0.780822],[0.178723,0.753425],[0.178723,0.726027],[0.178723,0.69863],[0.178723,0.671233],[0.178723,0.643836],[0.178723,0.616438],[0.178723,0.589041],[0.178723,0.561644],[0.178723,0.534247],[0.178723,0.506849],[0.178723,0.479452],[0.180243,0.454664],[0.180851,0.855957],[0.189058,0.439661],[0.191489,0.856372],[0.191489,0.835616],[0.191489,0.808219],[0.191489,0.780822],[0.191489,0.753425],[0.191489,0.726027],[0.191489,0.69863],[0.191489,0.671233],[0.191489,0.643836],[0.191489,0.616438],[0.191489,0.589041],[0.191489,0.561644],[0.191489,0.534247],[0.191489,0.506849],[0.191489,0.479452],[0.191489,0.452055],[0.197872,0.424658],[0.202128,0.856787],[0.204255,0.835616],[0.204255,0.808219],[0.204255,0.780822],[0.204255,0.753425],[0.204255,0.726027],[0.204255,0.69863],[0.204255,0.671233],[0.204255,0.643836],[0.204255,0.616438],[0.204255,0.589041],[0.204255,0.561644],[0.204255,0.534247],[0.204255,0.506849],[0.204255,0.479452],[0.204255,0.452055],[0.204255,0.424658],[0.208511,0.418016],[0.212766,0.857202],[0.217021,0.835616],[0.217021,0.808219],[0.217021,0.780822],[0.217021,0.753425],[0.217021,0.726027],[0.217021,0.69863],[0.217021,0.671233],[0.217021,0.643836],[0.217021,0.616438],[0.217021,0.589041],[0.217021,0.561644],[0.217021,0.534247],[0.217021,0.506849],[0.217021,0.479452],[0.217021,0.452055],[0.217021,0.424658],[0.219149,0.411374],[0.223404,0.857617],[0.229787,0.835616],[0.229787,0.808219],[0.229787,0.780822],[0.229787,0.753425],[0.229787,0.726027],[0.229787,0.69863],[0.229787,0.671233],[0.229787,0.643836],[0.229787,0.616438],[0.229787,0.589041],[0.229787,0.561644],[0.229787,0.534247],[0.229787,0.506849],[0.229787,0.479452],[0.229787,0.452055],[0.229787,0.424658],[0.229787,0.404732],[0.234043,0.858032],[0.240426,0.39809],[0.242553,0.835616],[0.242553,0.808219],[0.242553,0.780822],[0.242553,0.753425],[0.242553,0.726027],[0.242553,0.69863],[0.242553,0.671233],[0.242553,0.643836],[0.242553,0.616438],[0.242553,0.589041],[0.242553,0.561644],[0.242553,0.534247],[0.242553,0.506849],[0.242553,0.479452],[0.242553,0.452055],[0.242553,0.424658],[0.242553,0.39726],[0.244681,0.858447],[0.24924,0.836269],[0.251064,0.391449],[0.253799,0.81409],[0.255319,0.808219],[0.255319,0.780822],[0.255319,0.753425],[0.255319,0.726027],[0.255319,0.69863],[0.255319,0.671233],[0.255319,0.643836],[0.255319,0.616438],[0.255319,0.589041],[0.255319,0.561644],[0.255319,0.534247],[0.255319,0.506849],[0.255319,0.479452],[0.255319,0.452055],[0.255319,0.424658],[0.255319,0.39726],[0.258359,0.791911],[0.261702,0.384807],[0.262918,0.769733],[0.267477,0.747554],[0.268085,0.726027],[0.268085,0.69863],[0.268085,0.671233],[0.268085,0.643836],[0.268085,0.616438],[0.268085,0.589041],[0.268085,0.561644],[0.268085,0.534247],[0.268085,0.506849],[0.268085,0.479452],[0.268085,0.452055],[0.268085,0.424658],[0.268085,0.39726],[0.272036,0.725375],[0.27234,0.378165],[0.276596,0.703196],[0.280851,0.69863],[0.280851,0.671233],[0.280851,0.643836],[0.280851,0.616438],[0.280851,0.589041],[0.280851,0.561644],[0.280851,0.534247],[0.280851,0.506849],[0.280851,0.479452],[0.280851,0.452055],[0.280851,0.424658],[0.280851,0.39726],[0.282979,0.371523],[0.28734,0.698402],[0.293617,0.671233],[0.293617,0.643836],[0.293617,0.616438],[0.293617,0.589041],[0.293617,0.561644],[0.293617,0.534247],[0.293617,0.506849],[0.293617,0.479452],[0.293617,0.452055],[0.293617,0.424658],[0.293617,0.39726],[0.293617,0.369863],[0.293617,0.364882],[0.298085,0.693607],[0.304255,0.35824],[0.306383,0.671233],[0.306383,0.643836],[0.306383,0.616438],[0.306383,0.589041],[0.306383,0.561644],[0.306383,0.534247],[0.306383,0.506849],[0.306383,0.479452],[0.306383,0.452055],[0.306383,0.424658],[0.306383,0.39726],[0.306383,0.369863],[0.30883,0.688813],[0.314894,0.351598],[0.319149,0.671233],[0.319149,0.643836],[0.319149,0.616438],[0.319149,0.589041],[0.319149,0.561644],[0.319149,0.534247],[0.319149,0.506849],[0.319149,0.479452],[0.319149,0.452055],[0.319149,0.424658],[0.319149,0.39726],[0.319149,0.369863],[0.319574,0.684018],[0.32614,0.347684],[0.330319,0.679224],[0.331915,0.671233],[0.331915,0.643836],[0.331915,0.616438],[0.331915,0.589041],[0.331915,0.561644],[0.331915,0.534247],[0.331915,0.506849],[0.331915,0.479452],[0.331915,0.452055],[0.331915,0.424658],[0.331915,0.39726],[0.331915,0.369863],[0.337386,0.34377],[0.341064,0.674429],[0.344681,0.671233],[0.344681,0.643836],[0.344681,0.616438],[0.344681,0.589041],[0.344681,0.561644],[0.344681,0.534247],[0.344681,0.506849],[0.344681,0.479452],[0.344681,0.452055],[0.344681,0.424658],[0.344681,0.39726],[0.344681,0.369863],[0.344681,0.342466],[0.348632,0.339856],[0.351809,0.669635],[0.357447,0.643836],[0.357447,0.616438],[0.357447,0.589041],[0.357447,0.561644],[0.357447,0.534247],[0.357447,0.506849],[0.357447,0.479452],[0.357447,0.452055],[0.357447,0.424658],[0.357447,0.39726],[0.357447,0.369863],[0.357447,0.342466],[0.359878,0.335943],[0.362553,0.66484],[0.370213,0.643836],[0.370213,0.616438],[0.370213,0.589041],[0.370213,0.561644],[0.370213,0.534247],[0.370213,0.506849],[0.370213,0.479452],[0.370213,0.452055],[0.370213,0.424658],[0.370213,0.39726],[0.370213,0.369863],[0.370213,0.342466],[0.371125,0.332029],[0.373298,0.660046],[0.382371,0.328115],[0.382979,0.643836],[0.382979,0.616438],[0.382979,0.589041],[0.382979,0.561644],[0.382979,0.534247],[0.382979,0.506849],[0.382979,0.479452],[0.382979,0.452055],[0.382979,0.424658],[0.382979,0.39726],[0.382979,0.369863],[0.382979,0.342466],[0.384043,0.655251],[0.393617,0.324201],[0.394787,0.650457],[0.395745,0.643836],[0.395745,0.616438],[0.395745,0.589041],[0.395745,0.561644],[0.395745,0.534247],[0.395745,0.506849],[0.395745,0.479452],[0.395745,0.452055],[0.395745,0.424658],[0.395745,0.39726],[0.395745,0.369863],[0.395745,0.342466],[0.404863,0.320287],[0.405532,0.645662],[0.408511,0.643836],[0.408511,0.616438],[0.408511,0.589041],[0.408511,0.561644],[0.408511,0.534247],[0.408511,0.506849],[0.408511,0.479452],[0.408511,0.452055],[0.408511,0.424658],[0.408511,0.39726],[0.408511,0.369863],[0.408511,0.342466],[0.416109,0.316373],[0.416277,0.640868],[0.421277,0.616438],[0.421277,0.589041],[0.421277,0.561644],[0.421277,0.534247],[0.421277,0.506849],[0.421277,0.479452],[0.421277,0.452055],[0.421277,0.424658],[0.421277,0.39726],[0.421277,0.369863],[0.421277,0.342466],[0.421277,0.315068],[0.427021,0.636073],[0.427356,0.312459],[0.434043,0.616438],[0.434043,0.589041],[0.434043,0.561644],[0.434043,0.534247],[0.434043,0.506849],[0.434043,0.479452],[0.434043,0.452055],[0.434043,0.424658],[0.434043,0.39726],[0.434043,0.369863],[0.434043,0.342466],[0.434043,0.315068],[0.437766,0.631279],[0.438602,0.308545],[0.446809,0.616438],[0.446809,0.589041],[0.446809,0.561644],[0.446809,0.534247],[0.446809,0.506849],[0.446809,0.479452],[0.446809,0.452055],[0.446809,0.424658],[0.446809,0.39726],[0.446809,0.369863],[0.446809,0.342466],[0.446809,0.315068],[0.448511,0.626484],[0.449848,0.304631],[0.459255,0.621689],[0.459574,0.616438],[0.459574,0.589041],[0.459574,0.561644],[0.459574,0.534247],[0.459574,0.506849],[0.459574,0.479452],[0.459574,0.452055],[0.459574,0.424658],[0.459574,0.39726],[0.459574,0.369863],[0.459574,0.342466],[0.459574,0.315068],[0.461094,0.300718],[0.47,0.616895],[0.47234,0.616438],[0.47234,0.589041],[0.47234,0.561644],[0.47234,0.534247],[0.47234,0.506849],[0.47234,0.479452],[0.47234,0.452055],[0.47234,0.424658],[0.47234,0.39726],[0.47234,0.369863],[0.47234,0.342466],[0.47234,0.315068],[0.47234,0.296804],[0.480745,0.6121],[0.482979,0.291476],[0.485106,0.589041],[0.485106,0.561644],[0.485106,0.534247],[0.485106,0.506849],[0.485106,0.479452],[0.485106,0.452055],[0.485106,0.424658],[0.485106,0.39726],[0.485106,0.369863],[0.485106,0.342466],[0.485106,0.315068],[0.491489,0.607306],[0.493617,0.286149],[0.497872,0.589041],[0.497872,0.561644],[0.497872,0.534247],[0.497872,0.506849],[0.497872,0.479452],[0.497872,0.452055],[0.497872,0.424658],[0.497872,0.39726],[0.497872,0.369863],[0.497872,0.342466],[0.497872,0.315068],[0.497872,0.287671],[0.501862,0.601027],[0.504255,0.280822],[0.510638,0.589041],[0.510638,0.561644],[0.510638,0.534247],[0.510638,0.506849],[0.510638,0.479452],[0.510638,0.452055],[0.510638,0.424658],[0.510638,0.39726],[0.510638,0.369863],[0.510638,0.342466],[0.510638,0.315068],[0.510638,0.287671],[0.512234,0.594749],[0.514894,0.275495],[0.522606,0.58847],[0.523404,0.589041],[0.523404,0.561644],[0.523404,0.534247],[0.523404,0.506849],[0.523404,0.479452],[0.523404,0.452055],[0.523404,0.424658],[0.523404,0.39726],[0.523404,0.369863],[0.523404,0.342466],[0.523404,0.315068],[0.523404,0.287671],[0.525532,0.270167],[0.532979,0.582192],[0.53617,0.561644],[0.53617,0.534247],[0.53617,0.506849],[0.53617,0.479452],[0.53617,0.452055],[0.53617,0.424658],[0.53617,0.39726],[0.53617,0.369863],[0.53617,0.342466],[0.53617,0.315068],[0.53617,0.287671],[0.53617,0.26484],[0.543351,0.575913],[0.546809,0.259513],[0.548936,0.561644],[0.548936,0.534247],[0.548936,0.506849],[0.548936,0.479452],[0.548936,0.452055],[0.548936,0.424658],[0.548936,0.39726],[0.548936,0.369863],[0.548936,0.342466],[0.548936,0.315068],[0.548936,0.287671],[0.548936,0.260274],[0.553723,0.569635],[0.557447,0.254186],[0.561702,0.561644],[0.561702,0.534247],[0.561702,0.506849],[0.561702,0.479452],[0.561702,0.452055],[0.561702,0.424658],[0.561702,0.39726],[0.561702,0.369863],[0.561702,0.342466],[0.561702,0.315068],[0.561702,0.287671],[0.561702,0.260274],[0.564096,0.563356],[0.568085,0.248858],[0.574468,0.557078],[0.574468,0.534247],[0.574468,0.506849],[0.574468,0.479452],[0.574468,0.452055],[0.574468,0.424658],[0.574468,0.39726],[0.574468,0.369863],[0.574468,0.342466],[0.574468,0.315068],[0.574468,0.287671],[0.574468,0.260274],[0.578723,0.243531],[0.58484,0.550799],[0.587234,0.534247],[0.587234,0.506849],[0.587234,0.479452],[0.587234,0.452055],[0.587234,0.424658],[0.587234,0.39726],[0.587234,0.369863],[0.587234,0.342466],[0.587234,0.315068],[0.587234,0.287671],[0.587234,0.260274],[0.589362,0.238204],[0.595213,0.544521],[0.6,0.534247],[0.6,0.506849],[0.6,0.479452],[0.6,0.452055],[0.6,0.424658],[0.6,0.39726],[0.6,0.369863],[0.6,0.342466],[0.6,0.315068],[0.6,0.287671],[0.6,0.260274],[0.6,0.232877],[0.605585,0.538242],[0.610638,0.227549],[0.612766,0.534247],[0.612766,0.506849],[0.612766,0.479452],[0.612766,0.452055],[0.612766,0.424658],[0.612766,0.39726],[0.612766,0.369863],[0.612766,0.342466],[0.612766,0.315068],[0.612766,0.287671],[0.612766,0.260274],[0.612766,0.232877],[0.615957,0.531963],[0.621277,0.222222],[0.625532,0.506849],[0.625532,0.479452],[0.625532,0.452055],[0.625532,0.424658],[0.625532,0.39726],[0.625532,0.369863],[0.625532,0.342466],[0.625532,0.315068],[0.625532,0.287671],[0.625532,0.260274],[0.625532,0.232877],[0.62633,0.525685],[0.631915,0.216895],[0.636702,0.519406],[0.638298,0.506849],[0.638298,0.479452],[0.638298,0.452055],[0.638298,0.424658],[0.638298,0.39726],[0.638298,0.369863],[0.638298,0.342466],[0.638298,0.315068],[0.638298,0.287671],[0.638298,0.260274],[0.638298,0.232877],[0.642553,0.211568],[0.647074,0.513128],[0.651064,0.506849],[0.651064,0.479452],[0.651064,0.452055],[0.651064,0.424658],[0.651064,0.39726],[0.651064,0.369863],[0.651064,0.342466],[0.651064,0.315068],[0.651064,0.287671],[0.651064,0.260274],[0.651064,0.232877],[0.651064,0.205479],[0.653191,0.20624],[0.657447,0.506849],[0.66383,0.506849],[0.66383,0.479452],[0.66383,0.452055],[0.66383,0.424658],[0.66383,0.39726],[0.66383,0.369863],[0.66383,0.342466],[0.66383,0.315068],[0.66383,0.287671],[0.66383,0.260274],[0.66383,0.232877],[0.66383,0.205479],[0.66383,0.200913],[0.66874,0.508606],[0.674275,0.190951],[0.676596,0.506849],[0.676596,0.479452],[0.676596,0.452055],[0.676596,0.424658],[0.676596,0.39726],[0.676596,0.369863],[0.676596,0.342466],[0.676596,0.315068],[0.676596,0.287671],[0.676596,0.260274],[0.676596,0.232877],[0.676596,0.205479],[0.680033,0.510362],[0.68472,0.180988],[0.689362,0.506849],[0.689362,0.479452],[0.689362,0.452055],[0.689362,0.424658],[0.689362,0.39726],[0.689362,0.369863],[0.689362,0.342466],[0.689362,0.315068],[0.689362,0.287671],[0.689362,0.260274],[0.689362,0.232877],[0.689362,0.205479],[0.689362,0.178082],[0.691326,0.512118],[0.695164,0.171025],[0.702128,0.506849],[0.702128,0.479452],[0.702128,0.452055],[0.702128,0.424658],[0.702128,0.39726],[0.702128,0.369863],[0.702128,0.342466],[0.702128,0.315068],[0.702128,0.287671],[0.702128,0.260274],[0.702128,0.232877],[0.702128,0.205479],[0.702128,0.178082],[0.702619,0.513874],[0.705609,0.161063],[0.713912,0.51563],[0.714894,0.506849],[0.714894,0.479452],[0.714894,0.452055],[0.714894,0.424658],[0.714894,0.39726],[0.714894,0.369863],[0.714894,0.342466],[0.714894,0.315068],[0.714894,0.287671],[0.714894,0.260274],[0.714894,0.232877],[0.714894,0.205479],[0.714894,0.178082],[0.714894,0.150685],[0.716054,0.1511],[0.725205,0.517387],[0.726499,0.141137],[0.72766,0.506849],[0.72766,0.479452],[0.72766,0.452055],[0.72766,0.424658],[0.72766,0.39726],[0.72766,0.369863],[0.72766,0.342466],[0.72766,0.315068],[0.72766,0.287671],[0.72766,0.260274],[0.72766,0.232877],[0.72766,0.205479],[0.72766,0.178082],[0.72766,0.150685],[0.736498,0.519143],[0.736944,0.131175],[0.740426,0.506849],[0.740426,0.479452],[0.740426,0.452055],[0.740426,0.424658],[0.740426,0.39726],[0.740426,0.369863],[0.740426,0.342466],[0.740426,0.315068],[0.740426,0.287671],[0.740426,0.260274],[0.740426,0.232877],[0.740426,0.205479],[0.740426,0.178082],[0.740426,0.150685],[0.747389,0.121212],[0.747791,0.520899],[0.753191,0.506849],[0.753191,0.479452],[0.753191,0.452055],[0.753191,0.424658],[0.753191,0.39726],[0.753191,0.369863],[0.753191,0.342466],[0.753191,0.315068],[0.753191,0.287671],[0.753191,0.260274],[0.753191,0.232877],[0.753191,0.205479],[0.753191,0.178082],[0.753191,0.150685],[0.753191,0.123288],[0.757834,0.111249],[0.759083,0.522655],[0.765957,0.506849],[0.765957,0.479452],[0.765957,0.452055],[0.765957,0.424658],[0.765957,0.39726],[0.765957,0.369863],[0.765957,0.342466],[0.765957,0.315068],[0.765957,0.287671],[0.765957,0.260274],[0.765957,0.232877],[0.765957,0.205479],[0.765957,0.178082],[0.765957,0.150685],[0.765957,0.123288],[0.768279,0.101287],[0.770376,0.524412],[0.778723,0.506849],[0.778723,0.479452],[0.778723,0.452055],[0.778723,0.424658],[0.778723,0.39726],[0.778723,0.369863],[0.778723,0.342466],[0.778723,0.315068],[0.778723,0.287671],[0.778723,0.260274],[0.778723,0.232877],[0.778723,0.205479],[0.778723,0.178082],[0.778723,0.150685],[0.778723,0.123288],[0.778723,0.09589],[0.778723,0.091324],[0.781669,0.526168],[0.790071,0.091832],[0.791489,0.506849],[0.791489,0.479452],[0.791489,0.452055],[0.791489,0.424658],[0.791489,0.39726],[0.791489,0.369863],[0.791489,0.342466],[0.791489,0.315068],[0.791489,0.287671],[0.791489,0.260274],[0.791489,0.232877],[0.791489,0.205479],[0.791489,0.178082],[0.791489,0.150685],[0.791489,0.123288],[0.791489,0.09589],[0.792962,0.527924],[0.801418,0.092339],[0.804255,0.52968],[0.804255,0.506849],[0.804255,0.479452],[0.804255,0.452055],[0.804255,0.424658],[0.804255,0.39726],[0.804255,0.369863],[0.804255,0.342466],[0.804255,0.315068],[0.804255,0.287671],[0.804255,0.260274],[0.804255,0.232877],[0.804255,0.205479],[0.804255,0.178082],[0.804255,0.150685],[0.804255,0.123288],[0.804255,0.09589],[0.812766,0.092846],[0.813982,0.516634],[0.817021,0.506849],[0.817021,0.479452],[0.817021,0.452055],[0.817021,0.424658],[0.817021,0.39726],[0.817021,0.369863],[0.817021,0.342466],[0.817021,0.315068],[0.817021,0.287671],[0.817021,0.260274],[0.817021,0.232877],[0.817021,0.205479],[0.817021,0.178082],[0.817021,0.150685],[0.817021,0.123288],[0.817021,0.09589],[0.823708,0.503588],[0.824113,0.093354],[0.829787,0.479452],[0.829787,0.452055],[0.829787,0.424658],[0.829787,0.39726],[0.829787,0.369863],[0.829787,0.342466],[0.829787,0.315068],[0.829787,0.287671],[0.829787,0.260274],[0.829787,0.232877],[0.829787,0.205479],[0.829787,0.178082],[0.829787,0.150685],[0.829787,0.123288],[0.829787,0.09589],[0.833435,0.490541],[0.835461,0.093861],[0.842553,0.479452],[0.842553,0.452055],[0.842553,0.424658],[0.842553,0.39726],[0.842553,0.369863],[0.842553,0.347032],[0.842553,0.342466],[0.842553,0.315068],[0.842553,0.287671],[0.842553,0.260274],[0.842553,0.232877],[0.842553,0.205479],[0.842553,0.178082],[0.842553,0.150685],[0.842553,0.123288],[0.842553,0.09589],[0.843161,0.477495],[0.846809,0.094368],[0.85,0.369863],[0.852888,0.464449],[0.852908,0.340944],[0.855319,0.452055],[0.855319,0.424658],[0.855319,0.39726],[0.855319,0.315068],[0.855319,0.287671],[0.855319,0.260274],[0.855319,0.232877],[0.855319,0.205479],[0.855319,0.178082],[0.855319,0.150685],[0.855319,0.123288],[0.855319,0.09589],[0.857447,0.392694],[0.858156,0.094876],[0.862614,0.451402],[0.863262,0.334855],[0.864894,0.415525],[0.868085,0.424658],[0.868085,0.315068],[0.868085,0.287671],[0.868085,0.260274],[0.868085,0.232877],[0.868085,0.205479],[0.868085,0.178082],[0.868085,0.150685],[0.868085,0.123288],[0.868085,0.09589],[0.869504,0.095383],[0.87234,0.438356],[0.873617,0.328767],[0.880851,0.315068],[0.880851,0.287671],[0.880851,0.260274],[0.880851,0.232877],[0.880851,0.205479],[0.880851,0.178082],[0.880851,0.150685],[0.880851,0.123288],[0.880851,0.09589],[0.883972,0.322679],[0.891489,0.087588],[0.893617,0.315068],[0.893617,0.287671],[0.893617,0.260274],[0.893617,0.232877],[0.893617,0.205479],[0.893617,0.178082],[0.893617,0.150685],[0.893617,0.123288],[0.893617,0.09589],[0.894326,0.316591],[0.902128,0.079286],[0.904681,0.310502],[0.906383,0.287671],[0.906383,0.260274],[0.906383,0.232877],[0.906383,0.205479],[0.906383,0.178082],[0.906383,0.150685],[0.906383,0.123288],[0.906383,0.09589],[0.912766,0.070984],[0.915035,0.304414],[0.919149,0.287671],[0.919149,0.260274],[0.919149,0.232877],[0.919149,0.205479],[0.919149,0.178082],[0.919149,0.150685],[0.919149,0.123288],[0.919149,0.09589],[0.919149,0.068493],[0.923404,0.062682],[0.92539,0.298326],[0.931915,0.287671],[0.931915,0.260274],[0.931915,0.232877],[0.931915,0.205479],[0.931915,0.178082],[0.931915,0.150685],[0.931915,0.123288],[0.931915,0.09589],[0.931915,0.068493],[0.934043,0.054379],[0.935745,0.292237],[0.944681,0.287671],[0.944681,0.260274],[0.944681,0.232877],[0.944681,0.205479],[0.944681,0.178082],[0.944681,0.150685],[0.944681,0.123288],[0.944681,0.09589],[0.944681,0.068493],[0.944681,0.046077],[0.946099,0.286149],[0.955319,0.037775],[0.956454,0.280061],[0.957447,0.260274],[0.957447,0.232877],[0.957447,0.205479],[0.957447,0.178082],[0.957447,0.150685],[0.957447,0.123288],[0.957447,0.09589],[0.957447,0.068493],[0.957447,0.041096],[0.965957,0.029473],[0.966809,0.273973],[0.970213,0.260274],[0.970213,0.232877],[0.970213,0.205479],[0.970213,0.178082],[0.970213,0.150685],[0.970213,0.123288],[0.970213,0.09589],[0.970213,0.068493],[0.970213,0.041096],[0.976596,0.021171],[0.977163,0.267884],[0.982979,0.260274],[0.982979,0.232877],[0.982979,0.205479],[0.982979,0.178082],[0.982979,0.150685],[0.982979,0.123288],[0.982979,0.09589],[0.982979,0.068493],[0.982979,0.041096],[0.987234,0.012868],[0.987518,0.261796],[0.995745,0.232877],[0.995745,0.205479],[0.995745,0.178082],[0.995745,0.150685],[0.995745,0.123288],[0.995745,0.09589],[0.995745,0.068493],[0.995745,0.041096],[0.995745,0.013699],[0.997872,0.255708],[0.997872,0.232877],[0.997872,0.210046],[0.997872,0.187215],[0.997872,0.164384],[0.997872,0.141553],[0.997872,0.118721],[0.997872,0.09589],[0.997872,0.073059],[0.997872,0.050228],[0.997872,0.027397],[0.997872,0.004566]],"contact_weight":[0.1,0.06667,0.10303,0.22361,0.36056,0.08698,0.1,0.1,0.06768,0.14142,0.56569,0.84853,0.42426,0.1,0.08698,0.1,0.14142,0.56569,0.98995,1.0,0.89443,0.5,0.1,0.11706,0.10303,0.1,0.1,0.1,0.56569,0.98995,1.0,1.0,1.0,0.92195,0.56569,0.14142,0.06768,0.07297,0.1,0.5,0.92195,1.0,1.0,1.0,1.0,1.0,0.9434,0.5,0.1,0.05531,0.1125,0.11188,0.1,0.7,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.86023,0.42426,0.125,0.10125,0.1,0.7,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.78102,0.36056,0.1375,0.1,0.10466,0.1,0.67082,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.70711,0.31623,0.12071,0.05356,0.07974,0.6,1.0,1.0,0.89443,0.70711,0.86023,1.0,1.0,1.0,1.0,1.0,0.84853,0.5,0.22361,0.0625,0.09994,0.11417,0.075,0.6,0.67082,0.5099,0.31623,0.14142,0.14142,0.5099,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.72111,0.41231,0.1,0.06426,0.11706,0.08698,0.1,0.0875,0.1,0.09091,0.3,0.1,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.92195,0.5831,0.28284,0.08698,0.07974,0.06667,0.1,0.08182,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.64031,0.2,0.06426,0.07273,0.1095,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.56569,0.1,0.06364,0.08571,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.92195,0.42426,0.10466,0.05455,0.09994,0.14545,0.5831,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.80623,0.36056,0.1,0.13636,0.6,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.70711,0.2,0.05455,0.12727,0.6,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.89443,0.31623,0.10909,0.11818,0.6,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5,0.06364,0.10909,0.11818,0.36056,0.60828,0.8544,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.67082,0.1,0.1,0.08571,0.07273,0.07143,0.1,0.3,0.53852,0.76158,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.82462,0.28284,0.09516,0.1113,0.07495,0.11662,0.22361,0.5,0.9434,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.98489,0.41231,0.10711,0.08182,0.14142,0.14142,0.70711,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5831,0.11506,0.105,0.6,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.76158,0.2,0.09091,0.11,0.11883,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.92195,0.36056,0.11368,0.1,0.36056,0.92195,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5,0.11766,0.11429,0.12134,0.22361,0.80623,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.6,0.1214,0.12473,0.1,0.7,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.7,0.1,0.10705,0.12782,0.5831,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8,0.2,0.06665,0.08594,0.44721,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.9,0.3,0.07143,0.0855,0.08571,0.31623,0.90554,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.4,0.08536,0.1,0.0855,0.2,0.8,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5,0.11429,0.08594,0.1,0.67082,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.6,0.1214,0.065,0.53852,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.7,0.1,0.07,0.10705,0.41231,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8,0.2,0.075,0.06665,0.3,0.9,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.89443,0.3,0.08,0.07143,0.085,0.2,0.76158,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.98489,0.4,0.08571,0.09,0.1,0.63246,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5,0.1,0.095,0.08333,0.5099,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.6,0.1,0.06667,0.4,0.94868,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.72801,0.2,0.11828,0.05,0.2,0.8,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8544,0.3,0.075,0.11381,0.10792,0.1,0.60828,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.41231,0.11667,0.08536,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5831,0.1,0.0875,0.08333,0.3,0.89443,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.7,0.1,0.12134,0.06667,0.14142,0.70711,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.82462,0.28284,0.0625,0.05,0.1,0.53852,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.94868,0.4,0.11381,0.11828,0.4,0.94868,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5099,0.11667,0.075,0.22361,0.80623,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.67082,0.1,0.10792,0.08333,0.1,0.63246,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8,0.2,0.08536,0.06667,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.92195,0.36056,0.0875,0.05,0.12134,0.31623,0.89443,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5,0.11381,0.0625,0.2,0.72801,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.60828,0.1,0.11667,0.1,0.1,0.7,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.76158,0.2,0.1,0.06154,0.08182,0.2,0.76158,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.9434,0.41231,0.12308,0.06364,0.2,0.8,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.63246,0.1,0.08462,0.09882,0.22361,0.80623,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.89443,0.36056,0.12536,0.08074,0.10769,0.3,0.9,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5831,0.1,0.10667,0.06923,0.09091,0.3,0.9,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.82462,0.28284,0.13077,0.0936,0.4,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.53852,0.0731,0.09231,0.4,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.76158,0.22361,0.11894,0.05385,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.44721,0.1085,0.11538,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.72111,0.2,0.1,0.07692,0.11111,0.5099,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8,0.2,0.13153,0.12222,0.1,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8,0.2,0.13333,0.10705,0.2,0.67082,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8,0.2,0.07143,0.14444,0.36056,0.86023,1.0,1.0,0.8544,0.80623,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.70711,0.14142,0.07615,0.05556,0.1,0.56569,1.0,0.70711,0.36056,0.2,0.22361,0.72801,1.0,1.0,1.0,1.0,1.0,1.0,0.7,0.1,0.09635,0.06667,0.05,0.11423,0.11641,0.22361,0.53852,0.22361,0.6,1.0,1.0,1.0,1.0,1.0,1.0,0.7,0.1,0.1,0.07778,0.1107,0.08292,0.05,0.1,0.41231,1.0,1.0,1.0,1.0,1.0,1.0,0.7,0.1,0.08889,0.1,0.1,0.3,0.8544,1.0,1.0,1.0,1.0,1.0,0.7,0.1,0.12422,0.10753,0.1,0.7,1.0,1.0,1.0,1.0,1.0,0.82462,0.28284,0.06667,0.11506,0.1,0.53852,1.0,1.0,1.0,1.0,1.0,1.0,0.5,0.05455,0.10844,0.4,0.94868,1.0,1.0,1.0,1.0,1.0,0.67082,0.14142,0.07273,0.07073,0.2,0.8,1.0,1.0,1.0,1.0,1.0,0.89443,0.31623,0.09091,0.1,0.1,0.63246,1.0,1.0,1.0,1.0,1.0,1.0,0.53852,0.10909,0.12032,0.1113,0.06667,0.5,1.0,1.0,1.0,1.0,1.0,1.0,0.72801,0.2,0.11883,0.1,0.31623,0.89443,1.0,1.0,1.0,1.0,1.0,0.9434,0.4,0.06364,0.09235,0.2,0.72801,1.0,1.0,1.0,1.0,1.0,1.0,0.5831,0.08182,0.06667,0.6,1.0,1.0,1.0,1.0,1.0,1.0,0.80623,0.22361,0.1,0.6,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.53852,0.1]},"CreamDuvet":{"vertices":[[2.10028,3.96417,0.77111],[2.1005,3.96134,0.75383],[2.10762,3.962,0.78926],[2.10035,3.95764,0.73686],[2.11487,3.95965,0.80747],[2.10008,3.95368,0.71996],[2.12307,3.95944,0.82487],[2.09937,3.94869,0.70347],[2.13307,3.96323,0.84084],[2.12588,3.95524,0.823],[2.11941,3.94882,0.80466],[2.1139,3.94455,0.78555],[2.10852,3.94056,0.76636],[2.10393,3.93836,0.74645],[2.10184,3.94187,0.72413],[2.09999,3.94598,0.70144],[2.09922,3.94498,0.68641],[2.14371,3.96836,0.85642],[2.09746,3.93757,0.67095],[2.15341,3.97131,0.87285],[2.16362,3.97013,0.87352],[2.15802,3.9658,0.85413],[2.1491,3.9542,0.83743],[2.13179,3.9241,0.82783],[2.11702,3.89941,0.81666],[2.1071,3.88527,0.80176],[2.09785,3.87255,0.78651],[2.09011,3.86313,0.77008],[2.09086,3.87264,0.74578],[2.09456,3.88887,0.71834],[2.09303,3.89345,0.69568],[2.08864,3.89162,0.67577],[2.17057,3.97034,0.87618],[2.06954,3.82351,0.69127],[2.18306,3.96781,0.86783],[2.17601,3.96045,0.8494],[2.15768,3.92889,0.83997],[2.13299,3.88341,0.83635],[2.11843,3.85953,0.82519],[2.10926,3.84719,0.80985],[2.10092,3.83655,0.79399],[2.09277,3.8263,0.77814],[2.08737,3.82201,0.75989],[2.08258,3.81904,0.74108],[2.07706,3.81446,0.72301],[2.07108,3.80887,0.70543],[2.18801,3.96958,0.87947],[2.0679,3.79531,0.68898],[2.20498,3.96748,0.88324],[2.20708,3.967,0.88269],[2.2016,3.96307,0.8629],[2.18917,3.94458,0.84841],[2.15845,3.88755,0.84853],[2.14024,3.85651,0.83979],[2.12888,3.83976,0.82601],[2.12004,3.82829,0.8104],[2.11149,3.81737,0.79472],[2.10291,3.80632,0.77927],[2.09592,3.79863,0.76252],[2.09001,3.79324,0.74487],[2.0844,3.78846,0.72699],[2.07847,3.78298,0.70948],[2.07317,3.77886,0.69138],[2.07184,3.77967,0.68098],[2.07728,3.76737,0.67144],[2.22195,3.96505,0.88715],[2.22598,3.9625,0.87772],[2.22042,3.95847,0.85786],[2.19861,3.92106,0.8502],[2.16608,3.86132,0.85158],[2.15159,3.83849,0.83988],[2.14233,3.82639,0.82435],[2.1333,3.8147,0.80883],[2.12438,3.80317,0.79341],[2.11598,3.79268,0.77774],[2.1088,3.78469,0.76113],[2.10236,3.77824,0.74394],[2.09654,3.77306,0.72625],[2.08998,3.76629,0.70935],[2.08358,3.75985,0.69237],[2.08082,3.76115,0.67172],[2.08356,3.75687,0.66103],[2.23889,3.96223,0.89123],[2.2501,3.96082,0.89309],[2.24477,3.9573,0.87293],[2.23767,3.95024,0.85405],[2.21038,3.9025,0.85022],[2.18004,3.84831,0.84971],[2.16635,3.8275,0.83726],[2.15686,3.81512,0.82182],[2.14726,3.80248,0.80668],[2.13843,3.79132,0.79112],[2.13016,3.78126,0.77525],[2.12293,3.77328,0.75862],[2.11544,3.76474,0.74234],[2.10907,3.75849,0.72513],[2.10209,3.75093,0.70861],[2.09483,3.74276,0.69247],[2.0919,3.74361,0.67205],[2.0888,3.74415,0.65171],[2.25643,3.9603,0.89502],[2.09829,3.72882,0.65075],[2.26983,3.95698,0.88787],[2.26402,3.95258,0.8679],[2.24999,3.932,0.8538],[2.21998,3.87976,0.8518],[2.19655,3.84024,0.84593],[2.18223,3.81857,0.83381],[2.17202,3.80496,0.81883],[2.16238,3.79243,0.80363],[2.15378,3.78191,0.78777],[2.14574,3.77249,0.77159],[2.13749,3.76259,0.75576],[2.12917,3.7525,0.74016],[2.12142,3.74351,0.7242],[2.11348,3.73408,0.70857],[2.10677,3.72714,0.69188],[2.10367,3.72758,0.67165],[2.1001,3.7271,0.65181],[2.27403,3.95816,0.89892],[2.10935,3.71675,0.64816],[2.29185,3.95612,0.90281],[2.29473,3.95577,0.90329],[2.28969,3.95291,0.88265],[2.2808,3.94265,0.86464],[2.25401,3.89782,0.85937],[2.23014,3.85836,0.85293],[2.2114,3.82863,0.84347],[2.19826,3.80965,0.83031],[2.1887,3.79755,0.81469],[2.17992,3.78692,0.79865],[2.17101,3.77599,0.78289],[2.16207,3.76494,0.76735],[2.1528,3.75319,0.75229],[2.14291,3.74013,0.73801],[2.13284,3.72666,0.72413],[2.12422,3.71601,0.70912],[2.11943,3.71299,0.69058],[2.11654,3.71381,0.67013],[2.11358,3.7145,0.64968],[2.12244,3.70868,0.64351],[2.31042,3.95516,0.90637],[2.31597,3.95377,0.89742],[2.31008,3.94937,0.87718],[2.29348,3.92474,0.8642],[2.26205,3.87192,0.86202],[2.2425,3.84135,0.85243],[2.22706,3.81841,0.84044],[2.21582,3.8034,0.8257],[2.20749,3.79387,0.80897],[2.19877,3.78354,0.79271],[2.18901,3.77117,0.77748],[2.17926,3.75874,0.76246],[2.16701,3.74141,0.74985],[2.15188,3.71838,0.74014],[2.14166,3.7048,0.72643],[2.13595,3.7,0.70872],[2.13259,3.69979,0.6888],[2.13082,3.70276,0.66725],[2.12911,3.70586,0.64551],[2.13603,3.70141,0.6384],[2.32828,3.95282,0.89589],[2.33722,3.95123,0.89165],[2.328,3.94075,0.87341],[2.30352,3.90199,0.86551],[2.27443,3.85444,0.8616],[2.25687,3.82811,0.8505],[2.24343,3.8093,0.83694],[2.23406,3.79804,0.82067],[2.22641,3.78994,0.80326],[2.21705,3.77861,0.78733],[2.20672,3.76537,0.7724],[2.19649,3.75227,0.75762],[2.17808,3.72353,0.75014],[2.1605,3.69622,0.74252],[2.15304,3.68812,0.72632],[2.14866,3.68591,0.70739],[2.14596,3.68693,0.68685],[2.14539,3.69209,0.66414],[2.14442,3.69654,0.64163],[2.1484,3.69156,0.63458],[2.34475,3.94752,0.88629],[2.33601,3.9139,0.87603],[2.31007,3.87314,0.86909],[2.28802,3.83923,0.86034],[2.27206,3.81625,0.84802],[2.25962,3.79962,0.83361],[2.25119,3.79025,0.81654],[2.24359,3.78237,0.79898],[2.23467,3.77204,0.78258],[2.22411,3.75861,0.76767],[2.21302,3.74414,0.75345],[2.18952,3.70644,0.75008],[2.17206,3.67983,0.74226],[2.16623,3.67488,0.72459],[2.16263,3.67413,0.70492],[2.15988,3.675,0.68441],[2.15864,3.67875,0.66234],[2.15776,3.68323,0.63977],[2.1595,3.67918,0.63205],[2.35369,3.92836,0.88141],[2.34419,3.88772,0.87849],[2.32281,3.85577,0.86852],[2.30293,3.82632,0.85821],[2.28721,3.80417,0.8456],[2.27482,3.7879,0.83106],[2.26605,3.77807,0.81415],[2.25892,3.77114,0.79617],[2.25108,3.76293,0.77884],[2.24187,3.75217,0.76273],[2.22933,3.7353,0.74954],[2.20181,3.69096,0.7493],[2.18544,3.66681,0.74042],[2.18027,3.66315,0.72212],[2.17667,3.66236,0.70242],[2.17342,3.66224,0.68235],[2.17203,3.66559,0.66044],[2.17155,3.67069,0.63748],[2.17173,3.66885,0.62842],[2.35441,3.89478,0.88169],[2.35691,3.86982,0.87802],[2.3377,3.8422,0.86656],[2.31801,3.81359,0.85598],[2.30185,3.79105,0.84356],[2.28869,3.77368,0.82947],[2.27868,3.76178,0.81343],[2.27166,3.75515,0.79534],[2.26492,3.74897,0.77715],[2.25718,3.74101,0.75981],[2.24372,3.72279,0.74724],[2.21498,3.67707,0.74778],[2.20039,3.65654,0.73726],[2.19481,3.65217,0.71925],[2.1908,3.65061,0.69988],[2.18698,3.64942,0.6803],[2.18612,3.65363,0.65789],[2.18653,3.6602,0.63409],[2.18417,3.65882,0.6246],[2.35988,3.87004,0.87897],[2.199,3.65068,0.62582],[2.36799,3.8501,0.8746],[2.35221,3.82786,0.86485],[2.33423,3.80269,0.85302],[2.31789,3.78023,0.84059],[2.30294,3.76004,0.82764],[2.29062,3.74433,0.81319],[2.28213,3.7352,0.7962],[2.27485,3.72814,0.77845],[2.2682,3.72217,0.76029],[2.25694,3.7081,0.74592],[2.23145,3.66893,0.74355],[2.21676,3.64858,0.73294],[2.21025,3.64262,0.71564],[2.20584,3.64036,0.69656],[2.20202,3.63915,0.67693],[2.20124,3.64337,0.65443],[2.20163,3.64973,0.63063],[2.38705,3.852,0.88052],[2.21297,3.64085,0.62793],[2.38598,3.84021,0.87482],[2.36841,3.81627,0.8621],[2.35181,3.79386,0.84923],[2.33526,3.77144,0.83677],[2.31828,3.74818,0.82506],[2.30378,3.72901,0.81207],[2.29271,3.7156,0.79695],[2.28424,3.70658,0.78011],[2.27791,3.7012,0.76176],[2.27011,3.69327,0.74465],[2.25112,3.66602,0.73679],[2.23445,3.64264,0.72758],[2.22701,3.63513,0.71096],[2.22154,3.63105,0.69272],[2.21795,3.63023,0.67281],[2.21702,3.63406,0.65041],[2.21636,3.63842,0.62759],[2.4069,3.85489,0.88616],[2.22741,3.63172,0.62965],[2.41902,3.85026,0.88599],[2.40339,3.83003,0.8715],[2.38574,3.80638,0.85867],[2.36926,3.78456,0.84556],[2.35241,3.76201,0.83315],[2.3347,3.73793,0.82178],[2.31948,3.71789,0.80916],[2.30555,3.69993,0.79601],[2.2938,3.68551,0.7816],[2.28651,3.67855,0.76404],[2.27988,3.67267,0.74606],[2.26956,3.66054,0.73114],[2.25502,3.6412,0.71994],[2.24551,3.63032,0.70488],[2.23937,3.62513,0.68709],[2.23491,3.62282,0.66785],[2.23353,3.62577,0.6458],[2.42825,3.85985,0.89114],[2.24225,3.62312,0.63108],[2.45635,3.87527,0.89254],[2.46371,3.87804,0.89125],[2.43667,3.8399,0.88269],[2.42174,3.82113,0.86764],[2.40352,3.79696,0.855],[2.38598,3.7738,0.84242],[2.36894,3.75133,0.82999],[2.35118,3.72757,0.81851],[2.33557,3.70725,0.80602],[2.31991,3.68673,0.79399],[2.3042,3.66604,0.78241],[2.29393,3.65423,0.76714],[2.28716,3.64815,0.74937],[2.28038,3.64204,0.73168],[2.27277,3.63454,0.71475],[2.26502,3.62677,0.69806],[2.25814,3.62043,0.68074],[2.25337,3.61761,0.66165],[2.25143,3.61955,0.64],[2.25844,3.61662,0.63135],[2.49483,3.90648,0.88858],[2.48542,3.87317,0.8859],[2.45558,3.83128,0.87871],[2.43952,3.81104,0.86418],[2.42132,3.78731,0.85138],[2.40309,3.76344,0.83908],[2.38524,3.74006,0.82703],[2.36746,3.71668,0.81542],[2.35154,3.69618,0.80301],[2.33564,3.67564,0.79101],[2.31848,3.65296,0.78036],[2.3054,3.63678,0.76713],[2.29732,3.62863,0.75037],[2.29113,3.62354,0.73224],[2.28462,3.61792,0.71443],[2.27923,3.6141,0.69577],[2.27479,3.61184,0.67631],[2.27151,3.6115,0.65583],[2.26935,3.61301,0.63429],[2.52599,3.92503,0.88929],[2.27477,3.61012,0.63159],[2.49968,3.85631,0.88478],[2.47428,3.82202,0.87491],[2.45773,3.80136,0.86052],[2.43999,3.77875,0.84727],[2.42065,3.75356,0.83549],[2.40152,3.72856,0.82412],[2.38349,3.7052,0.81253],[2.36735,3.68469,0.80015],[2.35172,3.66492,0.78782],[2.33509,3.64346,0.77664],[2.32066,3.62542,0.76428],[2.31048,3.61404,0.74905],[2.30299,3.60691,0.73192],[2.29661,3.60155,0.714],[2.292,3.59901,0.69469],[2.28884,3.5988,0.67416],[2.28694,3.60063,0.65247],[2.53295,3.90236,0.88543],[2.28892,3.59985,0.63387],[2.53179,3.86755,0.88598],[2.51291,3.83786,0.88424],[2.49213,3.81119,0.87164],[2.47616,3.79175,0.85678],[2.45842,3.76953,0.84337],[2.43913,3.74483,0.83139],[2.4189,3.71859,0.82054],[2.40057,3.69515,0.80899],[2.38371,3.67389,0.79694],[2.36763,3.65374,0.7848],[2.35142,3.63331,0.77318],[2.33728,3.616,0.76046],[2.3256,3.60247,0.74625],[2.31659,3.59307,0.73022],[2.30985,3.58719,0.71256],[2.3049,3.58411,0.69352],[2.30168,3.58376,0.67303],[2.29988,3.58565,0.65128],[2.30121,3.58655,0.6378],[2.53324,3.83722,0.88511],[2.5296,3.82461,0.8818],[2.50968,3.79967,0.86859],[2.49427,3.78139,0.85327],[2.47672,3.75982,0.8396],[2.45693,3.7348,0.82776],[2.43691,3.70931,0.81661],[2.41764,3.68487,0.8055],[2.40045,3.66345,0.79354],[2.38322,3.6419,0.78204],[2.36716,3.62203,0.77018],[2.35324,3.60535,0.75719],[2.34162,3.59212,0.74281],[2.33161,3.58132,0.72746],[2.32422,3.57451,0.71024],[2.31907,3.57113,0.69133],[2.31583,3.57071,0.67084],[2.3142,3.5728,0.64892],[2.31431,3.57452,0.64103],[2.54173,3.81771,0.88042],[2.32717,3.5666,0.6305],[2.54732,3.81273,0.87882],[2.52823,3.78942,0.86501],[2.51262,3.77114,0.84967],[2.49532,3.7503,0.8357],[2.47557,3.72576,0.82367],[2.45511,3.70006,0.81261],[2.43476,3.67441,0.80204],[2.41677,3.65218,0.79045],[2.39933,3.63067,0.77896],[2.38338,3.6113,0.76689],[2.36977,3.59536,0.75354],[2.35803,3.58217,0.73913],[2.34837,3.57205,0.72341],[2.34028,3.56429,0.70663],[2.33502,3.56076,0.68773],[2.33162,3.56007,0.66732],[2.33062,3.56303,0.64484],[2.55282,3.80207,0.87432],[2.33974,3.55807,0.6202],[2.54797,3.78062,0.86082],[2.53184,3.76189,0.84562],[2.51443,3.74124,0.83156],[2.49483,3.71733,0.81926],[2.47366,3.69104,0.80845],[2.45225,3.66429,0.79838],[2.4333,3.64103,0.78726],[2.41617,3.62033,0.77543],[2.4007,3.60199,0.76288],[2.38648,3.58542,0.74982],[2.37478,3.57249,0.73526],[2.36565,3.56332,0.71902],[2.35777,3.55597,0.70197],[2.35246,3.5524,0.68301],[2.3499,3.55293,0.66184],[2.34947,3.55665,0.63881],[2.34544,3.55503,0.61866],[2.56683,3.7906,0.86663],[2.34794,3.54289,0.61364],[2.56859,3.77275,0.8562],[2.55091,3.75215,0.84171],[2.53298,3.73109,0.8278],[2.51345,3.70768,0.81529],[2.49212,3.68162,0.80441],[2.46966,3.65382,0.79482],[2.45073,3.63098,0.78353],[2.43362,3.61066,0.77153],[2.41805,3.59249,0.75889],[2.40369,3.57598,0.74579],[2.39209,3.5634,0.73104],[2.38297,3.55437,0.71467],[2.37577,3.54811,0.697],[2.37104,3.54543,0.67747],[2.3692,3.54696,0.65562],[2.36835,3.54997,0.63284],[2.36073,3.54307,0.61561],[2.58374,3.78307,0.85737],[2.35103,3.52029,0.61144],[2.59283,3.76957,0.84967],[2.56981,3.74188,0.83795],[2.55077,3.71962,0.82453],[2.53169,3.69723,0.81161],[2.51022,3.67141,0.80064],[2.48688,3.64286,0.79143],[2.46794,3.62039,0.78],[2.45072,3.60026,0.76792],[2.43517,3.5824,0.75513],[2.42166,3.56736,0.7413],[2.41015,3.55513,0.72633],[2.40163,3.54708,0.70939],[2.39486,3.54152,0.69126],[2.38988,3.5385,0.67181],[2.38864,3.54083,0.64939],[2.38694,3.54252,0.62721],[2.37491,3.52938,0.61352],[2.59863,3.77248,0.84921],[2.37009,3.51806,0.61189],[2.61636,3.7656,0.8395],[2.58946,3.73238,0.83382],[2.56866,3.70809,0.82124],[2.54878,3.68497,0.80865],[2.52712,3.65932,0.79765],[2.50491,3.6328,0.78757],[2.48495,3.60931,0.77663],[2.46801,3.58989,0.76423],[2.45331,3.57351,0.75073],[2.44067,3.55993,0.73614],[2.42985,3.54883,0.72052],[2.42159,3.54129,0.70324],[2.41491,3.53591,0.6849],[2.41017,3.53325,0.66514],[2.40892,3.53551,0.6426],[2.40524,3.53435,0.6219],[2.38887,3.5151,0.61277],[2.38777,3.51374,0.61255],[2.59594,3.73116,0.83308],[2.58405,3.70845,0.82231],[2.57202,3.68567,0.8119],[2.40955,3.51448,0.61226],[2.56533,3.6718,0.80605],[2.54322,3.64597,0.79519],[2.52299,3.62259,0.78373],[2.50345,3.60005,0.77237],[2.48576,3.57994,0.76029],[2.47167,3.56465,0.74624],[2.4596,3.55207,0.73111],[2.44972,3.54243,0.71468],[2.44273,3.53671,0.69634],[2.43559,3.53079,0.67817],[2.43132,3.5288,0.6579],[2.43095,3.53218,0.63456],[2.42421,3.5268,0.61614],[2.55925,3.66199,0.8022],[2.54653,3.6385,0.79277],[2.53591,3.61792,0.78235],[2.43053,3.51387,0.61174],[2.52599,3.59836,0.77174],[2.52238,3.59109,0.76789],[2.50454,3.57111,0.75574],[2.49055,3.55621,0.74146],[2.47961,3.54533,0.72542],[2.47064,3.53706,0.70819],[2.46341,3.5311,0.68986],[2.45641,3.52545,0.67144],[2.45568,3.52823,0.64836],[2.45639,3.533,0.624],[2.44206,3.51741,0.61136],[2.51636,3.57928,0.76117],[2.4528,3.51462,0.6104],[2.52301,3.56313,0.75164],[2.52353,3.56229,0.75111],[2.50952,3.54759,0.7367],[2.49959,3.5382,0.71983],[2.49124,3.53087,0.70203],[2.48392,3.52488,0.68361],[2.47894,3.52196,0.66357],[2.48129,3.52875,0.63803],[2.47965,3.53028,0.61529],[2.47475,3.5145,0.60957],[2.53085,3.54857,0.74136],[2.5289,3.53922,0.73173],[2.51906,3.53009,0.71466],[2.51119,3.52347,0.6964],[2.50484,3.51881,0.67717],[2.5039,3.52115,0.65405],[2.51004,3.5327,0.62554],[2.49851,3.52131,0.61007],[2.49647,3.51367,0.60913],[2.5401,3.5358,0.73016],[2.52254,3.52513,0.61223],[2.53926,3.52258,0.70908],[2.53181,3.51658,0.69041],[2.5271,3.51405,0.66988],[2.53071,3.5221,0.64336],[2.53443,3.53034,0.61646],[2.55095,3.525,0.7179],[2.54206,3.52782,0.6206],[2.55388,3.52065,0.63471],[2.5594,3.51467,0.70363],[2.55253,3.50946,0.68442],[2.55017,3.50987,0.66213],[2.56218,3.51457,0.70537],[2.56266,3.5097,0.65096],[2.57388,3.50461,0.69251],[2.57313,3.50185,0.6786],[2.57388,3.50181,0.66546],[2.59029,3.50018,0.67649]],"faces":[[316,335,337],[554,559,555],[559,554,557],[554,551,557],[551,554,546],[542,545,543],[545,542,541],[318,317,336],[295,297,299],[561,560,562],[560,559,562],[559,560,555],[560,561,555],[536,527,535],[544,551,546],[544,536,535],[553,556,558],[556,553,548],[556,561,558],[561,556,555],[545,550,552],[550,545,541],[297,298,299],[317,298,297],[298,317,318],[355,318,336],[397,355,336],[357,355,397],[471,397,473],[471,452,397],[278,295,299],[490,534,543],[524,534,490],[533,534,524],[534,542,543],[534,533,542],[511,524,490],[524,511,522],[511,507,522],[550,549,552],[549,553,552],[553,549,548],[521,524,522],[521,533,524],[377,395,378],[357,376,358],[376,377,358],[376,395,377],[376,357,397],[395,376,397],[471,454,452],[454,435,452],[474,455,473],[455,471,473],[455,454,471],[240,257,259],[240,220,238],[257,276,259],[276,279,259],[279,276,278],[278,276,295],[260,240,259],[240,260,241],[316,315,335],[315,316,296],[294,315,296],[121,123,119],[8,17,22],[100,83,82],[83,84,82],[83,100,84],[102,100,119],[123,102,119],[491,474,473],[474,491,492],[472,490,453],[470,472,453],[472,489,490],[489,472,470],[494,511,490],[511,494,507],[489,494,490],[507,494,488],[494,489,488],[537,544,546],[544,537,536],[355,338,318],[338,357,358],[338,355,357],[221,240,241],[240,221,220],[181,182,163],[354,356,337],[508,509,496],[509,508,495],[508,476,495],[495,493,492],[476,493,495],[451,470,453],[413,394,393],[489,469,488],[469,489,470],[398,395,397],[395,398,378],[236,239,237],[122,121,141],[142,122,141],[121,122,123],[143,161,163],[142,161,143],[161,142,141],[200,182,181],[48,65,49],[46,48,50],[48,49,50],[65,66,49],[66,65,82],[84,66,82],[12,1,0],[2,11,0],[11,12,0],[10,11,2],[79,64,63],[30,31,18],[356,374,375],[354,374,356],[394,374,393],[374,394,375],[493,475,492],[475,493,476],[475,474,492],[434,451,453],[432,434,415],[434,432,451],[413,396,394],[396,413,415],[452,433,397],[435,433,452],[277,294,296],[275,277,296],[160,140,159],[179,160,159],[162,181,163],[161,162,163],[162,161,141],[220,219,238],[21,17,19],[17,21,22],[34,32,46],[34,46,50],[4,10,2],[8,9,6],[9,4,6],[4,9,10],[13,1,12],[81,98,99],[79,80,64],[80,81,64],[81,80,98],[31,45,33],[47,45,61],[45,47,33],[100,103,84],[102,103,100],[477,508,496],[508,477,476],[527,526,535],[413,431,415],[431,432,415],[414,398,397],[433,414,397],[414,416,398],[416,414,433],[416,433,435],[139,140,120],[139,120,138],[140,139,138],[217,218,199],[218,236,237],[218,217,236],[179,180,160],[140,158,159],[158,140,138],[201,219,220],[200,201,182],[219,201,200],[20,21,19],[34,20,32],[13,3,1],[14,7,5],[3,14,5],[14,3,13],[7,14,15],[101,118,120],[98,101,99],[117,101,98],[101,117,118],[120,117,138],[118,117,120],[16,7,15],[16,30,18],[16,29,30],[29,16,15],[47,62,63],[62,47,61],[62,79,63],[236,256,239],[523,498,513],[335,334,337],[334,354,337],[523,525,514],[515,525,527],[514,525,515],[525,526,527],[198,180,179],[180,198,199],[198,217,199],[258,275,239],[256,258,239],[258,256,255],[498,512,513],[499,523,514],[499,498,523],[512,497,510],[497,512,498],[509,497,496],[510,497,509],[277,274,294],[274,277,275],[258,274,275],[274,258,255],[554,547,546],[547,554,555],[556,547,555],[547,556,548],[539,531,530],[531,539,540],[507,487,506],[487,507,488],[447,466,467],[448,447,467],[447,465,466],[465,447,446],[515,501,500],[501,515,516],[549,550,541],[540,549,541],[539,549,540],[549,539,548],[521,507,506],[507,521,522],[532,531,540],[532,540,541],[519,532,520],[531,532,519],[542,532,541],[533,532,542],[532,521,520],[521,532,533],[359,377,378],[377,359,358],[298,319,299],[319,298,318],[418,401,400],[401,418,419],[435,455,436],[454,455,435],[420,401,419],[401,420,402],[260,279,280],[279,260,259],[241,260,242],[260,261,242],[322,343,323],[343,322,342],[343,324,323],[324,343,344],[345,324,344],[324,345,325],[304,324,325],[305,304,325],[406,387,386],[387,406,407],[420,403,402],[403,420,421],[385,406,386],[406,385,405],[468,486,487],[486,468,467],[444,464,445],[464,444,463],[482,464,463],[464,482,483],[464,465,446],[445,464,446],[464,483,484],[465,464,484],[408,387,407],[387,408,388],[531,518,530],[518,531,519],[518,502,517],[502,518,503],[483,502,484],[502,503,484],[482,502,483],[502,482,501],[502,501,516],[517,502,516],[379,399,380],[399,400,380],[361,360,380],[360,379,380],[340,360,341],[360,361,341],[360,359,378],[379,360,378],[300,279,278],[300,278,299],[361,362,341],[362,342,341],[385,365,384],[365,385,366],[365,345,344],[365,344,364],[346,365,366],[345,365,346],[304,284,283],[284,304,305],[284,265,264],[265,284,285],[345,326,325],[326,345,346],[143,144,123],[123,144,124],[164,144,163],[144,143,163],[52,35,51],[35,52,36],[59,60,43],[60,44,43],[68,52,51],[52,68,69],[67,68,50],[68,51,50],[365,383,384],[383,365,364],[404,385,384],[385,404,405],[404,403,421],[422,404,421],[383,404,384],[404,383,403],[465,485,466],[485,465,484],[466,485,467],[485,486,467],[521,505,520],[505,521,506],[487,505,506],[486,505,487],[449,448,467],[468,449,467],[469,449,468],[449,469,450],[451,469,470],[469,451,450],[469,487,488],[469,468,487],[447,427,446],[427,447,428],[427,410,409],[410,427,428],[518,529,530],[529,518,517],[398,399,379],[398,379,378],[417,418,400],[399,417,400],[339,360,340],[360,339,359],[339,319,318],[338,339,318],[359,339,358],[339,338,358],[322,321,342],[342,321,341],[400,381,380],[401,381,400],[381,361,380],[381,362,361],[343,363,344],[344,363,364],[363,343,342],[362,363,342],[261,243,242],[262,243,261],[284,263,283],[263,284,264],[263,282,283],[282,263,262],[265,246,264],[264,246,245],[306,284,305],[284,306,285],[306,326,327],[307,306,327],[306,305,325],[326,306,325],[347,326,346],[326,347,327],[328,347,348],[347,328,327],[328,308,307],[328,307,327],[122,143,123],[122,142,143],[165,144,164],[144,165,145],[66,67,50],[49,66,50],[121,32,141],[100,32,119],[37,23,22],[36,37,22],[96,79,78],[79,96,97],[444,443,463],[443,462,463],[501,481,500],[482,481,501],[481,482,463],[462,481,463],[440,422,421],[440,441,422],[420,440,421],[440,420,439],[458,440,439],[440,458,459],[392,374,373],[374,392,393],[293,314,294],[294,314,315],[372,392,373],[392,372,391],[475,456,455],[475,455,474],[475,457,456],[457,475,476],[503,504,484],[504,485,484],[504,518,519],[518,504,503],[485,504,486],[504,505,486],[504,519,520],[505,504,520],[426,408,407],[425,426,407],[426,444,445],[444,426,425],[426,427,409],[408,426,409],[426,445,446],[427,426,446],[392,412,393],[412,413,393],[412,392,391],[411,412,391],[429,412,411],[412,429,430],[429,447,448],[447,429,428],[449,429,448],[429,449,430],[429,410,428],[429,411,410],[547,538,546],[538,537,546],[538,547,548],[539,538,548],[538,539,530],[529,538,530],[528,538,529],[538,528,537],[515,528,516],[528,515,527],[528,527,536],[537,528,536],[528,517,516],[528,529,517],[279,301,280],[300,301,279],[319,320,299],[320,300,299],[320,339,340],[339,320,319],[320,301,300],[301,320,321],[320,340,341],[321,320,341],[382,401,402],[382,381,401],[403,382,402],[383,382,403],[381,382,362],[382,363,362],[382,383,364],[363,382,364],[244,263,264],[244,264,245],[244,243,262],[263,244,262],[260,281,261],[281,260,280],[281,262,261],[281,282,262],[324,303,323],[304,303,324],[303,304,283],[282,303,283],[246,226,245],[226,225,245],[385,367,366],[367,385,386],[367,346,366],[367,347,346],[290,271,270],[271,290,291],[269,290,270],[290,269,289],[288,269,268],[269,288,289],[222,241,242],[222,221,241],[243,222,242],[222,243,223],[222,203,202],[221,222,202],[183,164,163],[182,183,163],[186,168,167],[168,186,187],[186,166,185],[166,186,167],[186,206,187],[206,186,205],[222,204,203],[204,222,223],[186,204,205],[204,186,185],[21,35,36],[21,36,22],[35,34,51],[51,34,50],[10,9,24],[9,23,24],[23,9,22],[9,8,22],[52,53,36],[53,37,36],[92,112,93],[92,111,112],[92,91,110],[111,92,110],[116,96,115],[96,116,97],[116,115,136],[137,116,136],[80,79,97],[98,80,97],[29,28,42],[29,42,43],[44,29,43],[30,29,44],[45,30,44],[45,31,30],[60,45,44],[61,45,60],[106,126,127],[126,106,105],[103,123,124],[103,102,123],[85,103,104],[103,85,84],[66,85,67],[85,66,84],[85,104,105],[86,85,105],[85,68,67],[85,86,68],[173,174,153],[153,174,154],[271,252,270],[252,251,270],[252,272,253],[272,252,271],[251,252,231],[252,232,231],[252,233,232],[233,252,253],[156,135,155],[135,156,136],[155,135,154],[135,134,154],[406,424,407],[424,425,407],[424,443,444],[424,444,425],[477,457,476],[457,477,458],[292,271,291],[292,272,271],[417,437,418],[437,417,436],[455,437,436],[456,437,455],[431,412,430],[412,431,413],[431,449,450],[449,431,430],[451,431,450],[432,431,451],[398,416,399],[416,417,399],[416,435,436],[417,416,436],[204,224,205],[224,204,223],[224,206,205],[206,224,225],[224,244,245],[225,224,245],[244,224,243],[243,224,223],[301,302,280],[302,281,280],[302,321,322],[302,301,321],[281,302,282],[302,303,282],[302,322,323],[303,302,323],[191,173,172],[173,191,192],[211,191,210],[191,211,192],[171,191,172],[191,171,190],[191,209,210],[209,191,190],[131,109,130],[109,131,110],[131,111,110],[111,131,132],[152,173,153],[173,152,172],[134,133,154],[133,153,154],[133,152,153],[152,133,132],[133,111,132],[111,133,112],[349,368,369],[368,349,348],[368,387,388],[369,368,388],[347,368,348],[367,368,347],[387,368,386],[368,367,386],[215,233,234],[233,215,214],[196,215,197],[197,215,216],[195,215,196],[215,195,214],[184,204,185],[204,184,203],[166,184,185],[184,166,165],[203,184,202],[184,183,202],[184,165,164],[183,184,164],[169,168,187],[188,169,187],[206,207,187],[207,188,187],[226,207,225],[207,206,225],[201,221,202],[221,201,220],[201,183,182],[183,201,202],[21,20,35],[20,34,35],[29,14,28],[14,29,15],[25,10,24],[25,11,10],[53,38,37],[38,53,54],[37,38,23],[23,38,24],[25,38,39],[38,25,24],[91,90,110],[90,109,110],[38,55,39],[55,38,54],[76,58,75],[58,76,59],[58,59,43],[42,58,43],[58,74,75],[74,58,57],[74,92,93],[75,74,93],[77,61,60],[61,77,78],[77,60,59],[76,77,59],[116,117,97],[117,98,97],[117,116,137],[117,137,138],[79,62,78],[62,61,78],[68,87,69],[86,87,68],[106,87,105],[87,86,105],[165,146,145],[166,146,165],[146,166,167],[146,167,147],[127,146,147],[126,146,127],[103,125,104],[125,103,124],[125,146,126],[146,125,145],[144,125,124],[125,144,145],[125,126,105],[104,125,105],[195,175,194],[175,195,176],[175,156,155],[156,175,176],[175,155,154],[174,175,154],[254,233,253],[233,254,234],[215,235,216],[235,215,234],[235,217,216],[217,235,236],[235,254,255],[254,235,234],[235,256,236],[256,235,255],[423,404,422],[404,423,405],[423,406,405],[423,424,406],[460,440,459],[440,460,441],[458,478,459],[477,478,458],[478,460,459],[460,478,479],[374,353,373],[353,374,354],[313,314,293],[292,313,293],[314,334,315],[315,334,335],[313,334,314],[334,313,333],[334,353,354],[353,334,333],[408,389,388],[389,408,409],[370,369,388],[389,370,388],[438,458,439],[438,457,458],[438,420,419],[420,438,439],[457,438,456],[438,437,456],[418,438,419],[437,438,418],[213,195,194],[195,213,214],[233,213,232],[213,233,214],[232,213,231],[213,212,231],[193,175,174],[175,193,194],[213,193,212],[193,213,194],[193,174,173],[193,173,192],[211,193,192],[212,193,211],[151,130,150],[151,131,130],[131,151,132],[151,152,132],[151,171,172],[152,151,172],[288,310,289],[310,288,309],[329,328,348],[349,329,348],[328,329,308],[308,329,309],[310,329,330],[329,310,309],[227,247,248],[227,248,228],[227,226,246],[247,227,246],[267,249,248],[249,267,268],[212,230,231],[230,212,211],[198,197,216],[217,198,216],[177,195,196],[195,177,176],[109,129,130],[129,109,108],[151,170,171],[170,151,150],[171,170,190],[170,189,190],[170,169,188],[189,170,188],[55,40,39],[40,55,56],[90,73,72],[73,90,91],[55,73,56],[73,55,72],[92,73,91],[74,73,92],[73,74,57],[73,57,56],[95,96,78],[77,95,78],[55,71,72],[71,55,54],[292,273,272],[273,292,293],[272,273,253],[273,254,253],[441,442,422],[442,423,422],[424,442,443],[423,442,424],[499,515,500],[499,514,515],[481,480,500],[480,499,500],[498,480,479],[499,480,498],[478,497,479],[497,498,479],[497,478,477],[497,477,496],[371,352,351],[352,371,372],[352,372,373],[353,352,373],[372,390,391],[371,390,372],[411,390,410],[390,411,391],[390,370,389],[370,390,371],[410,390,409],[390,389,409],[329,350,330],[350,329,349],[350,349,369],[370,350,369],[350,370,371],[350,371,351],[207,208,188],[208,189,188],[208,209,190],[189,208,190],[208,207,226],[227,208,226],[208,227,228],[209,208,228],[286,306,307],[306,286,285],[269,250,268],[250,249,268],[250,269,270],[251,250,270],[250,251,231],[230,250,231],[229,250,230],[250,229,249],[209,229,210],[229,209,228],[248,229,228],[249,229,248],[229,211,210],[229,230,211],[137,157,138],[157,158,138],[156,157,136],[157,137,136],[157,156,176],[177,157,176],[178,157,177],[157,178,158],[198,178,197],[178,198,179],[178,179,159],[158,178,159],[178,196,197],[178,177,196],[167,148,147],[168,148,167],[58,41,57],[41,58,42],[41,40,56],[57,41,56],[25,26,11],[11,26,12],[26,25,39],[40,26,39],[94,75,93],[94,76,75],[94,77,76],[94,95,77],[109,89,108],[90,89,109],[89,90,72],[71,89,72],[254,274,255],[273,274,254],[274,293,294],[274,273,293],[460,461,441],[461,442,441],[443,461,462],[442,461,443],[461,460,479],[480,461,479],[461,481,462],[461,480,481],[332,353,333],[332,352,353],[247,266,248],[266,267,248],[266,246,265],[266,247,246],[266,265,285],[286,266,285],[287,266,286],[266,287,267],[287,308,309],[288,287,309],[287,288,268],[267,287,268],[308,287,307],[287,286,307],[128,129,108],[107,128,108],[128,106,127],[106,128,107],[128,127,147],[148,128,147],[149,128,148],[128,149,129],[149,170,150],[170,149,169],[130,149,150],[129,149,130],[169,149,168],[149,148,168],[28,27,42],[27,41,42],[14,27,28],[27,14,13],[41,27,40],[27,26,40],[26,27,12],[27,13,12],[133,113,112],[113,133,134],[112,113,93],[113,94,93],[88,87,106],[88,106,107],[89,88,108],[88,107,108],[311,290,289],[310,311,289],[96,114,115],[95,114,96],[115,114,136],[114,135,136],[94,114,95],[113,114,94],[135,114,134],[114,113,134],[53,70,54],[70,71,54],[70,52,69],[70,53,52],[70,89,71],[70,88,89],[87,70,69],[88,70,87],[313,312,333],[312,332,333],[312,292,291],[312,313,292],[290,312,291],[311,312,290],[331,312,311],[312,331,332],[331,350,351],[350,331,330],[352,331,351],[332,331,352],[331,310,330],[331,311,310]],"uv":[[0.0,0.457627],[0.006568,0.416667],[0.007389,0.504237],[0.013136,0.375706],[0.014778,0.550847],[0.019704,0.334746],[0.022167,0.597458],[0.026273,0.293785],[0.029557,0.644068],[0.029557,0.59322],[0.029557,0.542373],[0.029557,0.491525],[0.029557,0.440678],[0.029557,0.389831],[0.029557,0.338983],[0.029557,0.288136],[0.032841,0.252825],[0.036946,0.690678],[0.039409,0.211864],[0.044335,0.737288],[0.059113,0.745763],[0.059113,0.694915],[0.059113,0.644068],[0.059113,0.59322],[0.059113,0.542373],[0.059113,0.491525],[0.059113,0.440678],[0.059113,0.389831],[0.059113,0.338983],[0.059113,0.288136],[0.059113,0.237288],[0.059113,0.186441],[0.067871,0.757062],[0.078818,0.161017],[0.08867,0.745763],[0.08867,0.694915],[0.08867,0.644068],[0.08867,0.59322],[0.08867,0.542373],[0.08867,0.491525],[0.08867,0.440678],[0.08867,0.389831],[0.08867,0.338983],[0.08867,0.288136],[0.08867,0.237288],[0.08867,0.186441],[0.091407,0.776836],[0.098522,0.135593],[0.114943,0.79661],[0.118227,0.79661],[0.118227,0.745763],[0.118227,0.694915],[0.118227,0.644068],[0.118227,0.59322],[0.118227,0.542373],[0.118227,0.491525],[0.118227,0.440678],[0.118227,0.389831],[0.118227,0.338983],[0.118227,0.288136],[0.118227,0.237288],[0.118227,0.186441],[0.118227,0.135593],[0.118227,0.110169],[0.137931,0.084746],[0.138478,0.816384],[0.147783,0.79661],[0.147783,0.745763],[0.147783,0.694915],[0.147783,0.644068],[0.147783,0.59322],[0.147783,0.542373],[0.147783,0.491525],[0.147783,0.440678],[0.147783,0.389831],[0.147783,0.338983],[0.147783,0.288136],[0.147783,0.237288],[0.147783,0.186441],[0.147783,0.135593],[0.147783,0.084746],[0.157635,0.059322],[0.162014,0.836158],[0.17734,0.847458],[0.17734,0.79661],[0.17734,0.745763],[0.17734,0.694915],[0.17734,0.644068],[0.17734,0.59322],[0.17734,0.542373],[0.17734,0.491525],[0.17734,0.440678],[0.17734,0.389831],[0.17734,0.338983],[0.17734,0.288136],[0.17734,0.237288],[0.17734,0.186441],[0.17734,0.135593],[0.17734,0.084746],[0.17734,0.033898],[0.18555,0.855932],[0.203202,0.03072],[0.206897,0.847458],[0.206897,0.79661],[0.206897,0.745763],[0.206897,0.694915],[0.206897,0.644068],[0.206897,0.59322],[0.206897,0.542373],[0.206897,0.491525],[0.206897,0.440678],[0.206897,0.389831],[0.206897,0.338983],[0.206897,0.288136],[0.206897,0.237288],[0.206897,0.186441],[0.206897,0.135593],[0.206897,0.084746],[0.206897,0.033898],[0.209086,0.875706],[0.229064,0.027542],[0.232622,0.89548],[0.236453,0.898305],[0.236453,0.847458],[0.236453,0.79661],[0.236453,0.745763],[0.236453,0.694915],[0.236453,0.644068],[0.236453,0.59322],[0.236453,0.542373],[0.236453,0.491525],[0.236453,0.440678],[0.236453,0.389831],[0.236453,0.338983],[0.236453,0.288136],[0.236453,0.237288],[0.236453,0.186441],[0.236453,0.135593],[0.236453,0.084746],[0.236453,0.033898],[0.254926,0.024364],[0.256158,0.915254],[0.26601,0.898305],[0.26601,0.847458],[0.26601,0.79661],[0.26601,0.745763],[0.26601,0.694915],[0.26601,0.644068],[0.26601,0.59322],[0.26601,0.542373],[0.26601,0.491525],[0.26601,0.440678],[0.26601,0.389831],[0.26601,0.338983],[0.26601,0.288136],[0.26601,0.237288],[0.26601,0.186441],[0.26601,0.135593],[0.26601,0.084746],[0.26601,0.033898],[0.280788,0.021186],[0.28243,0.902542],[0.295567,0.898305],[0.295567,0.847458],[0.295567,0.79661],[0.295567,0.745763],[0.295567,0.694915],[0.295567,0.644068],[0.295567,0.59322],[0.295567,0.542373],[0.295567,0.491525],[0.295567,0.440678],[0.295567,0.389831],[0.295567,0.338983],[0.295567,0.288136],[0.295567,0.237288],[0.295567,0.186441],[0.295567,0.135593],[0.295567,0.084746],[0.295567,0.033898],[0.30665,0.018008],[0.308703,0.889831],[0.325123,0.847458],[0.325123,0.79661],[0.325123,0.745763],[0.325123,0.694915],[0.325123,0.644068],[0.325123,0.59322],[0.325123,0.542373],[0.325123,0.491525],[0.325123,0.440678],[0.325123,0.389831],[0.325123,0.338983],[0.325123,0.288136],[0.325123,0.237288],[0.325123,0.186441],[0.325123,0.135593],[0.325123,0.084746],[0.325123,0.033898],[0.332512,0.014831],[0.334975,0.877119],[0.35468,0.847458],[0.35468,0.79661],[0.35468,0.745763],[0.35468,0.694915],[0.35468,0.644068],[0.35468,0.59322],[0.35468,0.542373],[0.35468,0.491525],[0.35468,0.440678],[0.35468,0.389831],[0.35468,0.338983],[0.35468,0.288136],[0.35468,0.237288],[0.35468,0.186441],[0.35468,0.135593],[0.35468,0.084746],[0.35468,0.033898],[0.358374,0.011653],[0.361248,0.864407],[0.384236,0.847458],[0.384236,0.79661],[0.384236,0.745763],[0.384236,0.694915],[0.384236,0.644068],[0.384236,0.59322],[0.384236,0.542373],[0.384236,0.491525],[0.384236,0.440678],[0.384236,0.389831],[0.384236,0.338983],[0.384236,0.288136],[0.384236,0.237288],[0.384236,0.186441],[0.384236,0.135593],[0.384236,0.084746],[0.384236,0.033898],[0.384236,0.008475],[0.387521,0.851695],[0.410509,0.020716],[0.413793,0.838983],[0.413793,0.79661],[0.413793,0.745763],[0.413793,0.694915],[0.413793,0.644068],[0.413793,0.59322],[0.413793,0.542373],[0.413793,0.491525],[0.413793,0.440678],[0.413793,0.389831],[0.413793,0.338983],[0.413793,0.288136],[0.413793,0.237288],[0.413793,0.186441],[0.413793,0.135593],[0.413793,0.084746],[0.413793,0.033898],[0.434319,0.865819],[0.436782,0.032957],[0.44335,0.847458],[0.44335,0.79661],[0.44335,0.745763],[0.44335,0.694915],[0.44335,0.644068],[0.44335,0.59322],[0.44335,0.542373],[0.44335,0.491525],[0.44335,0.440678],[0.44335,0.389831],[0.44335,0.338983],[0.44335,0.288136],[0.44335,0.237288],[0.44335,0.186441],[0.44335,0.135593],[0.44335,0.084746],[0.44335,0.033898],[0.454844,0.892655],[0.463054,0.045198],[0.472906,0.898305],[0.472906,0.847458],[0.472906,0.79661],[0.472906,0.745763],[0.472906,0.694915],[0.472906,0.644068],[0.472906,0.59322],[0.472906,0.542373],[0.472906,0.491525],[0.472906,0.440678],[0.472906,0.389831],[0.472906,0.338983],[0.472906,0.288136],[0.472906,0.237288],[0.472906,0.186441],[0.472906,0.135593],[0.472906,0.084746],[0.475369,0.919492],[0.489327,0.057439],[0.495895,0.946328],[0.502463,0.949153],[0.502463,0.898305],[0.502463,0.847458],[0.502463,0.79661],[0.502463,0.745763],[0.502463,0.694915],[0.502463,0.644068],[0.502463,0.59322],[0.502463,0.542373],[0.502463,0.491525],[0.502463,0.440678],[0.502463,0.389831],[0.502463,0.338983],[0.502463,0.288136],[0.502463,0.237288],[0.502463,0.186441],[0.502463,0.135593],[0.502463,0.084746],[0.515599,0.06968],[0.51642,0.973164],[0.53202,0.949153],[0.53202,0.898305],[0.53202,0.847458],[0.53202,0.79661],[0.53202,0.745763],[0.53202,0.694915],[0.53202,0.644068],[0.53202,0.59322],[0.53202,0.542373],[0.53202,0.491525],[0.53202,0.440678],[0.53202,0.389831],[0.53202,0.338983],[0.53202,0.288136],[0.53202,0.237288],[0.53202,0.186441],[0.53202,0.135593],[0.53202,0.084746],[0.536946,1.0],[0.541872,0.081921],[0.561576,0.949153],[0.561576,0.898305],[0.561576,0.847458],[0.561576,0.79661],[0.561576,0.745763],[0.561576,0.694915],[0.561576,0.644068],[0.561576,0.59322],[0.561576,0.542373],[0.561576,0.491525],[0.561576,0.440678],[0.561576,0.389831],[0.561576,0.338983],[0.561576,0.288136],[0.561576,0.237288],[0.561576,0.186441],[0.561576,0.135593],[0.562671,0.987759],[0.568144,0.094162],[0.588396,0.975518],[0.591133,0.949153],[0.591133,0.898305],[0.591133,0.847458],[0.591133,0.79661],[0.591133,0.745763],[0.591133,0.694915],[0.591133,0.644068],[0.591133,0.59322],[0.591133,0.542373],[0.591133,0.491525],[0.591133,0.440678],[0.591133,0.389831],[0.591133,0.338983],[0.591133,0.288136],[0.591133,0.237288],[0.591133,0.186441],[0.591133,0.135593],[0.594417,0.106403],[0.614122,0.963277],[0.62069,0.949153],[0.62069,0.898305],[0.62069,0.847458],[0.62069,0.79661],[0.62069,0.745763],[0.62069,0.694915],[0.62069,0.644068],[0.62069,0.59322],[0.62069,0.542373],[0.62069,0.491525],[0.62069,0.440678],[0.62069,0.389831],[0.62069,0.338983],[0.62069,0.288136],[0.62069,0.237288],[0.62069,0.186441],[0.62069,0.135593],[0.62069,0.118644],[0.639847,0.951036],[0.64532,0.101695],[0.650246,0.949153],[0.650246,0.898305],[0.650246,0.847458],[0.650246,0.79661],[0.650246,0.745763],[0.650246,0.694915],[0.650246,0.644068],[0.650246,0.59322],[0.650246,0.542373],[0.650246,0.491525],[0.650246,0.440678],[0.650246,0.389831],[0.650246,0.338983],[0.650246,0.288136],[0.650246,0.237288],[0.650246,0.186441],[0.650246,0.135593],[0.665572,0.938795],[0.669951,0.084746],[0.679803,0.898305],[0.679803,0.847458],[0.679803,0.79661],[0.679803,0.745763],[0.679803,0.694915],[0.679803,0.644068],[0.679803,0.59322],[0.679803,0.542373],[0.679803,0.491525],[0.679803,0.440678],[0.679803,0.389831],[0.679803,0.338983],[0.679803,0.288136],[0.679803,0.237288],[0.679803,0.186441],[0.679803,0.135593],[0.679803,0.084746],[0.691297,0.926554],[0.694581,0.067797],[0.70936,0.898305],[0.70936,0.847458],[0.70936,0.79661],[0.70936,0.745763],[0.70936,0.694915],[0.70936,0.644068],[0.70936,0.59322],[0.70936,0.542373],[0.70936,0.491525],[0.70936,0.440678],[0.70936,0.389831],[0.70936,0.338983],[0.70936,0.288136],[0.70936,0.237288],[0.70936,0.186441],[0.70936,0.135593],[0.70936,0.084746],[0.717022,0.914313],[0.719212,0.050847],[0.738916,0.898305],[0.738916,0.847458],[0.738916,0.79661],[0.738916,0.745763],[0.738916,0.694915],[0.738916,0.644068],[0.738916,0.59322],[0.738916,0.542373],[0.738916,0.491525],[0.738916,0.440678],[0.738916,0.389831],[0.738916,0.338983],[0.738916,0.288136],[0.738916,0.237288],[0.738916,0.186441],[0.738916,0.135593],[0.738916,0.084746],[0.742748,0.902072],[0.743842,0.069007],[0.768473,0.889831],[0.768473,0.847458],[0.768473,0.79661],[0.768473,0.745763],[0.768473,0.694915],[0.768473,0.644068],[0.768473,0.59322],[0.768473,0.542373],[0.768473,0.491525],[0.768473,0.440678],[0.768473,0.389831],[0.768473,0.338983],[0.768473,0.288136],[0.768473,0.237288],[0.768473,0.186441],[0.768473,0.135593],[0.768473,0.087167],[0.768473,0.084746],[0.776478,0.849576],[0.784483,0.809322],[0.792488,0.769068],[0.793103,0.105327],[0.79803,0.745763],[0.79803,0.694915],[0.79803,0.644068],[0.79803,0.59322],[0.79803,0.542373],[0.79803,0.491525],[0.79803,0.440678],[0.79803,0.389831],[0.79803,0.338983],[0.79803,0.288136],[0.79803,0.237288],[0.79803,0.186441],[0.79803,0.135593],[0.800493,0.728814],[0.808498,0.688559],[0.816502,0.648305],[0.817734,0.123487],[0.824507,0.608051],[0.827586,0.59322],[0.827586,0.542373],[0.827586,0.491525],[0.827586,0.440678],[0.827586,0.389831],[0.827586,0.338983],[0.827586,0.288136],[0.827586,0.237288],[0.827586,0.186441],[0.827586,0.135593],[0.832512,0.567797],[0.842365,0.141646],[0.855735,0.543584],[0.857143,0.542373],[0.857143,0.491525],[0.857143,0.440678],[0.857143,0.389831],[0.857143,0.338983],[0.857143,0.288136],[0.857143,0.237288],[0.857143,0.186441],[0.866995,0.159806],[0.878958,0.51937],[0.8867,0.491525],[0.8867,0.440678],[0.8867,0.389831],[0.8867,0.338983],[0.8867,0.288136],[0.8867,0.237288],[0.8867,0.186441],[0.891626,0.177966],[0.902182,0.495157],[0.908867,0.214689],[0.916256,0.440678],[0.916256,0.389831],[0.916256,0.338983],[0.916256,0.288136],[0.916256,0.237288],[0.925405,0.470944],[0.926108,0.251412],[0.94335,0.288136],[0.945813,0.440678],[0.945813,0.389831],[0.945813,0.338983],[0.948628,0.446731],[0.960591,0.324859],[0.971851,0.422518],[0.975369,0.389831],[0.977833,0.361582],[0.995074,0.398305]],"contact_weight":[0.1,0.11706,0.08536,0.08698,0.1,0.1,0.08536,0.08698,0.1,0.22361,0.4,0.53852,0.6,0.44721,0.3,0.1,0.06667,0.08536,0.1,0.1,0.1,0.5,0.63246,0.80623,0.94868,1.0,1.0,1.0,0.8544,0.7,0.53852,0.1,0.06667,0.1,0.36056,0.89443,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.89443,0.42426,0.07804,0.1,0.1,0.1,0.63246,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.78102,0.28284,0.1,0.1,0.07344,0.36056,0.89443,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.64031,0.2,0.1,0.10487,0.1,0.63246,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.53852,0.1,0.1,0.0625,0.36056,0.89443,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.7,0.1,0.09375,0.125,0.11815,0.1,0.63246,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8,0.2,0.0875,0.1,0.2,0.8,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8,0.2,0.05,0.09714,0.1,0.60828,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8544,0.3,0.1125,0.1,0.5,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.9,0.3,0.075,0.12071,0.3,0.89443,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.90554,0.31623,0.13201,0.1,0.14142,0.70711,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.92195,0.36056,0.1,0.07357,0.09375,0.1,0.6,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.8,0.2,0.09186,0.08889,0.36056,0.84853,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.60828,0.1,0.08698,0.11381,0.22361,0.70711,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5,0.08536,0.07778,0.08698,0.14142,0.5831,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.89443,0.31623,0.11354,0.09186,0.5,0.9434,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.72801,0.2,0.1,0.06667,0.5099,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5831,0.10197,0.10894,0.08889,0.36056,0.92195,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.41231,0.07804,0.12032,0.2,0.76158,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.9,0.3,0.1,0.07778,0.1,0.1,0.60828,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5,0.12078,0.1,0.44721,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.70711,0.14142,0.06667,0.1,0.3,0.8544,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.92195,0.36056,0.1075,0.1,0.14142,0.70711,0.98489,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.82462,0.28284,0.07055,0.10592,0.1,0.22361,0.41231,0.60828,0.80623,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,1.0,0.5831,0.11183,0.1,0.09602,0.075,0.10701,0.11775,0.1,0.28284,0.44721,0.63246,0.8544,1.0,1.0,1.0,1.0,1.0,1.0,0.89443,0.36056,0.05,0.0875,0.11768,0.05714,0.07835,0.1,0.36056,0.89443,1.0,1.0,1.0,1.0,1.0,0.67082,0.14142,0.1,0.07143,0.09994,0.1,0.5831,1.0,1.0,1.0,1.0,0.98489,0.44721,0.08571,0.10466,0.28284,0.78102,1.0,1.0,0.9434,0.56569,0.2,0.1,0.11417,0.07357,0.44721,0.98489,0.84853,0.5,0.14142,0.05356,0.06667,0.05,0.14142,0.67082,0.42426,0.06426,0.11381,0.07974,0.31623,0.09714,0.1]},"BlueClothes":{"vertices":[[3.03001,3.85249,0.69257],[3.039,3.85006,0.7135],[3.05487,3.85518,0.73114],[3.02785,3.82807,0.68044],[3.07458,3.8644,0.74719],[3.097,3.87642,0.76228],[3.0735,3.8541,0.7446],[3.05144,3.83327,0.72673],[3.03634,3.81998,0.70589],[3.02857,3.81466,0.6816],[3.02753,3.80583,0.66747],[3.02765,3.78423,0.65437],[3.12942,3.87284,0.76972],[3.11465,3.85667,0.76073],[3.0869,3.83016,0.74493],[3.06163,3.80622,0.72857],[3.04432,3.79074,0.70888],[3.0359,3.78473,0.68503],[3.02986,3.78127,0.66001],[3.03139,3.76662,0.63943],[3.16538,3.87245,0.77593],[3.16425,3.86729,0.77389],[3.13357,3.83821,0.75862],[3.10357,3.80976,0.74374],[3.07709,3.78493,0.72789],[3.05838,3.76819,0.70885],[3.04723,3.75936,0.68639],[3.03862,3.75319,0.66277],[3.03421,3.75143,0.63699],[3.03714,3.74038,0.63523],[3.20735,3.87761,0.77998],[3.18619,3.85138,0.77061],[3.1541,3.82131,0.75582],[3.12214,3.79131,0.7417],[3.09558,3.76679,0.72579],[3.07547,3.74885,0.70735],[3.06115,3.73685,0.68643],[3.04966,3.72774,0.66431],[3.04403,3.72468,0.63926],[3.0447,3.71625,0.63007],[3.21939,3.85208,0.77128],[3.21024,3.83739,0.76648],[3.1762,3.80586,0.75236],[3.14367,3.77577,0.73834],[3.11606,3.75058,0.72277],[3.09375,3.73069,0.70528],[3.07623,3.71561,0.68588],[3.06472,3.70658,0.66377],[3.05735,3.70174,0.63964],[3.05158,3.69158,0.62528],[3.23071,3.82596,0.76289],[3.23473,3.82357,0.7622],[3.19994,3.79182,0.74822],[3.16756,3.76237,0.73395],[3.13843,3.73609,0.71891],[3.11463,3.71503,0.70199],[3.09519,3.69825,0.68343],[3.08252,3.68817,0.66184],[3.07227,3.68049,0.63918],[3.05569,3.66649,0.62001],[3.05771,3.66636,0.6209],[3.24222,3.80014,0.75443],[3.06487,3.64237,0.61597],[3.22362,3.77747,0.74415],[3.19107,3.74834,0.72978],[3.16116,3.72172,0.71493],[3.13659,3.70024,0.69822],[3.11711,3.68368,0.67955],[3.1027,3.67203,0.65873],[3.09034,3.66238,0.63708],[3.07195,3.64679,0.6188],[3.27295,3.79904,0.75599],[3.07063,3.61719,0.6118],[3.28178,3.79331,0.75457],[3.24578,3.76144,0.74076],[3.21353,3.73307,0.72611],[3.18424,3.70747,0.71082],[3.15967,3.68633,0.69396],[3.13979,3.66963,0.67535],[3.124,3.65682,0.65509],[3.11015,3.64584,0.63411],[3.09015,3.62895,0.61658],[3.31069,3.80413,0.7549],[3.09438,3.61128,0.61159],[3.30815,3.78049,0.74964],[3.26913,3.74633,0.7369],[3.23721,3.71872,0.72194],[3.20845,3.69404,0.70625],[3.18278,3.67219,0.68974],[3.16187,3.65479,0.67148],[3.14645,3.6425,0.65092],[3.13218,3.63128,0.63003],[3.11059,3.61314,0.6132],[3.36718,3.82601,0.74658],[3.11931,3.60618,0.61089],[3.45678,3.87707,0.72572],[3.35179,3.78318,0.73771],[3.29646,3.73464,0.7314],[3.26306,3.70614,0.71687],[3.23335,3.68098,0.70141],[3.20595,3.65789,0.68552],[3.18475,3.64047,0.66726],[3.1707,3.62963,0.64591],[3.15834,3.62031,0.62392],[3.14612,3.60251,0.60935],[3.50468,3.88844,0.70015],[3.45299,3.83673,0.70258],[3.33199,3.72999,0.72252],[3.28866,3.69301,0.71196],[3.25775,3.66717,0.69685],[3.23024,3.64437,0.68083],[3.20954,3.62766,0.66218],[3.19766,3.61892,0.63963],[3.18645,3.61077,0.61691],[3.17518,3.60052,0.60682],[3.54539,3.89247,0.67726],[3.20055,3.59482,0.60633],[3.5589,3.89163,0.666],[3.40324,3.75627,0.69889],[3.31734,3.68232,0.70577],[3.28423,3.65496,0.6914],[3.25766,3.63334,0.67477],[3.23772,3.61758,0.65557],[3.22605,3.60912,0.63274],[3.21279,3.59925,0.61085],[3.22995,3.59917,0.61478],[3.58191,3.89208,0.65591],[3.56579,3.8599,0.63781],[3.40671,3.72389,0.67377],[3.31848,3.64917,0.68261],[3.28806,3.62456,0.66742],[3.2682,3.60909,0.64796],[3.25629,3.60053,0.62504],[3.67066,3.93603,0.61323],[3.65993,3.92167,0.59128],[3.28485,3.60354,0.63453],[3.51474,3.79212,0.62689],[3.39982,3.68904,0.65226],[3.34933,3.64135,0.65113],[3.34176,3.63519,0.64967],[3.33057,3.62086,0.63646]],"faces":[[6,4,5],[31,30,40],[31,21,30],[63,71,74],[71,63,61],[116,113,124],[125,116,124],[125,123,132],[123,125,124],[7,8,1],[1,8,0],[2,7,1],[2,4,6],[7,2,6],[10,3,9],[8,3,0],[3,8,9],[17,10,9],[59,28,39],[21,20,30],[41,31,40],[50,41,40],[41,50,42],[21,22,12],[106,105,115],[50,52,42],[63,52,61],[71,73,74],[73,71,82],[139,138,140],[113,114,104],[114,113,116],[130,138,139],[130,139,140],[138,130,137],[10,18,11],[17,18,10],[28,19,27],[19,18,27],[18,19,11],[49,59,39],[51,50,40],[52,51,61],[51,52,50],[22,13,12],[13,5,12],[83,81,92],[81,83,72],[70,81,72],[126,117,115],[117,126,118],[117,106,115],[127,126,133],[126,127,118],[105,96,95],[96,105,106],[96,93,95],[130,129,137],[129,136,137],[136,129,128],[91,94,92],[94,83,92],[103,113,104],[94,103,104],[103,94,91],[29,28,27],[28,29,39],[29,38,39],[38,48,39],[48,49,39],[49,48,58],[60,49,58],[49,60,59],[59,62,72],[62,70,72],[60,62,59],[62,60,70],[69,60,58],[60,69,70],[134,127,133],[127,134,128],[134,136,128],[93,84,82],[84,73,82],[96,84,93],[135,131,140],[131,130,140],[131,135,132],[37,29,27],[29,37,38],[123,113,112],[113,123,124],[25,17,16],[17,25,26],[17,8,16],[8,17,9],[41,42,32],[31,41,32],[22,31,32],[31,22,21],[8,15,16],[15,8,7],[15,7,6],[14,15,6],[97,108,98],[108,97,107],[108,109,98],[98,109,99],[64,52,63],[52,64,53],[64,63,74],[75,64,74],[67,57,56],[57,67,68],[55,67,56],[67,55,66],[109,100,99],[100,109,110],[101,100,111],[100,110,111],[102,101,111],[112,102,111],[91,79,90],[79,91,80],[89,100,101],[100,89,88],[102,89,101],[89,102,90],[27,18,26],[18,17,26],[13,14,6],[13,6,5],[91,81,80],[81,91,92],[117,118,107],[106,117,107],[119,109,108],[109,119,120],[118,119,107],[119,108,107],[127,119,118],[119,127,128],[97,96,107],[96,106,107],[100,87,99],[87,100,88],[87,98,99],[87,86,98],[85,75,74],[85,86,75],[85,97,98],[86,85,98],[55,65,66],[65,55,54],[64,65,53],[65,54,53],[122,123,112],[122,112,111],[119,129,120],[129,119,128],[83,94,72],[94,116,72],[114,94,104],[94,114,116],[113,103,112],[103,102,112],[103,91,90],[102,103,90],[67,78,68],[78,79,68],[78,67,66],[78,66,77],[79,78,90],[78,89,90],[89,78,88],[88,78,77],[69,57,68],[57,69,58],[69,79,80],[79,69,68],[81,69,80],[69,81,70],[84,85,74],[73,84,74],[84,96,97],[85,84,97],[87,76,86],[86,76,75],[76,87,88],[76,88,77],[76,64,75],[76,65,64],[65,76,66],[66,76,77],[24,25,16],[15,24,16],[24,35,25],[24,34,35],[43,52,53],[52,43,42],[42,43,32],[43,33,32],[129,121,120],[121,129,130],[109,121,110],[121,109,120],[110,121,111],[121,122,111],[131,121,130],[121,131,122],[122,131,123],[123,131,132],[23,22,32],[33,23,32],[13,23,14],[23,13,22],[24,23,34],[34,23,33],[23,15,14],[23,24,15],[34,44,35],[35,44,45],[55,44,54],[44,55,45],[44,34,33],[43,44,33],[54,44,53],[44,43,53],[35,36,25],[25,36,26],[36,27,26],[36,37,27],[47,57,58],[48,47,58],[47,48,38],[37,47,38],[46,35,45],[46,36,35],[46,55,56],[55,46,45],[36,46,37],[46,47,37],[57,46,56],[47,46,57]],"uv":[[0.0,0.448276],[0.017442,0.534483],[0.034884,0.62069],[0.043605,0.383621],[0.052326,0.706897],[0.069767,0.793103],[0.069767,0.689655],[0.069767,0.586207],[0.069767,0.482759],[0.069767,0.37931],[0.087209,0.318966],[0.130814,0.25431],[0.135659,0.850575],[0.139535,0.793103],[0.139535,0.689655],[0.139535,0.586207],[0.139535,0.482759],[0.139535,0.37931],[0.139535,0.275862],[0.174419,0.189655],[0.20155,0.908046],[0.209302,0.896552],[0.209302,0.793103],[0.209302,0.689655],[0.209302,0.586207],[0.209302,0.482759],[0.209302,0.37931],[0.209302,0.275862],[0.209302,0.172414],[0.236434,0.16092],[0.267442,0.965517],[0.27907,0.896552],[0.27907,0.793103],[0.27907,0.689655],[0.27907,0.586207],[0.27907,0.482759],[0.27907,0.37931],[0.27907,0.275862],[0.27907,0.172414],[0.29845,0.132184],[0.337209,0.931034],[0.348837,0.896552],[0.348837,0.793103],[0.348837,0.689655],[0.348837,0.586207],[0.348837,0.482759],[0.348837,0.37931],[0.348837,0.275862],[0.348837,0.172414],[0.360465,0.103448],[0.406977,0.896552],[0.418605,0.896552],[0.418605,0.793103],[0.418605,0.689655],[0.418605,0.586207],[0.418605,0.482759],[0.418605,0.37931],[0.418605,0.275862],[0.418605,0.172414],[0.418605,0.068966],[0.422481,0.074713],[0.476744,0.862069],[0.484496,0.045977],[0.488372,0.793103],[0.488372,0.689655],[0.488372,0.586207],[0.488372,0.482759],[0.488372,0.37931],[0.488372,0.275862],[0.488372,0.172414],[0.488372,0.068966],[0.531977,0.896552],[0.546512,0.017241],[0.55814,0.896552],[0.55814,0.793103],[0.55814,0.689655],[0.55814,0.586207],[0.55814,0.482759],[0.55814,0.37931],[0.55814,0.275862],[0.55814,0.172414],[0.55814,0.068966],[0.587209,0.931034],[0.602326,0.041379],[0.627907,0.896552],[0.627907,0.793103],[0.627907,0.689655],[0.627907,0.586207],[0.627907,0.482759],[0.627907,0.37931],[0.627907,0.275862],[0.627907,0.172414],[0.627907,0.068966],[0.642442,0.965517],[0.65814,0.065517],[0.697674,1.0],[0.697674,0.896552],[0.697674,0.793103],[0.697674,0.689655],[0.697674,0.586207],[0.697674,0.482759],[0.697674,0.37931],[0.697674,0.275862],[0.697674,0.172414],[0.713953,0.089655],[0.755814,0.961207],[0.767442,0.896552],[0.767442,0.793103],[0.767442,0.689655],[0.767442,0.586207],[0.767442,0.482759],[0.767442,0.37931],[0.767442,0.275862],[0.767442,0.172414],[0.769767,0.113793],[0.813953,0.922414],[0.825581,0.137931],[0.837209,0.896552],[0.837209,0.793103],[0.837209,0.689655],[0.837209,0.586207],[0.837209,0.482759],[0.837209,0.37931],[0.837209,0.275862],[0.837209,0.172414],[0.866279,0.206897],[0.872093,0.883621],[0.906977,0.793103],[0.906977,0.689655],[0.906977,0.586207],[0.906977,0.482759],[0.906977,0.37931],[0.906977,0.275862],[0.930233,0.844828],[0.94186,0.758621],[0.947674,0.344828],[0.953488,0.672414],[0.965116,0.586207],[0.976744,0.5],[0.976744,0.482759],[0.988372,0.413793]],"contact_weight":[0.1,0.12071,0.1,0.08902,0.12071,0.1,0.28284,0.41231,0.6,0.22361,0.08536,0.08902,0.1073,0.36056,0.82462,0.98489,1.0,0.64031,0.22361,0.1,0.06016,0.14142,0.67082,1.0,1.0,1.0,1.0,0.63246,0.1,0.12032,0.1,0.41231,0.98489,1.0,1.0,1.0,1.0,0.80623,0.22361,0.06667,0.1,0.22361,0.80623,1.0,1.0,1.0,1.0,0.98489,0.41231,0.1,0.1,0.1,0.60828,1.0,1.0,1.0,1.0,1.0,0.60828,0.1,0.12032,0.1,0.06667,0.5,1.0,1.0,1.0,1.0,1.0,0.76158,0.2,0.1,0.1,0.2,0.72111,1.0,1.0,1.0,1.0,1.0,0.90554,0.31623,0.1,0.12125,0.4,0.9434,1.0,1.0,1.0,1.0,1.0,0.72801,0.2,0.1,0.08,0.1,0.63246,1.0,1.0,1.0,1.0,1.0,1.0,0.5831,0.11531,0.11036,0.36056,0.92195,1.0,1.0,1.0,1.0,0.98489,0.4,0.06,0.12071,0.1,0.14142,0.67082,1.0,1.0,1.0,0.92195,0.5,0.14142,0.05,0.075,0.31623,0.44721,0.5831,0.7,0.44721,0.1,0.1,0.1,0.05,0.1,0.1,0.1,0.1,0.1]}}

SHELL = {
  "floor_z": -1.6592,
  "ceiling_z": 1.1608,
  "walls": {
    "Wall0": {
      "start": [
        0.5293,
        4.3332
      ],
      "end": [
        3.7357,
        4.9375
      ],
      "thickness": 0.0001
    },
    "Wall1": {
      "start": [
        0.8768,
        2.4892
      ],
      "end": [
        0.5293,
        4.3332
      ],
      "thickness": 0.0001
    },
    "Wall2": {
      "start": [
        -0.7643,
        2.1799
      ],
      "end": [
        0.8768,
        2.4892
      ],
      "thickness": 0.0001
    },
    "Wall3": {
      "start": [
        3.7357,
        4.9375
      ],
      "end": [
        4.2794,
        2.0526
      ],
      "thickness": 0.0001
    },
    "Wall4": {
      "start": [
        -0.5648,
        1.1212
      ],
      "end": [
        -0.7643,
        2.1799
      ],
      "thickness": 0.0001
    },
    "Wall5": {
      "start": [
        4.2794,
        2.0526
      ],
      "end": [
        0.3363,
        1.291
      ],
      "thickness": 0.0001
    },
    "Wall6": {
      "start": [
        0.3363,
        1.291
      ],
      "end": [
        -0.5648,
        1.1212
      ],
      "thickness": 0.0001
    }
  },
  "openings": {
    "Door0": {
      "wall": "Wall2",
      "type": "door",
      "offset": 0.50,
      "width": 0.76,
      "height": 2.3452,
      "sill": 0.0
    },
    "Door2": {
      "wall": "Wall6",
      "type": "door",
      "offset": 0.4866,
      "width": 0.86,
      "height": 2.3515,
      "sill": 0.0
    },
    "Window0": {
      "wall": "Wall3",
      "type": "window",
      "offset": 1.4777,
      "width": 2.6801,
      "height": 1.74,
      "sill": 0.63
    }
  },
  "objects": {
    "Bed0": {
      "category": "bed",
      "center": [
        2.2151113125585646,
        3.4738371980191363,
        -0.9892
      ],
      "size": [
        1.92,
        2.2894,
        1.34
      ],
      "yaw": 10.67
    },
    "Storage0": {
      "category": "storage",
      "center": [
        0.7427,
        3.4016,
        -0.47920000000000007
      ],
      "size": [
        1.6768,
        0.0743,
        2.36
      ],
      "yaw": 100.67
    },
    "Storage1": {
      "category": "storage",
      "center": [
        0.8812191825438066,
        4.180439899378631,
        -1.3742
      ],
      "size": [
        0.285,
        0.39,
        0.57
      ],
      "yaw": 10.67
    }
  },
  "floor": {
    "verts": [
      [
        -0.5648,
        1.1212,
        -1.6592
      ],
      [
        0.3363,
        1.291,
        -1.6592
      ],
      [
        -0.7643,
        2.1799,
        -1.6592
      ],
      [
        0.8768,
        2.4892,
        -1.6592
      ],
      [
        4.2794,
        2.0526,
        -1.6592
      ],
      [
        3.7357,
        4.9375,
        -1.6592
      ],
      [
        0.5293,
        4.3332,
        -1.6592
      ],
      [
        -0.5648,
        1.1212,
        -1.6593
      ],
      [
        0.3363,
        1.291,
        -1.6593
      ],
      [
        -0.7643,
        2.1799,
        -1.6593
      ],
      [
        0.8768,
        2.4892,
        -1.6593
      ],
      [
        4.2794,
        2.0526,
        -1.6593
      ],
      [
        3.7357,
        4.9375,
        -1.6593
      ],
      [
        0.5293,
        4.3332,
        -1.6593
      ],
      [
        4.2794,
        2.0526,
        -1.6592
      ],
      [
        0.3363,
        1.291,
        -1.6592
      ],
      [
        0.3363,
        1.291,
        -1.6593
      ],
      [
        4.2794,
        2.0526,
        -1.6593
      ],
      [
        3.7357,
        4.9375,
        -1.6592
      ],
      [
        4.2794,
        2.0526,
        -1.6592
      ],
      [
        4.2794,
        2.0526,
        -1.6593
      ],
      [
        3.7357,
        4.9375,
        -1.6593
      ],
      [
        0.5293,
        4.3332,
        -1.6592
      ],
      [
        3.7357,
        4.9375,
        -1.6592
      ],
      [
        3.7357,
        4.9375,
        -1.6593
      ],
      [
        0.5293,
        4.3332,
        -1.6593
      ],
      [
        0.8768,
        2.4892,
        -1.6592
      ],
      [
        0.5293,
        4.3332,
        -1.6592
      ],
      [
        0.5293,
        4.3332,
        -1.6593
      ],
      [
        0.8768,
        2.4892,
        -1.6593
      ],
      [
        -0.7643,
        2.1799,
        -1.6592
      ],
      [
        0.8768,
        2.4892,
        -1.6592
      ],
      [
        0.8768,
        2.4892,
        -1.6593
      ],
      [
        -0.7643,
        2.1799,
        -1.6593
      ],
      [
        -0.5648,
        1.1212,
        -1.6592
      ],
      [
        -0.7643,
        2.1799,
        -1.6592
      ],
      [
        -0.7643,
        2.1799,
        -1.6593
      ],
      [
        -0.5648,
        1.1212,
        -1.6593
      ],
      [
        0.3363,
        1.291,
        -1.6592
      ],
      [
        -0.5648,
        1.1212,
        -1.6592
      ],
      [
        -0.5648,
        1.1212,
        -1.6593
      ],
      [
        0.3363,
        1.291,
        -1.6593
      ]
    ],
    "faces": [
      [
        0,
        1,
        2
      ],
      [
        2,
        1,
        3
      ],
      [
        1,
        4,
        3
      ],
      [
        3,
        5,
        6
      ],
      [
        3,
        4,
        5
      ],
      [
        12,
        11,
        10
      ],
      [
        13,
        12,
        10
      ],
      [
        10,
        11,
        8
      ],
      [
        10,
        8,
        9
      ],
      [
        9,
        8,
        7
      ],
      [
        14,
        15,
        16
      ],
      [
        16,
        17,
        14
      ],
      [
        18,
        19,
        20
      ],
      [
        20,
        21,
        18
      ],
      [
        22,
        23,
        24
      ],
      [
        24,
        25,
        22
      ],
      [
        26,
        27,
        28
      ],
      [
        28,
        29,
        26
      ],
      [
        30,
        31,
        32
      ],
      [
        32,
        33,
        30
      ],
      [
        34,
        35,
        36
      ],
      [
        36,
        37,
        34
      ],
      [
        38,
        39,
        40
      ],
      [
        40,
        41,
        38
      ]
    ]
  },
  "root_matrix": [
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    -1.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0
  ]
}


if __name__ in ("__main__", "room_py_exec"):   # SHELL is now defined above
    ROOM = main()
