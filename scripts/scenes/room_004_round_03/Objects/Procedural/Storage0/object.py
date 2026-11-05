"""Storage0: shallow honey-oak storage with two unequal hinged leaves and four linen inlays.

SELF-CONTAINED — the Blender helper library is inlined below, so this file
defines the object on its own (no external imports / sys.path hacks).

Run:
  blender -b --python object.py                       # uses ./textures, writes ./Storage0.glb
  blender -b --python object.py -- <texdir> <out.glb>
"""

import bpy, bmesh, math, mathutils, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
_argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
TEXDIR = _argv[0] if _argv else os.path.join(HERE, 'textures')
OUT_GLB = _argv[1] if len(_argv) > 1 else os.path.join(HERE, 'Storage0.glb')

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
# Scan dimensions are permuted to upright furniture: width, depth, height.
WIDTH = 1.68
HEIGHT = 2.51
DEPTH = 0.07
FRAME_WIDTH = 0.022
PLINTH_HEIGHT = 0.018
BACK_THICKNESS = 0.006
DOOR_THICKNESS = 0.018
DOOR_GAP = 0.006
SPLIT_FRACTION = 0.615
EDGE_RADIUS = 0.0015
TRIM_WIDTH = 0.009
OPEN_LIMIT_DEG = 100.0
PREVIEW_OPEN_DEG = 90.0
# Positions as fractions of the full reference frontage.
INLAYS = [(0.108, 0.192), (0.445, 0.558), (0.640, 0.690), (0.853, 0.943)]

scene = reset_scene()
WOOD = make_pbr_material('Honey_Oak_Veneer',
    os.path.join(TEXDIR,'honey_oak_diff.jpg'),
    os.path.join(TEXDIR,'honey_oak_rough.jpg'),
    os.path.join(TEXDIR,'oak_veneer_01_nor_gl_1k.png'), normal_strength=0.12)
LINEN = make_pbr_material('Warm_Gray_Linen_Laminate',
    os.path.join(TEXDIR,'gray_linen_diff.jpg'),
    os.path.join(TEXDIR,'gray_linen_rough.jpg'),
    os.path.join(TEXDIR,'gray_linen_normal.png'), normal_strength=0.20)
METAL = make_plain_material('Brushed_Dark_Aluminum', (0.16,0.17,0.16), metallic=0.75, roughness=0.36)

# Wood UVs stay vertical on the facade; all textured components are UV projected before joining.
def box(name, dims, loc, mat, radius=EDGE_RADIUS):
    ob=add_rounded_box(name,dims,loc,mat,radius=radius,segments=3)
    if mat in (WOOD,LINEN):
        uv_cube_project(ob)
        # Explicit physical projection avoids aspect distortion and rotated grain on the face.
        for face in ob.data.polygons:
            for li in face.loop_indices:
                v=ob.data.vertices[ob.data.loops[li].vertex_index].co + ob.location
                if abs(face.normal.y)>0.5:
                    u,vv=(v.x+WIDTH/2)/1.15, v.z/2.51
                elif abs(face.normal.x)>0.5:
                    u,vv=v.y/1.15, v.z/2.51
                else:
                    u,vv=(v.x+WIDTH/2)/1.15, v.y/2.51
                if mat==LINEN: u,vv=u*5.0,vv*5.0
                ob.data.uv_layers.active.data[li].uv=(u,vv)
    return ob

# Open-front shallow carcass: no full-volume box, so open doors reveal real empty space.
body_parts=[]
for sign,label in [(-1,'Left'),(1,'Right')]:
    body_parts.append(box('Frame_'+label,(FRAME_WIDTH,DEPTH,HEIGHT),
        (sign*(WIDTH-FRAME_WIDTH)/2,0,HEIGHT/2),WOOD))
body_parts += [
    box('Top_Header',(WIDTH-2*FRAME_WIDTH,DEPTH,0.020),(0,0,HEIGHT-0.010),WOOD),
    box('Recessed_Toe_Plinth',(WIDTH-2*FRAME_WIDTH,DEPTH-0.010,PLINTH_HEIGHT),
        (0,0.005,PLINTH_HEIGHT/2),WOOD),
    box('Recessed_Back',(WIDTH-2*FRAME_WIDTH,BACK_THICKNESS,HEIGHT-0.020-PLINTH_HEIGHT),
        (0,DEPTH/2-BACK_THICKNESS/2,(HEIGHT-0.020+PLINTH_HEIGHT)/2),WOOD,0.0007),
    box('Dark_Top_Cap',(WIDTH,0.009,0.012),(0,-DEPTH/2+0.0045,HEIGHT-0.006),METAL,0.001),
]
body=join_parts(body_parts,'Storage_Carcass',(0,0,0))
body['category']='storage'
body['front_direction']='-Y'
body['dimension_interpretation']='scan 1.68 x 2.51 x 0.07 mapped to upright X width / Z height / Y depth'
body['reference_layout']='two unequal doors; four full-height gray textile inlays; no drawers'

z0,z1=PLINTH_HEIGHT+DOOR_GAP,HEIGHT-0.020-DOOR_GAP
x0,x1=-WIDTH/2+FRAME_WIDTH+DOOR_GAP/2, WIDTH/2-FRAME_WIDTH-DOOR_GAP/2
split=-WIDTH/2+WIDTH*SPLIT_FRACTION
face_y=-DEPTH/2+0.007
hinge_y=-DEPTH/2+0.003
leaf_defs=[('Door_Left',x0,split-DOOR_GAP/2,-1,INLAYS[:2]),
           ('Door_Right',split+DOOR_GAP/2,x1,1,INLAYS[2:])]
doors=[]
for name,left,right,sign,inlays in leaf_defs:
    hinge_x=left if sign==-1 else right
    pivot=(hinge_x,hinge_y,(z0+z1)/2)
    parts=[box(name+'_Oak_Leaf',(right-left,DOOR_THICKNESS,z1-z0),
               ((left+right)/2,face_y+DOOR_THICKNESS/2,(z0+z1)/2),WOOD)]
    for i,(f0,f1) in enumerate(inlays,1):
        a,b=-WIDTH/2+WIDTH*f0,-WIDTH/2+WIDTH*f1
        parts.append(box(name+'_Linen_'+str(i),(b-a,0.002,z1-z0-0.002),
            ((a+b)/2,face_y-0.001,(z0+z1)/2),LINEN,0.0005))
        for suffix,x in [('L',a-TRIM_WIDTH/2),('R',b+TRIM_WIDTH/2)]:
            parts.append(box(name+'_Inlay_Trim_'+str(i)+suffix,(TRIM_WIDTH,0.004,z1-z0),
                (x,face_y-0.002,(z0+z1)/2),METAL,0.0006))
    # Slim full-height edge channels are the visible pulls; no invented external knobs.
    latch=right-0.004 if sign==-1 else left+0.004
    parts.append(box(name+'_Edge_Pull',(0.008,0.006,z1-z0),
                     (latch,face_y-0.003,(z0+z1)/2),METAL,0.001))
    parts.append(box(name+'_Hinge_Edge',(0.006,0.004,z1-z0),
                     (hinge_x-sign*0.003,face_y-0.002,(z0+z1)/2),METAL,0.0007))
    # Small concealed hinge barrels at three heights; each belongs to the moving leaf.
    for i,z in enumerate([0.20,HEIGHT/2,HEIGHT-0.20]):
        parts.append(add_cylinder(name+'_Hinge_'+str(i),0.003,0.055,(hinge_x,hinge_y,z),METAL))
    ob=join_parts(parts,name,pivot)
    parent_keep_world(ob,body)
    set_articulation(ob,'revolute',axis=(0,0,sign),limit_min=0,
                     limit_max=math.radians(OPEN_LIMIT_DEG),origin=pivot)
    ob['joint_description']='outer vertical edge; outward opening toward -Y'
    doors.append((ob,sign*math.radians(PREVIEW_OPEN_DEG)))

animate_revolute(scene,doors,open_frames=36,stagger=10,hold=45,close_stagger=10)
scene.frame_end=150
scene.frame_set(1)
bpy.context.view_layer.update()
pts=[ob.matrix_world@mathutils.Vector(v) for ob in bpy.data.objects if ob.type=='MESH' for v in ob.bound_box]
mn=[min(v[i] for v in pts) for i in range(3)]
mx=[max(v[i] for v in pts) for i in range(3)]
print('REST BOUNDS',mn,mx,'SIZE',[mx[i]-mn[i] for i in range(3)])
assert abs(mn[2])<1e-5 and abs(mx[2]-HEIGHT)<1e-5
export_glb(OUT_GLB)
