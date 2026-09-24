import ast
import importlib.util
from pathlib import Path

import zipline

GENERATOR = Path(__file__).parents[1] / "scripts" / "gen_api_stub.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("gen_api_stub", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summarize(source):
    """What a stub declares, whatever its formatting: its imports, its
    ``__all__`` and each function's signature and docstring.
    """
    imports = set()
    functions = {}
    all_ = None
    for node in ast.parse(source).body:
        if isinstance(node, ast.ImportFrom):
            imports.update((node.module, a.name, a.asname) for a in node.names)
        elif isinstance(node, ast.Import):
            imports.update((None, a.name, a.asname) for a in node.names)
        elif isinstance(node, ast.FunctionDef):
            docstring = ast.get_docstring(node)
            if docstring is not None:
                node.body = node.body[1:]
                # The formatter strips blank lines at the end.
                docstring = docstring.strip()
            functions[node.name] = (ast.dump(node), docstring)
        elif isinstance(node, ast.AnnAssign):
            all_ = ast.literal_eval(node.value)
    return imports, all_, functions


def test_api_stub_is_up_to_date():
    stub = Path(zipline.__file__).parent / "api.pyi"
    generated = summarize(load_generator().render())
    committed = summarize(stub.read_text())
    assert committed == generated, (
        "zipline/api.pyi is out of date; regenerate it with scripts/gen_api_stub.py"
    )
