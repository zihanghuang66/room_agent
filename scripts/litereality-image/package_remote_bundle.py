"""Package the tested remote recipe and its launch scripts with normalized timestamps."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

here=Path(__file__).resolve().parent
out=here.parent/'packages'
out.mkdir(exist_ok=True)
target=out/'room-litereality-image-0.1.3-cuda124-r3-build.tar.gz'
files={}
for name in ['Dockerfile','.dockerignore','provenance.json']:
    files['scripts/'+name]=(here/'remote-build'/name).read_bytes()
for name in ['run_remote_build.sh','build_remote.sh','prepare_remote_base.sh',
             'prepare_gpu_wheels.py','prepare_extra_gpu_wheels.py','prepare_remaining_gpu_wheels.py',
             'fetch_cudnn_ranges.py','download_artifacts.py','gpu-requirements.lock',
             'smoke_image.sh','smoke_gpu_imports.py','run_gpu_smoke.sh','run_codex_sandbox_smoke.sh',
             'verify_export.py','package_build_report.py','check_image_ssh.py']:
    files['scripts/'+name]=(here/name).read_bytes()
for name in ['Dockerfile','make_remote_dockerfile.py','install_cached_gpu.py']:
    files['scripts/source/'+name]=(here/name).read_bytes()
files['远程构建说明.md']=(here/'远程构建说明.md').read_bytes()
files['使用说明.md']='''# LiteReality 镜像远程构建包 cuda124-r3

这是可复建的脚本与 Dockerfile 包；成品镜像是另一个 `.docker.tar.gz` 文件。

解压后目录为 `room-image-build/`，代码在 `scripts/`，输出在 `return_output/`。
需要 x86_64 Linux、Docker/BuildKit、足够磁盘空间和可访问公开依赖源的网络。

在 Ubuntu 上启动构建：

```bash
cd room-image-build
bash scripts/run_remote_build.sh
```

脚本默认代理是 `http://127.0.0.1:17897`，即本次 Mac SSH 转发的地址；可用 `ROOM_BUILD_PROXY` 指定其他可用地址。准备脚本也从该变量设置 HTTP_PROXY/HTTPS_PROXY。先按《远程构建说明》建立代理，再运行构建。不要把令牌或账号目录加入构建上下文。

`Dockerfile` 已生成，无需手工生成；`scripts/source/` 保存生成器与原始配方，用于审查和修改。原始配方的学校基础镜像由构建脚本覆盖为经过摘要校验的公开 CUDA 基础镜像。

一键入口会准备依赖、构建、离线 CPU 验收、默认启动 SSH 登录与重启验收、导出和检查归档结构，最终生成诊断 tar。错误会同时显示在终端和日志。归档与内容时间戳固定为 0，避免未来时间警告。

可选实机测试：

```bash
bash scripts/run_gpu_smoke.sh
bash scripts/run_codex_sandbox_smoke.sh
```

本次 GTX 1070 / 535 驱动的 GPU 测试需使用 `ROOM_GPU_USE_HOST_DRIVER=1 bash scripts/run_gpu_smoke.sh`，不修改镜像默认设置。不能据此声称学校目标 GPU 内核或模型推理已经通过。Codex 沙箱在此宿主机默认 Docker 策略下仍被拒绝创建 namespace。

默认启动命令为 `/usr/local/bin/room-sshd`，容器启动时自动生成自身 host key 并启动 SSH；只允许公钥认证。平台仍需映射容器 22 端口并注入登录公钥。`ROOM_SSH_PORT` 可指定其他容器端口；如果平台覆盖启动命令，需保留上述命令。

学校导入成品镜像后，挂载持久化项目到 `/yinxin/projects/room`，分配目标 GPU，再运行 `/usr/local/bin/room-check`。账号、API 密钥、模型权重和扫描数据不在镜像里。
'''.encode()
files['manifest.json']=(json.dumps({name:hashlib.sha256(data).hexdigest() for name,data in files.items()},indent=2,ensure_ascii=False)+'\n').encode()
with target.open('wb') as raw,gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as gz:
    with tarfile.open(fileobj=gz,mode='w') as tar:
        for name,data in sorted(files.items()):
            info=tarfile.TarInfo('room-image-build/'+name)
            info.size=len(data);info.mode=0o755 if name.endswith('.sh') else 0o644
            info.uid=info.gid=info.mtime=0
            tar.addfile(info,io.BytesIO(data))
with tarfile.open(target,'r:gz') as tar:
    assert all(m.mtime==0 for m in tar.getmembers())
    for name,data in files.items():assert tar.extractfile('room-image-build/'+name).read()==data
sha=hashlib.sha256(target.read_bytes()).hexdigest()
target.with_name(target.name+'.sha256').write_text(sha+'  '+target.name+'\n')
print(target)
print(sha)
