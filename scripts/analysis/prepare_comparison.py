"""Audit the fixed real image sample without changing it or inferring scene quality."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageOps


def frame_metrics(image):
    rgb = np.asarray(image.convert('RGB'), dtype=np.float32)
    gray_image = image.convert('L')
    gray_image.thumbnail((640, 640), Image.Resampling.LANCZOS)
    gray = np.asarray(gray_image, dtype=np.float32)
    laplace = (gray[1:-1, :-2] + gray[1:-1, 2:] + gray[:-2, 1:-1] + gray[2:, 1:-1]
               - 4 * gray[1:-1, 1:-1])
    return dict(width=image.width, height=image.height,
                laplacian_variance_gray_max640=float(laplace.var()),
                near_black_fraction=float(np.mean(np.max(rgb, axis=-1) <= 5)),
                near_white_fraction=float(np.mean(np.min(rgb, axis=-1) >= 250)),
                mean_gray=float(gray.mean()))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--frames', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    frames, output = Path(args.frames).resolve(), Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(Path(args.manifest).read_text())
    items = manifest['selected_frames']
    rows, hashes = [], set()
    contact = Image.new('RGB', (1400, ((len(items) + 9) // 10) * 280), '#222222')
    draw = ImageDraw.Draw(contact)
    for i, item in enumerate(items):
        name = item['file']
        if Path(name).name != name:
            raise ValueError('Unsafe frame name')
        path = frames / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item['sha256']:
            raise ValueError('Input hash mismatch: ' + name)
        hashes.add(digest)
        with Image.open(path) as image:
            row = dict(file=name, resampled_time_seconds=item['resampled_time_seconds'], **frame_metrics(image))
            rows.append(row)
            thumb = ImageOps.contain(image.convert('RGB'), (136, 242), Image.Resampling.LANCZOS)
            x, y = (i % 10) * 140, (i // 10) * 280
            contact.paste(thumb, (x + (140 - thumb.width) // 2, y))
            draw.text((x + 3, y + 244), name.replace('frame_', '').replace('.jpg', ''), fill='white')
            draw.text((x + 3, y + 259), '%.2fs  L=%.1f' % (row['resampled_time_seconds'], row['laplacian_variance_gray_max640']), fill='white')
    contact.save(output / 'input_50_contact_sheet.jpg', quality=92)
    with (output / 'frames.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    times = [r['resampled_time_seconds'] for r in rows]
    stats = dict(count=len(rows), unique_content_hashes=len(hashes), dimensions=sorted({(r['width'], r['height']) for r in rows}),
                 first_time_seconds=min(times), last_time_seconds=max(times),
                 maximum_time_gap_seconds=float(np.diff(times).max()),
                 lowest_laplacian_10=sorted(rows, key=lambda r: r['laplacian_variance_gray_max640'])[:10],
                 all_input_hashes_verified=True, selection_changed=False,
                 caveat='Laplacian score depends on scene texture and resampling; it is not a universal blur threshold. RGB clipping fractions do not measure HDR scene radiance.')
    (output / 'summary.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n')
    (output / 'input_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
