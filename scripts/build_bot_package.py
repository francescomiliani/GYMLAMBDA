#!/usr/bin/env python3
"""Build the staging directory used as CodeUri by bot-template.yaml.

Only an explicit allowlist of files is copied; every other source file (e.g. the
git-ignored credentials.json) is ignored. The build fails if an allowlisted file
is missing or if the output contains anything outside the allowlist.
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'lambda_function'
TARGET = ROOT / 'build' / 'bot-package'

ALLOWLIST = (
    'app.py',
    'bot_scheduler.py',
    'bot_store.py',
    'requirements.txt',
    'scheduled_guard.py',
    'ssm_config.py',
    'sportrick.py',
    'telegram_bot.py',
    'telegram_worker.py',
)
IGNORED = ('__init__.py',)  # present in source, deliberately not packaged
FORBIDDEN = ('credentials.json',)


class PackageError(Exception):
    pass


def build(source=SOURCE, target=TARGET):
    source, target = Path(source), Path(target)
    missing = sorted(name for name in ALLOWLIST if not (source / name).is_file())
    if missing:
        raise PackageError(f'missing allowlisted file(s) in {source}: {", ".join(missing)}')
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for name in ALLOWLIST:
        shutil.copy2(source / name, target / name)
    # Final guard on the output: only allowlisted files, never credentials.
    produced = {p.name for p in target.rglob('*')}
    bad = sorted(produced - set(ALLOWLIST))
    if bad or produced & set(FORBIDDEN):
        shutil.rmtree(target)
        raise PackageError(f'unexpected file(s) in output {target}: {", ".join(bad)}')
    return sorted(ALLOWLIST)


def main():
    try:
        copied = build()
    except PackageError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    print(f'Built {TARGET.relative_to(ROOT)}:')
    for name in copied:
        print(f'  {name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
