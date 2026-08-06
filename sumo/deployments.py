from dataclasses import dataclass


@dataclass
class Deployment:
    code: str
    name: str
    service_url: str
    api_base: str


# Standard public deployments (service.{region}.sumologic.com)
DEPLOYMENTS: dict[str, Deployment] = {
    "us1": Deployment(
        code="us1",
        name="US East (N. Virginia)",
        service_url="https://service.sumologic.com",
        api_base="https://api.sumologic.com",
    ),
    "us2": Deployment(
        code="us2",
        name="US West (Oregon)",
        service_url="https://service.us2.sumologic.com",
        api_base="https://api.us2.sumologic.com",
    ),
    "eu": Deployment(
        code="eu",
        name="Europe (Ireland)",
        service_url="https://service.eu.sumologic.com",
        api_base="https://api.eu.sumologic.com",
    ),
    "de": Deployment(
        code="de",
        name="Europe (Frankfurt)",
        service_url="https://service.de.sumologic.com",
        api_base="https://api.de.sumologic.com",
    ),
    "au": Deployment(
        code="au",
        name="Asia Pacific (Sydney)",
        service_url="https://service.au.sumologic.com",
        api_base="https://api.au.sumologic.com",
    ),
    "jp": Deployment(
        code="jp",
        name="Asia Pacific (Tokyo)",
        service_url="https://service.jp.sumologic.com",
        api_base="https://api.jp.sumologic.com",
    ),
    "ca": Deployment(
        code="ca",
        name="Canada (Central)",
        service_url="https://service.ca.sumologic.com",
        api_base="https://api.ca.sumologic.com",
    ),
    "kr": Deployment(
        code="kr",
        name="Asia Pacific (Seoul)",
        service_url="https://service.kr.sumologic.com",
        api_base="https://api.kr.sumologic.com",
    ),
}


def get_deployment(code: str) -> Deployment:
    if code in DEPLOYMENTS:
        return DEPLOYMENTS[code]
    raise ValueError(f"Unknown deployment: {code}. Valid: {list(DEPLOYMENTS.keys())} or use from_url()")


def from_url(base_url: str) -> Deployment:
    """
    Create a Deployment from a raw Sumo Logic URL.
    Supports both patterns:
      - https://service.au.sumologic.com (public)
      - https://syddata.long.sumologic.net (pod-specific)
    """
    url = base_url.rstrip("/")
    if not url.startswith("https://"):
        url = f"https://{url}"

    # For pod-specific URLs (e.g., syddata.long.sumologic.net),
    # the service_url and api_base are the same host.
    return Deployment(
        code=url,
        name=url.replace("https://", ""),
        service_url=url,
        api_base=url,
    )
