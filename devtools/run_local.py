"""Run the pinned upstream application locally, without model credentials.

V0-1 bootstraps the original app only. Qwen is enabled separately in V0-2.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def configure_offline() -> None:
    # The registry discovers *_ENABLED variables on import. Prevent accidental
    # provider pings before V0-2; do not read or print any existing secret value.
    for name in list(os.environ):
        upper = name.upper()
        if upper.endswith("_ENABLED"):
            os.environ[name] = "false"
        if upper.endswith(("_API_KEY", "_API_TOKEN")) or upper == "QWEN-API-KEY":
            os.environ.pop(name, None)
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "DATA_FORMULATOR_HOME": str(ROOT / ".local" / "runtime"),
        "HOST": "127.0.0.1",
        "WORKSPACE_BACKEND": "local",
        "DISABLE_DATABASE": "false",
        "DISABLE_DATA_CONNECTORS": "true",
        "DISABLE_CUSTOM_MODELS": "true",
        "DISABLE_DISPLAY_KEYS": "true",
        "AUTH_PROVIDER": "",
        "SANDBOX": "local",
        "ECOMMERCE_RESTRICTED": "true",
    })


def main() -> None:
    configure_offline()
    os.chdir(ROOT)
    sys.argv = [
        "data_formulator", "--dev", "--host", "127.0.0.1", "--port", "5567",
        "--workspace-backend", "local", "--data-dir", os.environ["DATA_FORMULATOR_HOME"],
        "--disable-data-connectors", "--disable-custom-models", "--disable-display-keys",
    ]
    from data_formulator import run_app
    run_app()


if __name__ == "__main__":
    main()
