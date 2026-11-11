import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


class LambdaPackagingTests(unittest.TestCase):
    def test_handler_imports_when_modules_are_at_zip_root(self):
        source = Path(__file__).resolve().parents[1] / 'lambda_function'
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'function.zip'
            with ZipFile(package, 'w') as archive:
                for filename in ('app.py', 'sportrick.py'):
                    archive.write(source / filename, filename)
            script = (
                'import sys; sys.path.insert(0, sys.argv[1]); import app; '
                'assert callable(app.lambda_handler); '
                'assert app.SportrickClient.__module__ == "sportrick"'
            )
            result = subprocess.run([sys.executable, '-W', 'ignore', '-B', '-c', script, str(package)],
                                    cwd=directory, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
