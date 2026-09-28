"""Deployment entry-point smoke test.

`src/api/server.py` is what `uvicorn src.api.server:app` loads. Importing it runs every
module-level statement: the runtime config is read, the graph is built and compiled, and the
secret provider is provisioned. A module-level failure there — an import that does not resolve
against the installed framework, a renamed symbol — means the deployment does not start, while
every unit test that exercises nodes and services in isolation still passes.

No other test in this suite imports that module, so this file is the only thing standing between
such a failure and a deployment. The import is the assertion.
"""

import importlib

import yaml


def _entry_point():
    return importlib.import_module("src.api.server")


def test_entry_point_module_imports():
    module = _entry_point()

    assert module.app is not None
    assert module.agent is not None


def test_entry_point_loads_the_runtime_config():
    """The graph receives the parsed `config/config.yaml`, not the empty default a config-less
    construction would leave behind — otherwise every value in that file is inert on the
    deployed standalone path while still applying on the AgentRegistry path."""
    module = _entry_point()
    shipped = yaml.safe_load(module._CONFIG_PATH.read_text())

    assert isinstance(module.agent.config, dict)
    for key, value in shipped.items():
        assert module.agent.config[key] == value
