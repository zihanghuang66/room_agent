"""Real MuJoCo dynamics, numerical checks, and an annotated walkthrough recording."""
import argparse
import json
import math
import subprocess
from pathlib import Path

import mujoco
import numpy as np


class Demo:
    def __init__(self, directory):
        self.directory=directory
        self.report=json.loads((directory/'physics_report.json').read_text())
        self.model=mujoco.MjModel.from_xml_path(str(directory/'scene.xml'))
        self.data=mujoco.MjData(self.model)
        self.sid=self.model.body('demo_stool').id
        j=self.model.joint('stool_free').id
        self.qadr=int(self.model.jnt_qposadr[j]); self.vadr=int(self.model.jnt_dofadr[j])
        self.furniture=[self.model.geom(c['name']).id for c in self.report['colliders'] if c['kind']=='furniture']
        self.drawer_q=[int(self.model.jnt_qposadr[self.model.joint(f'drawer_slide_{i}').id]) for i in range(2)]
        self.reset()

    def reset(self, mode='original'):
        mujoco.mj_resetData(self.model,self.data)
        self.gravity(True); self.collision(True); self.lock(True)
        if mode=='gravity':
            self.data.qpos[self.qadr:self.qadr+3]=[-1.05,2.5,self.report['stool_initial'][2]+.35]
        elif mode=='collision':
            self.data.qpos[self.qadr:self.qadr+3]=[-1.37,2.55,self.report['stool_initial'][2]+.002]
        mujoco.mj_forward(self.model,self.data)

    def gravity(self, enabled):
        self.model.opt.gravity[:]=[0,0,-9.81 if enabled else 0]

    def collision(self, enabled):
        self.model.geom_contype[self.furniture]=2 if enabled else 0
        self.model.geom_conaffinity[self.furniture]=1 if enabled else 0

    def lock(self, enabled):
        self.data.eq_active[:]=enabled

    def step(self, seconds, force=0):
        contacts=0
        self.data.xfrc_applied[self.sid,:]=0
        self.data.xfrc_applied[self.sid,1]=force
        for _ in range(round(seconds/self.model.opt.timestep)):
            mujoco.mj_step(self.model,self.data)
            for c in self.data.contact:
                if c.geom1 in self.furniture or c.geom2 in self.furniture: contacts+=1
        self.data.xfrc_applied[self.sid,:]=0
        assert np.isfinite(self.data.qpos).all() and np.isfinite(self.data.qvel).all()
        assert not any(w.number for w in self.data.warning), 'MuJoCo solver warnings'
        return contacts

    def state(self):
        return dict(gravity=bool(self.model.opt.gravity[2]),furniture_collision=bool(self.model.geom_contype[self.furniture[0]]),
                    drawers_locked=bool(self.data.eq_active[0]),drawer_m=[float(self.data.qpos[i]) for i in self.drawer_q],
                    stool_xyz=self.data.xpos[self.sid].tolist(),contacts=int(self.data.ncon))


def verify(demo):
    out={}
    demo.reset('gravity'); demo.gravity(False)
    z0=demo.data.qpos[demo.qadr+2]; demo.step(1)
    out['gravity_off_drift_m']=float(abs(demo.data.qpos[demo.qadr+2]-z0))
    assert out['gravity_off_drift_m']<1e-5
    demo.gravity(True); demo.step(2)
    out['gravity_on_drop_m']=float(z0-demo.data.qpos[demo.qadr+2])
    out['gravity_on_floor_contacts']=int(demo.data.ncon)
    assert .25<out['gravity_on_drop_m']<.48 and demo.data.ncon>0
    for enabled in (False,True):
        demo.reset('collision'); demo.collision(enabled)
        contacts=demo.step(.8,force=20)
        out['collision_'+str(enabled)]=dict(position=demo.data.xpos[demo.sid].tolist(),furniture_contact_samples=contacts)
    off=out['collision_False']; on=out['collision_True']
    out['collision_travel_difference_m']=off['position'][1]-on['position'][1]
    assert out['collision_travel_difference_m']>.1 and on['furniture_contact_samples']>0 and off['furniture_contact_samples']==0
    demo.reset(); demo.data.ctrl[:]=.14; demo.step(2)
    out['locked_drawer_m']=demo.state()['drawer_m']
    assert max(abs(x) for x in out['locked_drawer_m'])<.003
    demo.lock(False); demo.step(2)
    out['unlocked_drawer_m']=demo.state()['drawer_m']
    assert all(.13<x<.15 for x in out['unlocked_drawer_m'])
    demo.data.ctrl[:]=0; demo.step(2)
    out['closed_drawer_m']=demo.state()['drawer_m']
    assert max(abs(x) for x in out['closed_drawer_m'])<.003
    out['passed']=True; out['mujoco']=mujoco.__version__
    (demo.directory/'physics_checks.json').write_text(json.dumps(out,indent=2))
    print(json.dumps(out,indent=2),flush=True)
    return out


def look_at(model, position, target):
    p=np.array(position,dtype=float); back=p-np.array(target); back/=np.linalg.norm(back)
    right=np.cross([0,0,1],back); right/=np.linalg.norm(right); up=np.cross(back,right)
    q=np.zeros(4); mujoco.mju_mat2Quat(q,np.column_stack([right,up,back]).flatten())
    i=model.camera('walkthrough').id
    model.cam_pos[i]=p; model.cam_quat[i]=q


STAGES=[(0,18,'walk','01 房间漫游'),(18,23,'gravity_off','02 重力关闭：凳子悬停'),
        (23,29,'gravity_on','03 重力开启：凳子落地'),
        (29,35,'collision_off','04 家具碰撞关闭：凳子穿过桌腿'),
        (35,41,'collision_on','05 家具碰撞开启：同样推力被桌腿阻挡'),
        (41,46,'locked','06 抽屉锁定：发出打开指令'),
        (46,54,'open','07 抽屉解锁：滑动关节打开'),
        (54,59,'close','08 关闭抽屉'),
        (59,66,'colliders','09 显示简化碰撞体'),(66,72,'end','10 演示结束')]


def record(demo, fps=25):
    import imageio_ffmpeg
    from PIL import Image,ImageDraw,ImageFont
    width,height=1280,720
    fontpath='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
    font=ImageFont.truetype(fontpath,23); small=ImageFont.truetype(fontpath,18); title=ImageFont.truetype(fontpath,28)
    target=demo.directory/'room003_walkthrough_physics_v002.mp4'
    encoder=subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-loglevel','error','-f','rawvideo','-vcodec','rawvideo',
        '-s',f'{width}x{height}','-pix_fmt','rgb24','-r',str(fps),'-i','-','-an','-c:v','libx264',
        '-preset','fast','-crf','20','-pix_fmt','yuv420p','-movflags','+faststart',str(target)],stdin=subprocess.PIPE)
    option=mujoco.MjvOption(); option.geomgroup[1:4]=0
    trace=[]; previous=None
    try:
        with mujoco.Renderer(demo.model,height=height,width=width) as renderer:
            for frame in range(72*fps):
                t=frame/fps
                start,end,key,label=next(s for s in STAGES if s[0]<=t<s[1]); elapsed=t-start
                if key!=previous:
                    if key=='walk': demo.reset()
                    elif key=='gravity_off': demo.reset('gravity'); demo.gravity(False)
                    elif key=='gravity_on': demo.gravity(True)
                    elif key.startswith('collision_'):
                        demo.reset('collision'); demo.collision(key=='collision_on')
                    elif key=='locked': demo.reset(); demo.data.ctrl[:]=.14
                    elif key=='open': demo.lock(False)
                    elif key=='close': demo.data.ctrl[:]=0
                    elif key in ('colliders','end'): demo.reset()
                    trace.append(dict(video_time=t,event='stage_start',stage=key,state=demo.state()))
                    previous=key
                option.geomgroup[3]=int(key=='colliders')
                if key=='walk':
                    u=t/18
                    position=[-1.05+.22*math.sin(u*math.pi*2),.65+1.5*u,.05]
                    # Start through the aisle, glance at the desks, then face the vanity.
                    targetpoint=[-1.05+.7*math.sin(u*math.pi*2),2.9,-.7]
                    look_at(demo.model,position,targetpoint)
                    demo.step(1/fps)
                elif key.startswith('gravity') or key.startswith('collision'):
                    look_at(demo.model,[-1.05,2.2,-.15],[-1.30,3.0,-1.18])
                    # Collision comparison advances exactly 0.8 s, then holds the final state.
                    if key.startswith('collision'):
                        if elapsed<.8-1e-6: demo.step(1/fps,force=20)
                    else: demo.step(1/fps)
                elif key in ('locked','open','close'):
                    look_at(demo.model,[-.65,2.2,-.5],[-1.2,3.2,-.93])
                    if key=='open':
                        demo.data.ctrl[:]=[.14,.14 if elapsed>2 else 0]
                    demo.step(1/fps)
                else:
                    u=(t-start)/(end-start)
                    look_at(demo.model,[-.2-.5*u,.2,1.6],[-1.05,2.5,-.7])
                    demo.step(1/fps)
                mujoco.mj_forward(demo.model,demo.data)
                renderer.update_scene(demo.data,camera='walkthrough',scene_option=option)
                img=Image.fromarray(renderer.render()); draw=ImageDraw.Draw(img)
                draw.rectangle((0,0,width,110),fill=(16,22,30))
                draw.text((24,10),label,font=title,fill='white')
                state=demo.state()
                flags=[('重力',state['gravity']),('家具碰撞',state['furniture_collision']),('抽屉锁定',state['drawers_locked'])]
                for x,(name,enabled) in zip([24,260,555],flags):
                    color=(70,225,155) if enabled else (245,170,80)
                    draw.rounded_rectangle((x,57,x+205,95),radius=7,fill=(38,48,61))
                    draw.text((x+10,60),name+('  开' if enabled else '  关'),font=font,fill=color)
                draw.rectangle((0,height-79,width,height),fill=(16,22,30))
                meters=state['drawer_m']
                draw.text((24,height-74),f"凳子高度 {state['stool_xyz'][2]-demo.report['floor_z']:.3f} m（中心）    抽屉位移 {meters[0]*100:.1f} / {meters[1]*100:.1f} cm    接触点 {state['contacts']}",font=small,fill='white')
                note='演示参数，未实测校准｜仅凳子自由运动、两抽屉滑动｜地板碰撞始终开启'
                draw.text((24,height-43),note,font=small,fill=(245,195,115))
                draw.text((width-125,14),f'{t:04.1f} / 72s',font=small,fill='white')
                if key.startswith('collision'):
                    draw.text((24,120),'同一初始位置 + 20 N 推力 × 0.8 s；随后定格',font=small,fill=(255,255,255),stroke_width=2,stroke_fill='black')
                if key=='colliders':
                    draw.text((24,120),'青色：简化碰撞体；抽屉与柜体内部碰撞尚未建模',font=small,fill='white',stroke_width=2,stroke_fill='black')
                if frame%fps==0: trace.append(dict(video_time=t,stage=key,state=state))
                if frame in [8*fps,21*fps,27*fps,34*fps,40*fps,45*fps,52*fps,58*fps,63*fps]:
                    img.save(demo.directory/f'frame_{int(t):02d}.png')
                encoder.stdin.write(img.tobytes())
                if frame%(5*fps)==0: print('VIDEO_PROGRESS',frame,'/',72*fps,flush=True)
        encoder.stdin.close()
        if encoder.wait()!=0: raise RuntimeError('Video encoding failed')
    except BaseException:
        encoder.kill(); encoder.wait(); raise
    (demo.directory/'recording_trace.json').write_text(json.dumps(trace,indent=2))
    (demo.directory/'chapters.json').write_text(json.dumps(STAGES,ensure_ascii=False,indent=2))
    print('VIDEO_COMPLETE',target,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('directory',type=Path); p.add_argument('--record',action='store_true')
    a=p.parse_args(); d=Demo(a.directory); verify(d)
    if a.record: record(d)
