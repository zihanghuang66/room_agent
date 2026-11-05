"""Fail early with a complete list of app-side missing/import-broken dependencies."""
import importlib
MODULES = ('numpy', 'PIL', 'open3d', 'trimesh', 'mcp', 'jsonschema', 'fcl', 'coacd', 'mujoco')

def probe():
    modules = {}; missing = []
    for name in MODULES:
        try:
            module = importlib.import_module(name)
            modules[name] = dict(imported=True, version=getattr(module, '__version__', None))
        except Exception as exc:
            missing.append(name)
            modules[name] = dict(imported=False, error=type(exc).__name__ + ': ' + str(exc))
    return dict(status='failed' if missing else 'passed', modules=modules, missing=missing,
                note='Imports only; Blender, MCP and CUDA execution have separate checks.')
