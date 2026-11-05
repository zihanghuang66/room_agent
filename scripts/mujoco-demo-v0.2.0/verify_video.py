import hashlib,json,sys
from pathlib import Path
import imageio_ffmpeg

directory=Path(sys.argv[1])
video=directory/'room003_walkthrough_physics_v002.mp4'
frames,seconds=imageio_ffmpeg.count_frames_and_secs(str(video))
assert frames==1800 and abs(seconds-72)<.05,(frames,seconds)
trace=json.loads((directory/'recording_trace.json').read_text())
last={}
for entry in trace:
    last[entry['stage']]=entry['state']
assert len(last)==10
assert not last['gravity_off']['gravity'] and last['gravity_on']['gravity']
drop=last['gravity_off']['stool_xyz'][2]-last['gravity_on']['stool_xyz'][2]
assert .25<drop<.48
assert not last['collision_off']['furniture_collision'] and last['collision_on']['furniture_collision']
travel=last['collision_off']['stool_xyz'][1]-last['collision_on']['stool_xyz'][1]
assert travel>.1
assert last['locked']['drawers_locked'] and not last['open']['drawers_locked']
assert max(abs(x) for x in last['locked']['drawer_m'])<.003
assert min(last['open']['drawer_m'])>.13
assert max(abs(x) for x in last['close']['drawer_m'])<.003
result=dict(video=video.name,frames=frames,duration_seconds=seconds,bytes=video.stat().st_size,
    sha256=hashlib.sha256(video.read_bytes()).hexdigest(),all_frames_decoded=True,
    recorded_gravity_drop_m=drop,recorded_collision_difference_m=travel,recorded_drawer_open_m=last['open']['drawer_m'],passed=True)
(directory/'video_validation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
