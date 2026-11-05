"""Add explicitly scoped demonstration physics to the exported static room."""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
import mujoco


def nums(a): return ' '.join(f'{float(x):.9g}' for x in a)


def build(source, metadata, output):
    output.mkdir(parents=True, exist_ok=True)
    meta=json.loads(metadata.read_text())
    exported=json.loads((source.parent/'export_report.json').read_text())
    root=ET.parse(source).getroot()
    root.set('model','room003_physics_demo_NOT_calibrated')
    compiler=root.find('compiler')
    compiler.set('meshdir','../room_003_mujoco_v002/meshes')
    compiler.set('texturedir','../room_003_mujoco_v002/textures')
    opt=root.find('option'); opt.set('gravity','0 0 -9.81'); opt.set('timestep','0.0025')
    opt.set('integrator','implicitfast'); opt.set('iterations','80')
    world=root.find('worldbody')
    bodies={o['object']:world.find(f"body[@name='obj_{i}']") for i,o in enumerate(exported['objects'])}
    equality=ET.SubElement(root,'equality'); actuators=ET.SubElement(root,'actuator')
    colliders=[]

    def box(parent,name,pos,size,quat=None,kind='furniture'):
        attrs=dict(name=name,type='box',pos=nums(pos),size=nums(np.maximum(size,.001)),
                   contype='2' if kind=='furniture' else '1',conaffinity='1',
                   group='3',rgba='0.1 0.85 0.8 0.3',friction='.25 .01 .001',
                   solref='.015 1',mass='0')
        if quat is not None: attrs['quat']=nums(quat)
        ET.SubElement(parent,'geom',**attrs); colliders.append({'name':name,'kind':kind})

    floor=meta['Floor0']['world_bbox'][1][2]
    box(world,'collision_floor',[-1.04935,1.93275,floor-.04],[1.56,1.76,.04],kind='floor')
    # Oriented boxes for each real vanity leg, rather than a solid box around the table.
    for name in ['Front_Left_Tapered_Leg','Front_Right_Tapered_Leg','Rear_Left_Tapered_Leg','Rear_Right_Tapered_Leg']:
        m=np.array(meta[name]['matrix']); bounds=np.array(meta[name]['local_bbox'])
        scales=np.linalg.norm(m[:3,:3],axis=0); rotation=m[:3,:3]/scales
        quat=np.zeros(4); mujoco.mju_mat2Quat(quat,rotation.flatten())
        center=m[:3,:3]@bounds.mean(axis=0)+m[:3,3]
        box(world,'collision_'+name,center,(bounds[1]-bounds[0])*scales/2,quat)
    # Conservative boxes for other fixed furniture, explicitly not precise concave collisions.
    for owner in ['Table0','Table1','Storage0','Storage1','Storage2','Storage3']:
        parts=[np.array(o['world_bbox']) for n,o in meta.items() if owner in o['ancestors'] and 'world_bbox' in o and n in bodies]
        bounds=np.array([np.min([b[0] for b in parts],axis=0),np.max([b[1] for b in parts],axis=0)])
        box(world,'collision_'+owner,bounds.mean(axis=0),(bounds[1]-bounds[0])/2)
    # Shell boundaries; these stay fixed like the building fabric.
    for name,obj in meta.items():
        if name in bodies and name.startswith('Wall') and not obj['parent']:
            b=np.array(obj['world_bbox']); half=(b[1]-b[0])/2
            if min(half)<.015: half[np.argmin(half)]=.015
            box(world,'collision_'+name,b.mean(axis=0),half)

    # Reparent all rendered stool parts together under one free body, preserving poses.
    stool_names=[n for n,o in meta.items() if 'Chair2' in o['ancestors'] and n in bodies]
    allbounds=np.array([meta[n]['world_bbox'] for n in stool_names])
    bounds=np.array([allbounds[:,0].min(axis=0),allbounds[:,1].max(axis=0)])
    center=bounds.mean(axis=0)
    stool=ET.SubElement(world,'body',name='demo_stool',pos=nums(center))
    ET.SubElement(stool,'freejoint',name='stool_free')
    ET.SubElement(stool,'inertial',pos='0 0 0',mass='4',diaginertia='.13 .13 .12')
    for name in stool_names:
        body=bodies[name]; world.remove(body)
        body.set('pos',nums(np.fromstring(body.get('pos'),sep=' ')-center)); stool.append(body)
    half=(bounds[1]-bounds[0])/2
    box(stool,'stool_seat',[0,0,half[2]-.035],[half[0],half[1],.035],kind='stool')
    legheight=(2*half[2]-.07)/2
    for i,x in enumerate([-half[0]+.035,half[0]-.035]):
        for j,y in enumerate([-half[1]+.035,half[1]-.035]):
            box(stool,f'stool_leg_{i}_{j}',[x,y,-half[2]+legheight],[.024,.024,legheight],kind='stool')
    for geom in stool.findall('geom'): geom.set('conaffinity','3')

    drawers=[]
    for idx,name in enumerate(['Drawer_Left_Upper','Drawer_Left_Lower']):
        original=bodies[name]; position=np.fromstring(original.get('pos'),sep=' ')
        motion=ET.SubElement(world,'body',name=f'drawer_body_{idx}',pos=nums(position))
        m=np.array(meta[name]['matrix']); properties=meta[name]['properties']
        axis=m[:3,:3]@np.array(properties['articulation_axis'])
        scale=float(np.linalg.norm(axis)); axis/=scale
        limit=float(properties['limit_max'])*scale
        joint=f'drawer_slide_{idx}'
        ET.SubElement(motion,'joint',name=joint,type='slide',axis=nums(axis),range=f'0 {limit}',limited='true',damping='4',frictionloss='.3')
        ET.SubElement(motion,'inertial',pos='0 0 0',mass='2',diaginertia='.025 .045 .06')
        for part in [n for n,o in meta.items() if (n==name or name in o['ancestors']) and n in bodies]:
            body=bodies[part]; world.remove(body)
            body.set('pos',nums(np.fromstring(body.get('pos'),sep=' ')-position)); motion.append(body)
        ET.SubElement(actuators,'position',name=f'drawer_motor_{idx}',joint=joint,kp='350',kv='45',ctrlrange=f'0 {limit}',ctrllimited='true',forcerange='-70 70',forcelimited='true')
        ET.SubElement(equality,'joint',name=f'drawer_lock_{idx}',joint1=joint,polycoef='0 0 0 0 0',active='true',solref='.002 1')
        drawers.append(dict(object=name,joint=joint,axis=axis.tolist(),range_m=[0,limit]))
    ET.SubElement(world,'camera',name='walkthrough',pos='-1.1 1 .05',quat='1 0 0 0',fovy='65')
    ET.indent(root)
    ET.ElementTree(root).write(output/'scene.xml',encoding='utf-8',xml_declaration=True)
    report=dict(source=str(source),floor_z=floor,stool_initial=center.tolist(),stool_half=half.tolist(),
                colliders=colliders,drawers=drawers,stool_mass_kg=4,drawer_mass_kg=2,
                friction=.25,calibrated=False,
                limits=['Only the stool is freely dynamic; other furniture is fixed.',
                        'Vanity drawer axes/ranges come from authored metadata, not measured mechanics.',
                        'Drawer joints have limits and locks; drawer-carcass collision geometry is not implemented.',
                        'Seat/legs and furniture colliders are approximations; large fixed furniture uses conservative boxes.',
                        'Camera motion is scripted, not collision-constrained robot navigation.'])
    (output/'physics_report.json').write_text(json.dumps(report,indent=2))
    print('PHYSICS_MODEL_BUILT',flush=True)


if __name__=='__main__': build(*map(Path,sys.argv[1:]))
