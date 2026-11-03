from pathlib import Path
import importlib.util

TESTS_DIR = Path(__file__).parent

for path in sorted(TESTS_DIR.glob("test_*.py")):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in sorted(dir(module)):
        if name.startswith("test_"):
            getattr(module, name)()
            print(f"{path.name}:{name}: OK")
