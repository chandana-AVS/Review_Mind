from app.clients.http import make_client
from app.config import settings

_client = make_client("card_network", settings.card_network_base_url)


def network_status(network: str) -> str:
    response = _client.get(f"/v1/networks/{network}/status")
    response.raise_for_status()
    return str(response.json()["status"])
