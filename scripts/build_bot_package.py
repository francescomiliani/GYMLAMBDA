#!/usr/bin/env python3
"""Build the staging directories zipped by Terraform (infra/terraform/package.tf).

Only an explicit allowlist of files is copied; every other source file (e.g. the
git-ignored credentials.json) is ignored. The build fails if an allowlisted file
is missing or if the output contains anything outside the allowlist.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'lambda_function'
TARGET = ROOT / 'build' / 'bot-package'
LEGACY_TARGET = ROOT / 'build' / 'legacy-package'
REQUIREMENTS = SOURCE / 'requirements.txt'

ALLOWLIST = (
    'app.py',
    'bot_scheduler.py',
    'bot_store.py',
    'calendar_ics.py',
    'google_calendar.py',
    'requirements.txt',
    'scheduled_guard.py',
    'ssm_config.py',
    'store_conflict.py',
    'sportrick.py',
    'telegram_bot.py',
    'telegram_worker.py',
    'timetable.py',
)
# The legacy function GymBooking-personale only needs the booking client.
LEGACY_ALLOWLIST = ('app.py', 'sportrick.py', 'ssm_config.py')
IGNORED = ('__init__.py',)  # present in source, deliberately not packaged
FORBIDDEN = ('credentials.json',)


class PackageError(Exception):
    pass


def build(source=SOURCE, target=TARGET, allowlist=ALLOWLIST):
    source, target = Path(source), Path(target)
    missing = sorted(name for name in allowlist if not (source / name).is_file())
    if missing:
        raise PackageError(f'missing allowlisted file(s) in {source}: {", ".join(missing)}')
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in allowlist:
        shutil.copy2(source / name, target / name)
    # Final guard on the output: only allowlisted files, never credentials.
    produced = {p.name for p in target.rglob('*')}
    bad = sorted(produced - set(ALLOWLIST))
    if bad or produced & set(FORBIDDEN):
        shutil.rmtree(target)
        raise PackageError(f'unexpected file(s) in output {target}: {", ".join(bad)}')
    return sorted(allowlist)


def install_dependencies(target, requirements=REQUIREMENTS):
    """Install the runtime dependencies into target for Lambda (Linux x86_64, Python 3.12).

    Runs only after build() has verified the project files, so the allowlist
    check never sees third-party packages. Terraform zips the directory as is.
    """
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--quiet', '--target', str(target),
         '--platform', 'manylinux2014_x86_64', '--implementation', 'cp', '--python-version', '3.12',
         '--abi', 'cp312', '--abi', 'abi3', '--abi', 'none', '--only-binary=:all:',
         '-r', str(requirements)],
        capture_output=True, text=True)
    if result.returncode:
        raise PackageError(f'pip install failed: {result.stderr.strip()}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--legacy', action='store_true',
                        help='build the legacy GymBooking-personale package instead of the bot package')
    parser.add_argument('--with-deps', action='store_true',
                        help='also install requirements.txt (needed for the Terraform zip)')
    args = parser.parse_args(argv)
    target, allowlist = (LEGACY_TARGET, LEGACY_ALLOWLIST) if args.legacy else (TARGET, ALLOWLIST)
    try:
        copied = build(SOURCE, target, allowlist)
        if args.with_deps:
            install_dependencies(target)
    except PackageError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    print(f'Built {target.relative_to(ROOT)}:')
    for name in copied:
        print(f'  {name}')
    if args.with_deps:
        print('  + dependencies from requirements.txt')
    return 0


if __name__ == '__main__':
    sys.exit(main())
