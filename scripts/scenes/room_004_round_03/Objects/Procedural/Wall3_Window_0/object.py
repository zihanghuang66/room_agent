"""Window with a fixed picture light, two inward-opening side casements,
low fixed sidelights, a safety rail, marble sill, and gathered curtains.
Dimensions from room scan; construction and hinge positions inferred from image.

SELF-CONTAINED — the Blender helper library is inlined below, so this file
defines the object on its own (no external imports / sys.path hacks).

Run:
  blender -b --python object.py                       # uses ./textures, writes ./Wall3_Window_0.glb
  blender -b --python object.py -- <texdir> <out.glb>
"""

import bpy, bmesh, math, mathutils, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
_argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
TEXDIR = _argv[0] if _argv else os.path.join(HERE, 'textures')
OUT_GLB = _argv[1] if len(_argv) > 1 else os.path.join(HERE, 'Wall3_Window_0.glb')

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
# Editable dimensions, in metres; front is -Y.
WIDTH = 2.68
HEIGHT = 1.81
DEPTH = 0.10
SURROUND = 0.075
SILL_H = 0.055
CENTER_HALF = 0.765
FRAME_FACE = 0.030
SASH_FACE = 0.023
SASH_BOTTOM = 0.56
SASH_TOP = 1.66
SIDE_BAR_Z = 1.195
RAIL_TOP = 0.965
RAIL_RODS = 14
CURTAIN_WIDTH = 0.233
SASH_OUTER = 0.946
CURTAIN_FOLDS = 8
OPEN_ANGLE = math.radians(60)
scene = reset_scene()
scene.frame_end=122

# PBR textures are baked images, with no non-exportable colour operations.
def pbr(name,prefix,strength):
    return make_pbr_material(name,os.path.join(TEXDIR,prefix+'_diff.png'),
        os.path.join(TEXDIR,prefix+'_rough.png'),os.path.join(TEXDIR,prefix+'_normal.png'),
        normal_strength=strength)
STONE=pbr('Pale_Marble_Sill','marble',.15)
LINEN=pbr('Beige_Linen_Curtain','linen',.30)
SHEER=pbr('Ivory_Sheer_Weave','sheer',.18)
SHEER.node_tree.nodes.get('Principled BSDF').inputs['Transmission Weight'].default_value=.20
METAL=make_plain_material('Charcoal_Powdercoated_Aluminium',(.026,.029,.028),.45,.32)
RAIL=make_plain_material('Charcoal_Painted_Steel',(.022,.025,.025),.55,.32)
PLASTER=make_plain_material('Warm_Offwhite_Plaster',(.72,.69,.63),0,.78)
SEAL=make_plain_material('Black_Rubber_Gasket',(.009,.011,.010),0,.70)
GLASS=make_plain_material('Clear_Glazing',(.90,.93,.96),0,.025)
g=GLASS.node_tree.nodes.get('Principled BSDF')
g.inputs['Transmission Weight'].default_value=1.0
g.inputs['IOR'].default_value=1.45

fixed=[]
def box(name,dims,loc,mat,group=None,r=.0015):
    ob=add_rounded_box(name,dims,loc,mat,radius=r,segments=3)
    uv_cube_project(ob,size=1.0)
    if group is not None:group.append(ob)
    return ob

def frame(name,x0,x1,z0,z1,y,depth,face,mat,group):
    # Stiles and rails meet without overlapping their volumes.
    box(name+'_left',(face,depth,z1-z0),(x0+face/2,y,(z0+z1)/2),mat,group)
    box(name+'_right',(face,depth,z1-z0),(x1-face/2,y,(z0+z1)/2),mat,group)
    box(name+'_top',(x1-x0-2*face,depth,face),((x0+x1)/2,y,z1-face/2),mat,group)
    box(name+'_bottom',(x1-x0-2*face,depth,face),((x0+x1)/2,y,z0+face/2),mat,group)

# Pale reveal and slightly projecting marble sill; exact resting bounding box.
box('Marble_Sill',(WIDTH,DEPTH,SILL_H),(0,0,SILL_H/2),STONE,fixed,r=.002)
box('Reveal_Left',(SURROUND,.09,HEIGHT-SILL_H),(-WIDTH/2+SURROUND/2,.005,(HEIGHT+SILL_H)/2),PLASTER,fixed,r=.002)
box('Reveal_Right',(SURROUND,.09,HEIGHT-SILL_H),(WIDTH/2-SURROUND/2,.005,(HEIGHT+SILL_H)/2),PLASTER,fixed,r=.002)
box('Reveal_Head',(WIDTH-2*SURROUND,.09,SURROUND),(0,.005,HEIGHT-SURROUND/2),PLASTER,fixed,r=.002)
# Fine inset step just beneath the upper reveal, like the recessed track in the photo.
box('Curtain_Track',(WIDTH-2*SURROUND,.023,.013),(0,.023,1.703),METAL,fixed)
XOUT=WIDTH/2-SURROUND
ZLO=.105
ZHI=1.675
frame('Outer_Window_Frame',-XOUT,XOUT,ZLO,ZHI,.023,.034,FRAME_FACE,METAL,fixed)
for side in [-1,1]:
    box('Structural_Mullion',(FRAME_FACE,.045,ZHI-ZLO-2*FRAME_FACE),
        (side*CENTER_HALF,.019,(ZHI+ZLO)/2),METAL,fixed)
# Broad fixed picture light: one continuous pane behind the guardrail.
frame('Central_Glazing_Bead',-CENTER_HALF+FRAME_FACE/2,CENTER_HALF-FRAME_FACE/2,
      ZLO+FRAME_FACE,ZHI-FRAME_FACE,.003,.012,.009,SEAL,fixed)
box('Central_Fixed_Glass',(2*CENTER_HALF-.052,.004,ZHI-ZLO-2*FRAME_FACE-.024),
    (0,.014,(ZHI+ZLO)/2),GLASS,fixed,r=.0002)

movers=[]
for side,label in [(-1,'Left'),(1,'Right')]:
    x0,x1=((-XOUT+FRAME_FACE,-CENTER_HALF-FRAME_FACE/2) if side<0 else
           (CENTER_HALF+FRAME_FACE/2,XOUT-FRAME_FACE))
    # Static bottom light and the horizontal transom supporting the upper casement.
    box(label+'_Transom',(x1-x0,.034,.032),((x0+x1)/2,.023,SASH_BOTTOM-.025),METAL,fixed)
    frame(label+'_Lower_Fixed_Bead',x0,x1,ZLO+FRAME_FACE,SASH_BOTTOM-.041,
          .009,.012,.010,SEAL,fixed)
    box(label+'_Lower_Fixed_Glass',(x1-x0-.02,.004,SASH_BOTTOM-.041-ZLO-FRAME_FACE-.02),
        ((x0+x1)/2,.014,(ZLO+FRAME_FACE+SASH_BOTTOM-.041)/2),GLASS,fixed,r=.0002)
    pieces=[]
    # The concealed side area stays fixed; only the exposed narrow ventilator opens.
    # The photograph does not establish hidden widths behind the gathered curtains.
    sx0,sx1=((-SASH_OUTER,x1-.004) if side<0 else (x0+.004,SASH_OUTER))
    hidden0,hidden1=((x0,sx0-.004) if side<0 else (sx1+.004,x1))
    box(label+'_Concealed_Fixed_Glass',(hidden1-hidden0,.004,SASH_TOP-SASH_BOTTOM),
        ((hidden0+hidden1)/2,.014,(SASH_TOP+SASH_BOTTOM)/2),GLASS,fixed,r=.0002)
    box(label+'_Concealed_Jamb',(.016,.025,SASH_TOP-SASH_BOTTOM),
        (side*(SASH_OUTER+.012),.017,(SASH_TOP+SASH_BOTTOM)/2),METAL,fixed)
    # Hinge line on the inner vertical stile, as indicated by the visible barrels.
    pivot_x=sx1 if side<0 else sx0
    pivot=(pivot_x,-.027,SASH_BOTTOM)
    frame(label+'_Sash',sx0,sx1,SASH_BOTTOM,SASH_TOP,-.013,.023,SASH_FACE,METAL,pieces)
    # Upper narrow lights have a horizontal glazing bar, unlike the broad center pane.
    box(label+'_Sash_Glazing_Bar',(sx1-sx0-2*SASH_FACE,.019,.021),
        ((sx0+sx1)/2,-.013,SIDE_BAR_Z),METAL,pieces)
    for i,(a,b) in enumerate([(SASH_BOTTOM+SASH_FACE,SIDE_BAR_Z-.0105),
                              (SIDE_BAR_Z+.0105,SASH_TOP-SASH_FACE)]):
        frame(label+'_Sash_Gasket'+str(i),sx0+SASH_FACE,sx1-SASH_FACE,a,b,
            -.008,.010,.005,SEAL,pieces)
        box(label+'_Sash_Glass'+str(i),(sx1-sx0-2*SASH_FACE-.010,.004,b-a-.010),
            ((sx0+sx1)/2,-.006,(a+b)/2),GLASS,pieces,r=.0002)
    # Two rounded hinge barrels on each inner edge.
    for z in [.69,1.53]:
        cyl=add_cylinder(label+'_Hinge_Barrel',.006,.071,(pivot_x,-.027,z),METAL,vertices=20)
        bevel(cyl,.001,2);pieces.append(cyl)
    # Compact offset window handle, visible near the inner stile in the reference.
    hx=pivot_x+side*.026
    box(label+'_Handle_Rosette',(.020,.008,.052),(hx,-.029,1.095),METAL,pieces,r=.004)
    box(label+'_Handle_Neck',(.010,.013,.010),(hx,-.037,1.108),METAL,pieces,r=.003)
    box(label+'_Handle_Elbow',(.030,.010,.011),(hx+side*.010,-.043,1.108),METAL,pieces,r=.004)
    box(label+'_Handle_Grip',(.011,.012,.068),(hx+side*.024,-.043,1.080),METAL,pieces,r=.004)
    sash=join_parts(pieces,'Casement_'+label,pivot)
    # Left extends -X and rotates +Z; right extends +X and rotates -Z.
    axis=(0,0,-side)
    set_articulation(sash,'revolute',axis,0,OPEN_ANGLE,origin=pivot)
    sash['mechanism']='inward side-hung casement; inferred inner-edge hinge'
    movers.append((sash,-side*OPEN_ANGLE))

# Slim fixed guard on the far side of the glazing: does not block inward casements.
box('Safety_Top_Rail',(2*XOUT-.025,.012,.019),(0,.040,RAIL_TOP),RAIL,fixed,r=.002)
box('Safety_Bottom_Rail',(2*XOUT-.025,.012,.015),(0,.040,ZLO+.019),RAIL,fixed,r=.0015)
for i in range(RAIL_RODS):
    x=-.666+i*(1.332/(RAIL_RODS-1))
    box('Safety_Picket_%02d'%i,(.010,.010,RAIL_TOP-ZLO-.035),
        (x,.040,(RAIL_TOP+ZLO+.009)/2),RAIL,fixed,r=.001)
for side in [-1,1]:
    for x in [.89,1.01,1.13]:
        box('Side_Safety_Picket',(.010,.010,RAIL_TOP-ZLO-.035),
            (side*x,.040,(RAIL_TOP+ZLO+.009)/2),RAIL,fixed,r=.001)

# Double-sided pleated fabric meshes with closed thin hems, gently varying folds.
def curtain(name,x0,x1,z0,z1,folds,mat,ybase,amp,thickness):
    nx,nz=folds*20,28
    verts=[];faces=[]
    for j in range(nz+1):
        v=j/nz;z=z0+(z1-z0)*v
        for i in range(nx+1):
            u=i/nx
            phase=u*folds*2*math.pi+.12*math.sin(v*math.pi*1.7)
            # Slightly pinched header and soft undulating lower hem.
            y=ybase+amp*(.85+.15*(1-v))*math.cos(phase)+amp*.17*math.cos(2*phase)
            zz=z+(.0025*math.cos(phase))*(1-v)**10
            verts.append((x0+(x1-x0)*u,y,zz))
    for j in range(nz):
        for i in range(nx):
            a=j*(nx+1)+i
            faces.append((a,a+1,a+nx+2,a+nx+1))
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],faces);mesh.update()
    ob=bpy.data.objects.new(name,mesh);bpy.context.collection.objects.link(ob)
    ob.data.materials.append(mat)
    uv=mesh.uv_layers.new(name='FabricUV')
    for poly in mesh.polygons:
        poly.use_smooth=True
        for li in poly.loop_indices:
            co=mesh.vertices[mesh.loops[li].vertex_index].co
            uv.data[li].uv=((co.x-x0)*2,(co.z-z0)*2)
    bpy.context.view_layer.objects.active=ob
    solid=ob.modifiers.new('Woven_fabric_thickness','SOLIDIFY');solid.thickness=thickness
    bpy.ops.object.modifier_apply(modifier=solid.name)
    fixed.append(ob)
for side,label in [(-1,'Left'),(1,'Right')]:
    a,b=(1.245-CURTAIN_WIDTH,1.245)
    if side<0:a,b=-b,-a
    curtain(label+'_Beige_Pleated_Drape',a,b,.061,1.702,CURTAIN_FOLDS,LINEN,-.026,.014,.0012)
    a,b=(.950,1.035)
    if side<0:a,b=-b,-a
    curtain(label+'_Ivory_Sheer',a,b,.063,1.693,4,SHEER,-.019,.006,.0006)

body=join_parts(fixed,'Window_Static_Frame_Sill_Rail_Curtains',(0,0,0))
body['category']='window'
body['dimensions_m']=[WIDTH,DEPTH,HEIGHT]
body['front_axis']='-Y (Blender Z-up)'
body['reference_notes']='One center picture light, two divided upper side casements, two fixed lower sidelights; safety railing; paired beige curtains with sheers.'
for ob,ang in movers:parent_keep_world(ob,body)
animate_revolute(scene,movers,open_frames=32,stagger=8,hold=40,close_stagger=8)
scene.frame_set(1)
bpy.context.view_layer.update()
# Canonical rest-pose dimensions and bounds, before any export conversion.
pts=[ob.matrix_world @ mathutils.Vector(c) for ob in [body]+[o for o,a in movers] for c in ob.bound_box]
lo=[min(p[i] for p in pts) for i in range(3)];hi=[max(p[i] for p in pts) for i in range(3)]
print('REST BOUNDS',lo,hi,'SIZE',[hi[i]-lo[i] for i in range(3)])
assert abs(lo[2])<1e-5 and abs(hi[2]-HEIGHT)<1e-5
assert abs(hi[0]-lo[0]-WIDTH)<1e-5
assert abs(hi[1]-lo[1]-DEPTH)<1e-5
export_glb(OUT_GLB)
