"""Create the requested 2 fps experiment from the verified existing 4 fps sample."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
source = root / 'return_output/diagnostics_reports/room_diagnostics_20261030T082944Z_4exhuia1/run'
report = json.loads((source / 'report/frames.json').read_text())
previous = json.loads((root / 'return_output/reconstruction_reports/room_omega_20261030T132217Z_mb2bumjg/input_manifest.json').read_text())
assert len(report['frames']) == 220
for i, frame in enumerate(report['frames']):
    assert frame['resampled_time_seconds'] == i / 4
selected = report['frames'][20::2]
assert len(selected) == 100
for i, frame in enumerate(selected):
    assert frame['resampled_time_seconds'] == 5 + i * 0.5
    assert hashlib.sha256((source / 'frames' / frame['file']).read_bytes()).hexdigest() == frame['sha256']
overlap = sorted({x['file'] for x in selected} & {x['file'] for x in previous['selected_frames']})
manifest = dict(status='planned_not_executed', experiment_id='omega_2fps_5s_54p5s_100frames',
                method='VGGT-Omega-1B-512', expected_frame_count=100,
                source_fps=4.0, target_fps=2.0, start_seconds=5.0, last_sample_seconds=54.5,
                eligible_range='frame_000021.jpg through frame_000220.jpg; sample odd-numbered frames through 219',
                sampling='Every second frame from existing 4 fps extraction; exclude first 5 seconds of door opening; no video re-decoding',
                preprocessing=previous['preprocessing'], server_source_relative=previous['server_source_relative'],
                overlap_with_previous_50_count=len(overlap), overlap_with_previous_50=overlap,
                selected_frames=selected)
output = root / 'scripts/vggt-omega/input_manifest.json'
output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print('Saved', output)
print('100 verified frames; 5.00..54.50 seconds, every 0.5 s; shared with previous 50:', len(overlap))
