"""Read-only audit of the superseded V4 candidate; never extracts or executes it."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.archive) as archive:
        names = set(archive.namelist())
        broken = archive.testzip()
        dockerfile = archive.read('Dockerfile').decode('utf-8')
        limits = archive.read('py-src/data_formulator/ecommerce/process_limits.py').decode('utf-8')
        compose = archive.read('deploy/ecommerce/compose.yml').decode('utf-8')
        checks = {
            'archive_crc_valid': broken is None,
            'pyproject_present': 'pyproject.toml' in names,
            'python_lock_present': 'uv.lock' in names,
            'license_present': 'LICENSE' in names,
            'not_upstream_cli_entrypoint': '"data_formulator", "--host"' not in dockerfile,
            'not_windows_only_worker': 'verified on Windows only' not in limits,
            'application_service_present': any(line in compose.splitlines() for line in ('  app:', '  application:')),
        }
        prohibited = [name for name in names if any(part in {'.git', '.venv', '.local', 'node_modules', '.env.v4.private'}
                      for part in name.split('/')) or name.endswith(('.pem', '.key'))]
        checks['prohibited_paths_absent'] = not prohibited
    report = {
        'status': 'blocked' if not all(checks.values()) else 'static-checks-only',
        'archive_sha256': hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        'checks': checks, 'prohibited_path_count': len(prohibited),
        'limitations': ['No content secret-scan, image build, Linux behavior or release acceptance performed.'],
        'real_model_calls': 0,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(report, indent=2))
    return 1 if report['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
