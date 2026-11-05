"""Native Ubuntu MuJoCo viewer, or an offscreen render/compile smoke test."""
import argparse
import json
import time
from pathlib import Path

import mujoco
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('scene', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--seconds', type=float, default=0)
    args = parser.parse_args()
    scene = args.scene.resolve()
    meta = json.loads((scene.parent / 'export_report.json').read_text())
    start = time.monotonic()
    model = mujoco.MjModel.from_xml_path(str(scene))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    assert model.nq == 0 and model.nv == 0, 'This demo must stay static'
    assert not np.any(model.geom_contype) and not np.any(model.geom_conaffinity)
    for _ in range(100):
        mujoco.mj_step(model, data)
    assert np.isfinite(data.geom_xpos).all()
    stats = dict(mujoco=mujoco.__version__, bodies=model.nbody, meshes=model.nmesh,
                 geoms=model.ngeom, textures=model.ntex, joints=model.njnt,
                 mode=meta['mode'], compile_and_step_seconds=time.monotonic()-start)
    print(json.dumps(stats), flush=True)
    cam = mujoco.MjvCamera()
    mujoco.mjv_defaultFreeCamera(model, cam)
    cam.lookat[:] = meta['center']
    cam.distance = meta['extent'] * 1.25
    cam.azimuth, cam.elevation = 100, -55
    option = mujoco.MjvOption()
    option.geomgroup[1:3] = 0  # Cutaway: walls/ceiling can be shown in the viewer's groups panel.
    if args.check:
        from PIL import Image
        with mujoco.Renderer(model, height=960, width=1280) as renderer:
            renderer.update_scene(data, camera=cam, scene_option=option)
            Image.fromarray(renderer.render()).save(scene.parent / 'mujoco_overview.png')
            for c in meta['cameras']:
                if 'Demo_Vanity' in c['source']:
                    renderer.update_scene(data, camera=c['name'])
                    Image.fromarray(renderer.render()).save(scene.parent / 'mujoco_vanity.png')
                    break
        (scene.parent / 'runtime_check.json').write_text(json.dumps(stats, indent=2))
        print('MUJOCO_RENDER_CHECK_PASS', flush=True)
        return
    from mujoco import viewer
    with viewer.launch_passive(model, data) as handle:
        with handle.lock():
            handle.cam.lookat[:] = cam.lookat
            handle.cam.distance = cam.distance
            handle.cam.azimuth = cam.azimuth
            handle.cam.elevation = cam.elevation
            handle.opt.geomgroup[:] = option.geomgroup
        print('VIEWER_READY static room; walls=group1, ceiling=group2', flush=True)
        started = time.monotonic()
        while handle.is_running():
            if args.seconds and time.monotonic() - started > args.seconds:
                break
            handle.sync()
            time.sleep(1/30)


if __name__ == '__main__':
    main()
