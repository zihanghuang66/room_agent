"""Post-start diagnostics; zero model/API calls and zero model downloads."""
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

from reports import archive, create, redact, save
from runtime import initialize

HERE = Path(__file__).resolve().parent
APP = '/opt/room/envs/app/bin/python'
GPU = '/opt/room/envs/gpu/bin/python'


def main():
    folder = create('room_image_check')
    result = {'image_recipe': '0.1.3', 'model_calls': 0, 'weights_downloaded': False,
              'full_scene_validated': False, 'checks': {}}

    def step(name, command, required=True, timeout=180):
        print(f'[{name}] running', flush=True)
        try:
            done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            output, code = done.stdout, done.returncode
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or b''
            if isinstance(output, bytes):
                output = output.decode(errors='replace')
            output += f'\nTIMEOUT after {timeout}s'
            code = 124
        except OSError as exc:
            output, code = str(exc), 127
        output = redact(output)
        (folder / (name + '.log')).write_text(output)
        result['checks'][name] = {'passed': code == 0, 'returncode': code, 'required': required}
        print(f'[{name}] ' + ('PASS' if code == 0 else 'FAIL') + '\n' + output[-4000:], flush=True)
        return code == 0

    try:
        work = initialize()
        result['runtime_root'] = str(work)
        pins = json.loads((HERE / 'pins.json').read_text())
        save(folder / 'pins.json', pins)
        step('ssh_client', ['ssh', '-V'])
        step('ssh_server_binary', [APP, '-c', 'import os; assert os.access("/usr/sbin/sshd", os.X_OK)'])
        step('tmux_version', ['tmux', '-V'])
        step('ghostty_terminfo', ['infocmp', '-x', 'xterm-ghostty'])
        step('tmux_terminfo', ['infocmp', 'tmux-256color'])
        step('app_imports', [APP, '-c', 'import open3d, fcl, coacd, mujoco, litereality_agent; print("OK")'])
        step('app_cli', [APP, '-m', 'litereality_agent.cli', '--help'])
        step('codex_version', ['codex', '--version'])
        step('cuda', [GPU, '-c', 'import torch; assert torch.cuda.is_available(), "CUDA unavailable"; '
             'x=torch.arange(8.,device="cuda"); assert x.sum().item()==28; '
             'print(torch.__version__,torch.cuda.get_device_name(0),torch.cuda.get_device_capability(0))'])
        step('gpu_extensions', [GPU, str(HERE / 'check_extensions.py')], timeout=600)
        from graphics_probe import probe as graphics_probe
        graphics = graphics_probe()
        save(folder / 'graphics.json', graphics)
        result['nvidia_graphics_libraries'] = {k: v for k, v in graphics['libraries'].items() if 'nvidia' in k}
        for mode in ('cpu', 'cuda', 'eevee'):
            output = folder / 'renders' / mode
            ok = step('blender_' + mode, ['blender', '--background', '--factory-startup',
                      '--python-exit-code', '1', '--python', str(HERE / 'blender_smoke.py'),
                      '--', '--mode', mode, '--output', str(output)], required=mode != 'eevee', timeout=300)
            rendered = output / (mode + '.png')
            verified = ok and rendered.is_file() and rendered.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'
            result['checks']['blender_' + mode]['passed'] = verified
            if not verified:
                print(f'[blender_{mode}] No verified PNG output', flush=True)
            if mode == 'eevee' and verified:
                details = json.loads((output / 'eevee.json').read_text())
                hardware = 'nvidia' in (details.get('renderer', '') + details.get('vendor', '')).lower()
                result['eevee_nvidia_confirmed'] = hardware
                print('[EEVEE NVIDIA] ' + ('PASS' if hardware else 'NOT CONFIRMED; may be software rendering'), flush=True)
        # Actual built-in sandbox probe, with managed policy preserved. No bypass fallback.
        from sandbox_probe import probe as sandbox_probe
        sandbox_work = folder / 'work'
        sandbox_work.mkdir()
        sandbox = sandbox_probe(folder, sandbox_work)
        save(folder / 'sandbox.json', sandbox)
        result['checks']['codex_sandbox_write'] = {'passed': sandbox['status'] == 'passed', 'required': True}
        print('[codex_sandbox_write]', sandbox['status'], flush=True)
        if sandbox['status'] != 'passed':
            print(redact(json.dumps(sandbox['locations'], ensure_ascii=False))[-6000:], flush=True)
        # Record status only; never copy auth files or login output into the report.
        auth = subprocess.run(['codex', 'login', 'status'], capture_output=True, text=True, timeout=20)
        result['codex_logged_in'] = auth.returncode == 0
        result['image_api_configured'] = any(os.environ.get(k) for k in ('OPENAI_API_KEY', 'GEMINI_API_KEY'))
        marker = Path(os.environ['LITEREALITY_WEIGHTS']) / 'room-models-v0.1.0.json'
        result['model_download_receipt_present'] = marker.is_file()
        result['remaining_for_full_pipeline'] = ['Codex actual model command and file-edit test',
            'model loading/inference on assigned GPU', 'image-generation service', 'real scene end-to-end validation']
        result['status'] = 'checks_passed_not_full_pipeline' if all(
            c['passed'] for c in result['checks'].values() if c.get('required')) else 'checks_failed'
    except Exception:
        error = redact(traceback.format_exc())
        (folder / 'fatal.log').write_text(error)
        print(error, flush=True)
        result['status'] = 'checks_failed'
    finally:
        save(folder / 'result.json', result)
        archive(folder)
    print('STATUS:', result['status'], flush=True)
    return 0 if result['status'] == 'checks_passed_not_full_pipeline' else 1


if __name__ == '__main__':
    sys.exit(main())
