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
