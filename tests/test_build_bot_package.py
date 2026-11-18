import importlib.util
import tempfile
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    'build_bot_package', Path(__file__).resolve().parents[1] / 'scripts' / 'build_bot_package.py')
builder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(builder)


class BuildBotPackageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.source = root / 'src'
        self.target = root / 'out' / 'bot-package'
        self.source.mkdir()
        for name in builder.ALLOWLIST + builder.IGNORED:
            (self.source / name).write_text('# ' + name)

    def test_copies_only_allowlist(self):
        (self.source / '__pycache__').mkdir()
        (self.source / '__pycache__' / 'x.pyc').write_bytes(b'')
        copied = builder.build(self.source, self.target)
        self.assertEqual(copied, sorted(builder.ALLOWLIST))
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), sorted(builder.ALLOWLIST))
        self.assertFalse((self.target / 'credentials.json').exists())
        self.assertFalse((self.target / '__init__.py').exists())

    def test_allowlist_covers_every_local_import_of_packaged_modules(self):
        import re
        root = Path(__file__).resolve().parents[1] / 'lambda_function'
        local = {p.stem for p in root.glob('*.py')}
        packaged = {Path(name).stem for name in builder.ALLOWLIST if name.endswith('.py')}
        for name in sorted(packaged):
            text = (root / (name + '.py')).read_text()
            imported = set(re.findall(r'^\s+(?:import|from) (\w+)', text, re.M))
            imported |= {m for group in re.findall(r'^\s+from \. import ([\w, ]+)', text, re.M)
                         for m in re.split(r',\s*', group)}
            missing = (imported & local) - packaged - {name}
            self.assertFalse(missing, f'{name}.py imports {sorted(missing)} which are not packaged')

    def test_includes_timetable(self):
        self.assertIn('timetable.py', builder.ALLOWLIST)

    def test_includes_ssm_config(self):
        self.assertIn('ssm_config.py', builder.ALLOWLIST)
        builder.build(self.source, self.target)
        self.assertTrue((self.target / 'ssm_config.py').exists())

    def test_ignores_credentials_in_source(self):
        (self.source / 'credentials.json').write_text('{"secret": 1}')
        builder.build(self.source, self.target)
        self.assertFalse((self.target / 'credentials.json').exists())
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), sorted(builder.ALLOWLIST))

    def test_ignores_unlisted_source_file(self):
        (self.source / 'notes.txt').write_text('x')
        builder.build(self.source, self.target)
        self.assertFalse((self.target / 'notes.txt').exists())

    def test_fails_if_output_contains_unlisted_file(self):
        original = builder.shutil.copy2
        def leaky(src, dst):
            original(src, dst)
            (Path(dst).parent / 'credentials.json').write_text('x')
        builder.shutil.copy2 = leaky
        self.addCleanup(setattr, builder.shutil, 'copy2', original)
        with self.assertRaisesRegex(builder.PackageError, 'credentials.json'):
            builder.build(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_fails_on_missing_file(self):
        (self.source / 'app.py').unlink()
        with self.assertRaisesRegex(builder.PackageError, 'app.py'):
            builder.build(self.source, self.target)

    def test_rebuild_removes_stale_files(self):
        builder.build(self.source, self.target)
        (self.target / 'stale.json').write_text('x')
        builder.build(self.source, self.target)
        self.assertFalse((self.target / 'stale.json').exists())


if __name__ == '__main__':
    unittest.main()
