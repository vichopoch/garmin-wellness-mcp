"""The production default must never register an unreviewed/mutating handler."""
import ast
import importlib
from pathlib import Path

import pytest

from garmin_mcp import _ToolFilter
from garmin_mcp import read_only


class Registry:
    def __init__(self):
        self.functions = []

    def tool(self, *args, **kwargs):
        def register(fn):
            self.functions.append(fn)
            return fn
        return register


def upstream_handlers():
    registry = Registry()
    for module in read_only._MANIFEST['modules']:
        # Wellness registration takes its service explicitly (tested separately).
        if module != 'garmin_mcp.wellness':
            importlib.import_module(module).register_tools(registry)
    return registry.functions


def test_every_upstream_tool_has_review():
    root = Path(read_only.__file__).parent
    discovered = set()
    for module in read_only._MANIFEST['modules']:
        if module == 'garmin_mcp.wellness':
            continue
        tree = ast.parse((root / (module.rsplit('.', 1)[1] + '.py')).read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == 'tool'
                for d in node.decorator_list
            ):
                discovered.add(module + '.' + node.name)
    upstream = {key for key in read_only.TOOL_AUDIT if not key.startswith('garmin_mcp.wellness.')}
    assert discovered == upstream
    assert all(read_only.classify_tool(fn) != read_only.Classification.UNKNOWN for fn in upstream_handlers())


def test_no_write_tools_registered_even_if_allowlisted(monkeypatch):
    monkeypatch.setenv('GARMIN_READ_ONLY', 'true')
    handlers = upstream_handlers()
    app = Registry()
    filt = _ToolFilter(app, {fn.__name__ for fn in handlers}, set())
    for fn in handlers:
        filt.tool()(fn)
    assert app.functions
    assert len(app.functions) == sum(read_only.classify_tool(fn) == read_only.Classification.READ for fn in handlers)
    assert all(read_only.classify_tool(fn) == read_only.Classification.READ for fn in app.functions)
    assert not any(fn.__name__ in {'request_reload', 'download_activity_file', 'download_course_gpx', 'set_fit_download_dir'} for fn in app.functions)


def test_secure_default(monkeypatch):
    monkeypatch.delenv('GARMIN_READ_ONLY', raising=False)
    assert read_only.read_only_enabled()
    app = Registry()
    filt = _ToolFilter(app, {'get_sleep_data'}, set())
    def get_sleep_data():
        return 'unreviewed replacement'
    filt.tool(name='get_sleep_data')(get_sleep_data)
    assert not app.functions


def test_changed_source_is_unknown(monkeypatch):
    fn = next(fn for fn in upstream_handlers() if fn.__name__ == 'get_sleep_data')
    monkeypatch.setitem(read_only._MANIFEST['modules'], fn.__module__, 'invalid digest')
    assert read_only.classify_tool(fn) == read_only.Classification.UNKNOWN


def test_changed_sdk_is_unknown(monkeypatch):
    fn = next(fn for fn in upstream_handlers() if fn.__name__ == 'get_sleep_data')
    monkeypatch.setitem(read_only._MANIFEST['sdk_files'], 'garminconnect.client', 'invalid digest')
    assert read_only.classify_tool(fn) == read_only.Classification.UNKNOWN
    with pytest.raises(PermissionError):
        read_only.validate_read_method('get_sleep_data')


@pytest.mark.parametrize('name', ['get_new_unreviewed_metric', 'connectapi', 'client', 'query_garmin_graphql', 'request_reload', 'delete_activity', 'upload_workout'])
def test_underlying_client_escape_hatches_denied(name):
    with pytest.raises(PermissionError):
        read_only.validate_read_method(name)


def test_reviewed_sdk_calls_accepted():
    for name in ['get_sleep_data', 'get_hrv_data', 'get_stress_data', 'get_body_battery', 'get_activities', 'get_training_readiness']:
        read_only.validate_read_method(name)


def test_malformed_config_fails_closed(monkeypatch):
    monkeypatch.setenv('GARMIN_READ_ONLY', 'tru')
    with pytest.raises(ValueError):
        _ToolFilter(Registry(), set(), set())


def test_mutation_cannot_be_renamed_to_allowed_read_tool(monkeypatch):
    monkeypatch.setenv('GARMIN_READ_ONLY', 'true')
    mutation = next(fn for fn in upstream_handlers() if fn.__name__ == 'request_reload')
    app = Registry()
    _ToolFilter(app, {'get_sleep_data'}, set()).tool(name='get_sleep_data')(mutation)
    assert not app.functions


def test_replacement_with_spoofed_public_identity_is_unknown():
    original = next(fn for fn in upstream_handlers() if fn.__name__ == 'get_sleep_data')
    def replacement():
        return 'unreviewed'
    replacement.__name__ = original.__name__
    replacement.__qualname__ = original.__qualname__
    replacement.__module__ = original.__module__
    assert read_only.classify_tool(replacement) == read_only.Classification.UNKNOWN


def test_imperative_registration_cannot_bypass_policy(monkeypatch):
    monkeypatch.setenv('GARMIN_READ_ONLY', 'true')
    mutation = next(fn for fn in upstream_handlers() if fn.__name__ == 'request_reload')
    app = Registry()
    _ToolFilter(app, {'request_reload'}, set()).add_tool(mutation)
    assert not app.functions


def test_all_production_wellness_handlers_are_registered_and_read_only(monkeypatch):
    from garmin_mcp.wellness import WellnessService, register_tools
    monkeypatch.setenv('GARMIN_READ_ONLY', 'true')
    app = Registry()
    register_tools(_ToolFilter(app, set(), set()), WellnessService(object()))
    expected = {key.rsplit('.', 1)[1] for key in read_only.TOOL_AUDIT if key.startswith('garmin_mcp.wellness.')}
    assert len(expected) == 22
    assert {fn.__name__ for fn in app.functions} == expected
    assert all(read_only.classify_tool(fn) == read_only.Classification.READ for fn in app.functions)


def test_unreviewed_wrapper_cannot_borrow_reviewed_identity():
    from functools import wraps
    original = next(fn for fn in upstream_handlers() if fn.__name__ == 'get_sleep_data')
    @wraps(original)
    def wrapper(*args, **kwargs):
        return 'unreviewed additional effect'
    assert read_only.classify_tool(wrapper) == read_only.Classification.UNKNOWN
