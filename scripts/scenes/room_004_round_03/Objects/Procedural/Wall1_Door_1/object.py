"""Single warm-beige flush door, left knob and right vertical hinge.
Scan dimensions take priority over the narrower photographic silhouette.

SELF-CONTAINED — the Blender helper library is inlined below, so this file
defines the object on its own (no external imports / sys.path hacks).

Run:
  blender -b --python object.py                       # uses ./textures, writes ./Wall1_Door_1.glb
  blender -b --python object.py -- <texdir> <out.glb>
"""

import bpy, bmesh, math, mathutils, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
_argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
TEXDIR = _argv[0] if _argv else os.path.join(HERE, 'textures')
OUT_GLB = _argv[1] if len(_argv) > 1 else os.path.join(HERE, 'Wall1_Door_1.glb')

# ==== inlined Blender helpers (generated from blender_lib.py; do not edit) ====
# ---------------------------------------------------------------- scene


def reset_scene(fps=24):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.render.fps = fps
    return scene


# ------------------------------------------------------------- materials


def make_plain_material(name, color, metallic=0.0, roughness=0.5):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    return mat


def make_pbr_material(
    name,
    diff_path,
    rough_path=None,
    normal_path=None,
    tint=None,
    tint_fac=0.45,
    normal_strength=0.6,
    metallic=0.0,
    fallback_roughness=0.55,
):
    """Principled BSDF wired to diffuse / roughness / normal image maps.
    tint: optional (r,g,b) multiplied over the diffuse (e.g. beech warm tone).
    Roughness/normal images are set to Non-Color automatically."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (900, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (600, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Metallic"].default_value = metallic

    tex_diff = nt.nodes.new("ShaderNodeTexImage")
    tex_diff.location = (-200, 300)
    tex_diff.image = bpy.data.images.load(diff_path)
    if tint is not None:
        mix = nt.nodes.new("ShaderNodeMixRGB")
        mix.location = (150, 300)
        mix.blend_type = "MULTIPLY"
        mix.inputs["Fac"].default_value = tint_fac
        mix.inputs["Color2"].default_value = (*tint, 1.0)
        nt.links.new(tex_diff.outputs["Color"], mix.inputs["Color1"])
        nt.links.new(mix.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        nt.links.new(tex_diff.outputs["Color"], bsdf.inputs["Base Color"])

    if rough_path:
        tex_r = nt.nodes.new("ShaderNodeTexImage")
        tex_r.location = (-200, 0)
        tex_r.image = bpy.data.images.load(rough_path)
        tex_r.image.colorspace_settings.name = "Non-Color"
        nt.links.new(tex_r.outputs["Color"], bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = fallback_roughness

    if normal_path:
        tex_n = nt.nodes.new("ShaderNodeTexImage")
        tex_n.location = (-200, -300)
        tex_n.image = bpy.data.images.load(normal_path)
        tex_n.image.colorspace_settings.name = "Non-Color"
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.location = (150, -300)
        nmap.inputs["Strength"].default_value = normal_strength
        nt.links.new(tex_n.outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


# ------------------------------------------------------------- primitives


def add_box(name, dims, loc, mat=None):
    """Axis-aligned box. dims are FULL extents (the size=1 cube is a unit
    cube, so scale = dims, NOT dims/2 — getting this wrong renders an
    'exploded' model with half-size parts)."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    ob = bpy.context.active_object
    ob.name = name
    # scale the mesh data directly: transform_apply(scale=True) in Blender
    # 5.1 also bakes LOCATION into the mesh (object jumps to world origin),
    # which silently breaks any later object-space rotation of the part
    ob.data.transform(mathutils.Matrix.Diagonal((*dims, 1.0)))
    if mat:
        ob.data.materials.append(mat)
    return ob


def add_wall_panel(
    name,
    start,
    end,
    v_lo,
    v_hi,
    mat=None,
    thickness=0.03,
    along_lo=0.0,
    along_hi=None,
    inset=0.0,
):
    """A thin material PANEL on a wall's interior face over a vertical band — for a tiled
    splashback / wainscot / dado / feature band that covers only PART of a wall's height.

    ``start, end`` = the wall's base endpoints ``(x, y)`` in world metres — read them straight
    from the room ``SHELL`` (each wall's ``start`` / ``end``). ``v_lo, v_hi`` = the band's height
    range above the floor (m), e.g. a kitchen splashback ``v_lo=0.9, v_hi=1.5``. ``along_lo /
    along_hi`` = optional span ALONG the wall (m from ``start``; default = the whole wall).

    The panel straddles the wall line and sits slightly proud on BOTH faces, so it reads on the
    interior without needing to know which side the room is on. Material it like the real surface
    (a recoloured tile / wood / enamel PBR — same fetch→recolour rule as the walls), and set the
    tile size via the material's UV mapping. Returns the panel object."""
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    length = math.hypot(ex - sx, ey - sy)
    if length < 1e-6:
        raise ValueError(f"add_wall_panel({name!r}): degenerate wall (start == end)")
    dx, dy = (ex - sx) / length, (ey - sy) / length  # along-wall unit
    nx, ny = -dy, dx  # horizontal wall normal
    a0 = max(0.0, float(along_lo))
    a1 = min(float(length if along_hi is None else along_hi), length)
    xl = max(a1 - a0, 1e-4)  # panel length along the wall
    zl = max(float(v_hi) - float(v_lo), 1e-4)  # panel height
    yl = max(float(thickness), 1e-4)  # panel thickness (straddles the wall)
    amid = 0.5 * (a0 + a1)
    cx = sx + dx * amid + nx * float(inset)
    cy = sy + dy * amid + ny * float(inset)
    cz = 0.5 * (float(v_lo) + float(v_hi))
    bpy.ops.mesh.primitive_cube_add(size=1)  # unit cube → oriented box via matrix
    ob = bpy.context.active_object
    ob.name = name
    ob.matrix_world = mathutils.Matrix(
        (
            (dx * xl, nx * yl, 0.0, cx),
            (dy * xl, ny * yl, 0.0, cy),
            (0.0, 0.0, zl, cz),
            (0.0, 0.0, 0.0, 1.0),
        )
    )
    uv_cube_project(ob, size=1.0)  # box UVs so a tile PBR maps sanely
    if mat:
        ob.data.materials.append(mat)
    return ob


def add_cylinder(name, radius, depth, loc, mat=None, rot=(0, 0, 0), vertices=24):
    bpy.ops.mesh.primitive_cylinder_add(
        radius=radius, depth=depth, location=loc, rotation=rot, vertices=vertices
    )
    ob = bpy.context.active_object
    ob.name = name
    if mat:
        ob.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    return ob


# ------------------------------------------------------- curved geometry


def _link_object(name, mesh, loc, mat):
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(ob)
    ob.location = loc
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    if mat:
        ob.data.materials.append(mat)
    return ob


def shade_smooth_auto(ob, angle=35):
    """Smooth shading with sharp edges preserved above `angle` degrees."""
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    try:
        bpy.ops.object.shade_auto_smooth(angle=math.radians(angle))
    except Exception:
        bpy.ops.object.shade_smooth()


def bevel(ob, width=0.004, segments=2, angle_limit=60):
    """Round the sharp edges of an existing object (applied immediately).
    Real furniture almost never has razor edges — 2-6 mm on desktops/fronts
    reads as much more lifelike in renders."""
    bpy.context.view_layer.objects.active = ob
    mod = ob.modifiers.new("Bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    mod.limit_method = "ANGLE"
    mod.angle_limit = math.radians(angle_limit)
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return ob


def add_rounded_box(name, dims, loc, mat=None, radius=0.008, segments=2):
    """add_box + edge bevel in one call. Use for tops, fronts, side panels
    whenever the reference photo shows softened edges."""
    ob = add_box(name, dims, loc, mat)
    bevel(ob, width=min(radius, min(dims) * 0.45), segments=segments)
    return ob


def add_lathe(name, profile, loc, mat=None, segments=48, smooth=True):
    """Surface of revolution around local +Z, e.g. turned legs, knobs,
    cylindrical handles, vases, lamp bases, bowl-shaped feet.
    profile: list of (radius, z) pairs from bottom to top, local to `loc`;
    use radius 0 at the ends to close the shape."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    verts = [bm.verts.new((r, 0.0, z)) for r, z in profile]
    edges = [bm.edges.new((verts[i], verts[i + 1])) for i in range(len(verts) - 1)]
    bmesh.ops.spin(
        bm, geom=verts + edges, angle=2 * math.pi, steps=segments, axis=(0, 0, 1), cent=(0, 0, 0)
    )
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    ob = _link_object(name, mesh, loc, mat)
    if smooth:
        shade_smooth_auto(ob)
    return ob


def add_arc_panel(name, width, height, thickness, bulge, loc, mat=None, segments=16, smooth=True):
    """Vertical panel whose face bows toward -Y (the front) by `bulge` at the
    center — curved drawer/door fronts, bowed cabinet sides, rounded desk
    aprons. Origin at the panel center; thickness extrudes toward +Y."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    rows = []
    for z in (-height / 2, height / 2):
        row = []
        for i in range(segments + 1):
            x = -width / 2 + width * i / segments
            y = -bulge * (1 - (2 * x / width) ** 2)  # parabolic bow
            row.append(bm.verts.new((x, y, z)))
        rows.append(row)
    for i in range(segments):
        bm.faces.new((rows[0][i], rows[0][i + 1], rows[1][i + 1], rows[1][i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    ob = _link_object(name, mesh, loc, mat)
    mod = ob.modifiers.new("Solidify", "SOLIDIFY")
    mod.thickness = thickness
    mod.offset = 1.0  # thicken toward +Y, curved face stays at the front
    bpy.ops.object.modifier_apply(modifier=mod.name)
    if smooth:
        shade_smooth_auto(ob)
    return ob


# -------------------------------------------------------------- topology


def uv_cube_project(ob, size=1.2):
    """Box-project UVs. Call on every textured part BEFORE joining."""
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.cube_project(cube_size=size, correct_aspect=True)
    bpy.ops.object.mode_set(mode="OBJECT")


def join_parts(objs, name, origin):
    """Join objects into one and put the origin at `origin` (world).
    For movable parts pick a meaningful origin: drawer front center, hinge
    line, so node translation/rotation is clean."""
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    ob = bpy.context.active_object
    ob.name = name
    bpy.context.scene.cursor.location = origin
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    return ob


def parent_keep_world(child, parent):
    """Parent without moving the child (keeps its world transform)."""
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()


# ----------------------------------------------------------- articulation


def set_articulation(ob, joint="prismatic", axis=(0, -1, 0), limit_min=0.0, limit_max=0.4,
                     origin=None, damping=None, friction=None, effort=None, velocity=None):
    """Tag a movable part; exported as glTF node extras (export_extras=True).
    prismatic: axis = slide direction, limits in meters.
    revolute:  axis = hinge axis, limits in radians.

    `origin` STATES the pivot in build-frame world coordinates. It is optional only because the
    build already puts a moving part's origin on its hinge line (see join_parts), so the pivot can
    be recovered from the node transform — but recovered is not the same as stated, and every
    consumer that has had to recover it has had to guess. Pass it when you know it, which is
    always: it is the same number handed to join_parts().

    `damping` / `friction` / `effort` / `velocity` describe how the joint MOVES. Leave them None
    and the sim compiler fills in defaults for the joint type; set them when the object is
    genuinely different (a soft-close drawer, a heavy fire door).
    """
    ob["articulation_type"] = joint
    ob["articulation_axis"] = list(axis)
    ob["limit_min"] = float(limit_min)
    ob["limit_max"] = float(limit_max)
    if origin is not None:
        ob["joint_origin"] = [float(v) for v in origin]
    for key, value in (("joint_damping", damping), ("joint_friction", friction),
                       ("joint_effort", effort), ("joint_velocity", velocity)):
        if value is not None:
            ob[key] = float(value)


def set_physics(ob, mass=None, density=None, friction=None, restitution=None):
    """State what a part is made of. Everything left None is derived from the mesh later.

    `mass` (kg) is the part on its own; `density` (kg/m^3 of its own mesh volume) is the
    alternative when the shape is known but the weight is not. Give one or neither, never both.
    `friction` is the sliding coefficient and `restitution` the bounce — the second is carried for
    Isaac and Bullet and ignored by MuJoCo, which has no such parameter.

    Author these when the reference actually tells you something: a cast-iron radiator is not the
    same object as a pressed-steel one, and no density table keyed on the word "radiator" can know
    which one is in the photograph.
    """
    for key, value in (("mass", mass), ("density", density),
                       ("friction", friction), ("restitution", restitution)):
        if value is not None:
            ob[key] = float(value)


def set_object_mass(ob, mass):
    """State the WHOLE object's mass on any one of its parts. Stops the sim compiler deriving a
    total from occupancy density and splits this across the links by volume instead."""
    ob["object_mass"] = float(mass)


def _schedule(n, open_frames, stagger, hold, close_stagger):
    sched = []
    open_done = 1 + (n - 1) * stagger + open_frames
    for i in range(n):
        t0 = 1 + i * stagger
        t2 = open_done + hold + i * close_stagger
        sched.append((t0, t0 + open_frames, t2, t2 + open_frames))
    return sched


def animate_prismatic(
    scene, items, axis_index=1, direction=-1, open_frames=30, stagger=10, hold=40, close_stagger=8
):
    """Staggered open-then-close slide animation, one action per part.
    items: list of (object, extension_m). axis_index 0/1/2 = X/Y/Z of
    object.location; direction -1 slides toward -axis (front)."""
    sched = _schedule(len(items), open_frames, stagger, hold, close_stagger)
    end = 1
    for (ob, ext), (t0, t1, t2, t3) in zip(items, sched):
        closed = ob.location[axis_index]
        opened = closed + direction * ext
        for f, v in ((1, closed), (t0, closed), (t1, opened), (t2, opened), (t3, closed)):
            ob.location[axis_index] = v
            ob.keyframe_insert(data_path="location", index=axis_index, frame=f)
        ob.location[axis_index] = closed
        if ob.animation_data and ob.animation_data.action:
            ob.animation_data.action.name = f"{ob.name}_slide"
        end = max(end, t3)
    scene.frame_start = 1
    scene.frame_end = max(scene.frame_end, end + 10)


def animate_revolute(
    scene, items, axis_index=2, open_frames=30, stagger=10, hold=40, close_stagger=8
):
    """Staggered swing animation for doors/lids. items: list of
    (object, angle_radians). Rotates object.rotation_euler[axis_index];
    put the object origin on the hinge line via join_parts()."""
    sched = _schedule(len(items), open_frames, stagger, hold, close_stagger)
    end = 1
    for (ob, ang), (t0, t1, t2, t3) in zip(items, sched):
        closed = ob.rotation_euler[axis_index]
        opened = closed + ang
        for f, v in ((1, closed), (t0, closed), (t1, opened), (t2, opened), (t3, closed)):
            ob.rotation_euler[axis_index] = v
            ob.keyframe_insert(data_path="rotation_euler", index=axis_index, frame=f)
        ob.rotation_euler[axis_index] = closed
        if ob.animation_data and ob.animation_data.action:
            ob.animation_data.action.name = f"{ob.name}_swing"
        end = max(end, t3)
    scene.frame_start = 1
    scene.frame_end = max(scene.frame_end, end + 10)


# ------------------------------------------------------ furniture builders


def build_carcass(name, x0, x1, y_front, y_back, z0, z1, mat, wall=0.015):
    """Open-front cabinet body: two sides, bottom, top, back. Origin at
    (center_x, center_y, z0)."""
    cx, cy = (x0 + x1) / 2, (y_front + y_back) / 2
    dx, dy = x1 - x0, y_back - y_front
    parts = [
        add_box("side_l", (wall, dy, z1 - z0), (x0 + wall / 2, cy, (z0 + z1) / 2), mat),
        add_box("side_r", (wall, dy, z1 - z0), (x1 - wall / 2, cy, (z0 + z1) / 2), mat),
        add_box("bottom", (dx - 2 * wall, dy, wall), (cx, cy, z0 + wall / 2), mat),
        add_box("ctop", (dx - 2 * wall, dy, wall), (cx, cy, z1 - wall / 2), mat),
        add_box(
            "back",
            (dx - 2 * wall, wall, z1 - z0 - 2 * wall),
            (cx, y_back - wall / 2, (z0 + z1) / 2),
            mat,
        ),
    ]
    for p in parts:
        uv_cube_project(p)
    return join_parts(parts, name, (cx, cy, z0))


def build_drawer(
    name, x0, x1, y_front, depth, z0, z1, mat, handle_mat, wall=0.012, front_t=0.018, handle_w=0.22
):
    """Overlay front + open-top box + handle bar, joined; origin at front
    center so the node slides cleanly along Y."""
    cx = (x0 + x1) / 2
    h = z1 - z0
    parts = [
        add_box(
            "front",
            (x1 - x0 - 0.006, front_t, h - 0.006),
            (cx, y_front - front_t / 2, (z0 + z1) / 2),
            mat,
        )
    ]
    bx0, bx1 = x0 + 0.025, x1 - 0.025
    by0, by1 = y_front, y_front + depth
    bz0, bz1 = z0 + 0.012, z1 - 0.030
    bcx = (bx0 + bx1) / 2
    parts += [
        add_box(
            "d_bottom", (bx1 - bx0, by1 - by0, wall), (bcx, (by0 + by1) / 2, bz0 + wall / 2), mat
        ),
        add_box(
            "d_side_l",
            (wall, by1 - by0, bz1 - bz0),
            (bx0 + wall / 2, (by0 + by1) / 2, (bz0 + bz1) / 2),
            mat,
        ),
        add_box(
            "d_side_r",
            (wall, by1 - by0, bz1 - bz0),
            (bx1 - wall / 2, (by0 + by1) / 2, (bz0 + bz1) / 2),
            mat,
        ),
        add_box(
            "d_back",
            (bx1 - bx0 - 2 * wall, wall, bz1 - bz0),
            (bcx, by1 - wall / 2, (bz0 + bz1) / 2),
            mat,
        ),
    ]
    for p in parts:
        uv_cube_project(p, size=0.8)
    parts.append(
        add_box(
            "handle",
            (handle_w, 0.014, 0.028),
            (cx, y_front - front_t - 0.005, z1 - 0.035),
            handle_mat,
        )
    )
    return join_parts(parts, name, (cx, y_front, (z0 + z1) / 2))


# ---------------------------------------------------------------- export


def export_glb(path):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        export_extras=True,  # keeps articulation metadata
        export_animations=True,
        export_yup=True,
    )
    print("EXPORTED:", path)

# ==== OBJECT DEFINITION — edit below to change the object ====
WIDTH = 1.79
HEIGHT = 2.46
FRAME_DEPTH = 0.10
TRIM_WIDTH = 0.062
JAMB_WIDTH = 0.070
LEAF_THICKNESS = 0.040
REVEAL = 0.005
THRESHOLD_HEIGHT = 0.015
BOTTOM_GAP = 0.012
KNOB_HEIGHT = 1.31
KNOB_BACKSET = 0.105
OPEN_ANGLE = 90.0
EDGE_RADIUS = 0.0015
HINGE_Z = (0.25, 1.23, 2.21)

scene = reset_scene()
scene.frame_end = 111
PAINT = make_pbr_material('Warm_Beige_Painted_Wood',
    os.path.join(TEXDIR,'paint_diff.png'), os.path.join(TEXDIR,'paint_rough.png'),
    os.path.join(TEXDIR,'paint_normal.png'), normal_strength=0.22)
METAL = make_plain_material('Satin_Nickel', (0.43,0.405,0.335), metallic=0.88, roughness=0.27)
SILL = make_plain_material('Dark_Anodized_Aluminium', (0.045,0.043,0.038), metallic=0.65, roughness=0.34)
SLOT = make_plain_material('Lock_Recess', (0.018,0.016,0.012), metallic=0.3, roughness=0.48)

static=[]
moving=[]
def box(name,dims,loc,mat=PAINT,r=EDGE_RADIUS):
    ob=add_rounded_box(name,dims,loc,mat,radius=r,segments=3)
    uv_cube_project(ob, size=1.0)
    return ob

def prism(name,poly,y0,y1,mat=PAINT):
    # A mitred architrave segment; coordinates are X/Z, depth is along Y.
    n=len(poly)
    verts=[(x,y,z) for y in (y0,y1) for x,z in poly]
    faces=[tuple(range(n-1,-1,-1)),tuple(range(n,2*n))]
    faces += [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    mesh=bpy.data.meshes.new(name)
    mesh.from_pydata(verts,[],faces); mesh.update()
    ob=bpy.data.objects.new(name,mesh); bpy.context.collection.objects.link(ob)
    ob.data.materials.append(mat)
    # Recompute all outward normals, regardless of polygon winding.
    bm=bmesh.new(); bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm,faces=bm.faces[:]); bm.to_mesh(mesh); bm.free()
    bevel(ob,width=.00065,segments=2)
    uv_cube_project(ob)
    return ob

outer=WIDTH/2
opening=outer-JAMB_WIDTH
leaf_x=opening-REVEAL
leaf_bottom=THRESHOLD_HEIGHT+BOTTOM_GAP
leaf_top=HEIGHT-JAMB_WIDTH-REVEAL
leaf_y=0.002
front=leaf_y-LEAF_THICKNESS/2
hinge=(leaf_x+0.003,front-0.009,HEIGHT/2)

# Door lining: open portal, not a panel across the opening.
for side in (-1,1):
    static.append(box('Lining_Left' if side<0 else 'Lining_Right',
        (JAMB_WIDTH,FRAME_DEPTH-.020,HEIGHT-JAMB_WIDTH),
        (side*(outer-JAMB_WIDTH/2),.010,(HEIGHT-JAMB_WIDTH)/2)))
static.append(box('Lining_Header',(WIDTH,FRAME_DEPTH-.020,JAMB_WIDTH),
    (0,.010,HEIGHT-JAMB_WIDTH/2)))
# Front architrave with real diagonal mitre seams.
t=TRIM_WIDTH
static.append(prism('Architrave_Left',[(-outer,0),(-outer+t,0),(-outer+t,HEIGHT-t),(-outer,HEIGHT)],-.05,-.031))
static.append(prism('Architrave_Right',[(outer-t,0),(outer,0),(outer,HEIGHT),(outer-t,HEIGHT-t)],-.05,-.031))
static.append(prism('Architrave_Header',[(-outer,HEIGHT),(outer,HEIGHT),(outer-t,HEIGHT-t),(-outer+t,HEIGHT-t)],-.05,-.031))
# Narrow recessed bevel/rebate behind trim, visible as a fine reveal.
for side in (-1,1):
    static.append(box('Rebate_'+str(side),(.008,.012,HEIGHT-JAMB_WIDTH),
        (side*(opening+.004),-.025,(HEIGHT-JAMB_WIDTH)/2),r=.0006))
static.append(box('Header_Rebate',(2*opening,.012,.008),(0,-.025,HEIGHT-JAMB_WIDTH+.004),r=.0006))
# Stop is behind the closed leaf, so the door opens outwards without crossing it.
for side in (-1,1):
    static.append(box('Back_Stop_'+str(side),(.012,.014,leaf_top-leaf_bottom),
        (side*(opening-.004),.034,(leaf_top+leaf_bottom)/2),r=.0007))
static.append(box('Back_Stop_Header',(2*opening,.014,.012),(0,.034,leaf_top+.001),r=.0007))
static.append(box('Threshold',(2*opening,FRAME_DEPTH,THRESHOLD_HEIGHT),
    (0,0,THRESHOLD_HEIGHT/2),SILL,r=.0012))
for yy in (-.032,-.020,.020,.032):
    static.append(box('Threshold_Groove',(2*opening-.008,.0015,.0007),
        (0,yy,THRESHOLD_HEIGHT+.0002),SLOT,r=.0002))

moving.append(box('Flush_Leaf',(2*leaf_x,LEAF_THICKNESS,leaf_top-leaf_bottom),
    (0,leaf_y,(leaf_top+leaf_bottom)/2),r=.0018))
knob_x=-leaf_x+KNOB_BACKSET
# Rounded spun rose + neck + domed knob, mirrored to the reverse side.
for side in (-1,1):
    face_y=leaf_y+side*LEAF_THICKNESS/2
    profile=[(0,0),(.036,0),(.039,.0015),(.039,.004),(.036,.007),
             (.022,.009),(.014,.012),(.014,.017),(.021,.019),(.026,.024),
             (.027,.030),(.024,.037),(.018,.042),(0,.044)]
    ob=add_lathe('Knob_Front' if side<0 else 'Knob_Back',profile,(knob_x,face_y,KNOB_HEIGHT),METAL,segments=64)
    ob.rotation_euler[0]=math.pi/2 if side<0 else -math.pi/2
    moving.append(ob)
    # Inset lock cylinder face and the vertical keyway, visible on the front.
    if side<0:
        ob=add_cylinder('Lock_Cylinder',.011,.0012,(knob_x,face_y-.0436,KNOB_HEIGHT),METAL,rot=(math.pi/2,0,0),vertices=48)
        moving.append(ob)
        moving.append(box('Keyway',(.0022,.0008,.011),(knob_x,face_y-.0445,KNOB_HEIGHT),SLOT,r=.0006))
# Three compact butt hinges. Static and moving knuckles alternate on the same axis.
for num,z in enumerate(HINGE_Z,1):
    static.append(box('Hinge_Jamb_Plate_'+str(num),(.021,.002,.086),
        (hinge[0]+.010,hinge[1]+.006,z),METAL,r=.0007))
    moving.append(box('Hinge_Leaf_Plate_'+str(num),(.022,.002,.086),
        (hinge[0]-.011,front-.001,z),METAL,r=.0007))
    for k in range(5):
        ob=add_cylinder('Hinge_%d_Knuckle_%d'%(num,k),.0045,.0168,
            (hinge[0],hinge[1],z+(k-2)*.0174),METAL,vertices=32)
        bevel(ob,width=.00035,segments=2)
        (static if k%2==0 else moving).append(ob)
    # Flush screw heads with a subtle straight driver slot.
    for zz in (z-.026,z+.026):
        for side,group,yy in ((1,static,hinge[1]+.0045),(-1,moving,front-.0022)):
            x=hinge[0]+side*.013
            group.append(add_cylinder('Hinge_Screw',.0027,.001,(x,yy,zz),METAL,rot=(math.pi/2,0,0),vertices=24))
            group.append(box('Screw_Slot',(.0035,.0003,.00065),(x,yy-.00055,zz),SLOT,r=.0001))
# Latch and strike are thin surface hardware, kept within the edge clearances.
moving.append(box('Latch_Faceplate',(.0015,.026,.065),(-leaf_x-.0005,leaf_y,KNOB_HEIGHT),METAL,r=.0005))
static.append(box('Strike_Plate',(.0015,.030,.070),(-opening+.0005,leaf_y,KNOB_HEIGHT),METAL,r=.0005))

frame=join_parts(static,'Door_Frame',(0,0,0))
frame['category']='door'
frame['static']=True
frame['description']='Fixed lining, mitred architrave, threshold and stationary hinge knuckles'
frame['nominal_dimensions_m']=[WIDTH,FRAME_DEPTH,HEIGHT]
leaf=join_parts(moving,'Door_Leaf_Right_Hinge',hinge)
parent_keep_world(leaf,frame)
set_articulation(leaf,'revolute',(0,0,1),0,math.radians(OPEN_ANGLE),origin=hinge)
leaf['description']='Single flush leaf with left round keyed knob; outward right-hand swing'
leaf['coordinate_frame']='Blender Z-up; front -Y; extras axis and origin in Blender coordinates'
animate_revolute(scene,[(leaf,math.radians(OPEN_ANGLE))],open_frames=30,hold=40)
scene.frame_set(1)
bpy.context.view_layer.update()
# Build-level canonical and mechanical assertions before export.
pts=[ob.matrix_world@v.co for ob in (frame,leaf) for v in ob.data.vertices]
mins=[min(p[i] for p in pts) for i in range(3)]
maxs=[max(p[i] for p in pts) for i in range(3)]
assert abs(mins[2])<1e-5 and abs(maxs[2]-HEIGHT)<1e-5
assert abs(maxs[0]-mins[0]-WIDTH)<1e-5
assert front<0 and hinge[0]>0
p=mathutils.Vector((-leaf_x-hinge[0],0,0))
assert (mathutils.Matrix.Rotation(math.radians(OPEN_ANGLE),4,'Z')@p).y<0
print('CLOSED BUILD BBOX:',mins,maxs)
export_glb(OUT_GLB)
