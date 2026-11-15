"""Download verified bundle sources and select an unposted clip. No publishing."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile
from process_bundle_clip import process
from bundle_backend import call
from source_exclusions import exclusions

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend-ledger', action='store_true')
    parser.add_argument('--history', type=Path,
                        help='JSON list of source SHA256 hashes already reserved or published')
    parser.add_argument('--output', type=Path, default=ROOT / 'bundle-output')
    args = parser.parse_args()
    if not args.backend_ledger and not args.history:
        parser.error('--backend-ledger or --history is required')
    history = set(call('history')['hashes'] if args.backend_ledger else json.loads(args.history.read_text()))
    history.update(exclusions())
    manifest = json.loads((ROOT / 'data/bundle_manifest.json').read_text())
    sources = ROOT / 'bundles'
    sources.mkdir(exist_ok=True)
    args.output.mkdir(parents=True, exist_ok=True)
    for archive in manifest['archives']:
        path = sources / archive['asset']
        if not path.exists():
            subprocess.run(['gh', 'release', 'download', manifest['release_tag'],
                            '--repo', 'Skywalkers-24/instapilot-reel-worker',
                            '--pattern', archive['release_asset'], '--dir', str(sources)], check=True)
        downloaded = sources / archive['release_asset']
        if not path.exists() and downloaded.exists():
            downloaded.rename(path)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != archive['sha256']:
                raise ValueError('Archive checksum mismatch')
        with zipfile.ZipFile(path) as bundle:
            for entry in bundle.infolist():
                if entry.is_dir() or not entry.filename.lower().endswith('.mp4'):
                    continue
                # Fixed temporary name prevents archive path traversal.
                with tempfile.TemporaryDirectory() as temp:
                    candidate = Path(temp) / 'clip.mp4'
                    digest = hashlib.sha256()
                    with bundle.open(entry) as source, candidate.open('wb') as target:
                        while chunk := source.read(1024 * 1024):
                            digest.update(chunk)
                            target.write(chunk)
                    source_hash = digest.hexdigest()
                    if source_hash in history:
                        continue
                    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format',
                                             '-show_streams', '-of', 'json', str(candidate)],
                                            capture_output=True, text=True)
                    if result.returncode:
                        continue
                    info = json.loads(result.stdout)
                    duration = float(info.get('format', {}).get('duration', 0))
                    if duration <= 5 or not any(s['codec_type'] == 'video' for s in info['streams']):
                        continue
                    destination = args.output / f'{source_hash}.mp4'
                    candidate.replace(destination)
                    rendered = args.output / f'{source_hash}-rendered.mp4'
                    report = args.output / f'{source_hash}-report.json'
                    if not process(destination, rendered, report):
                        destination.unlink()
                        continue
                    reservation = call('reserve', {'source_sha256': source_hash,
                        'archive': archive['asset'], 'member': entry.filename}) if args.backend_ledger else {}
                    (args.output / 'selection.json').write_text(json.dumps({
                        'source_sha256': source_hash, 'archive': archive['asset'],
                        'member': entry.filename, 'duration': duration,
                        'video': str(rendered), 'status': 'rendered', **reservation}, indent=2))
                    print(destination)
                    return
    raise SystemExit('No eligible unused clip remains')


if __name__ == '__main__':
    main()
