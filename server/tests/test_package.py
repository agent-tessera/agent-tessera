import agent_tessera
import tessera_server


def test_server_version_matches_sdk_version() -> None:
    assert tessera_server.__version__ == agent_tessera.__version__
