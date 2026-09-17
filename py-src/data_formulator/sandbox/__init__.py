# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from .local_sandbox import LocalSandbox, SandboxSession
from .docker_sandbox import DockerSandbox

# Valid values for the --sandbox CLI option / SANDBOX env var.
SANDBOX_OPTIONS = ("local", "docker")


def create_sandbox(sandbox: str = "local") -> LocalSandbox | DockerSandbox:
    """Instantiate a sandbox from a config string.

    Parameters
    ----------
    sandbox : str
        ``"local"`` (default) or ``"docker"``.
    """
    from data_formulator.ecommerce.policy import deny_free_code
    deny_free_code()
    if sandbox == "docker":
        return DockerSandbox()
    return LocalSandbox()
