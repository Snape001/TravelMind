from collections.abc import Iterable
from ipaddress import ip_address
from urllib.parse import unquote, unquote_plus, urlsplit

from pydantic import HttpUrl, ValidationError

from travelmind.schemas.domain import Evidence, SourceType

TRACKING_KEYS = frozenset({"fbclid", "gclid", "msclkid"})
DOI_HOSTS = frozenset({"doi.org", "dx.doi.org", "www.doi.org"})
NON_PUBLIC_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".onion", ".test")


def source_url(value: object) -> HttpUrl | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        url = HttpUrl(value)
    except ValidationError:
        return None
    return url if is_public_target(url) else None


def is_public_target(url: HttpUrl) -> bool:
    if url.username is not None or url.password is not None:
        return False
    host = (url.host or "").strip("[]").rstrip(".").lower()
    try:
        address = ip_address(host)
    except ValueError:
        return "." in host and not host.endswith(NON_PUBLIC_SUFFIXES)
    return address.is_global and not (address.is_multicast or address.is_reserved)


def web_source_type(url: HttpUrl) -> SourceType:
    host = (url.host or "").rstrip(".").lower()
    return "official" if host == "gov.cn" or host.endswith(".gov.cn") else "web"


def source_key(item: Evidence) -> tuple[str, str]:
    if item.source_url is None:
        return item.source_type, " ".join((item.source_locator or "").split()).casefold()
    return url_key(item.source_url)


def url_key(url: HttpUrl) -> tuple[str, str]:
    parts = urlsplit(str(url))
    host = (parts.hostname or "").rstrip(".")
    if host in DOI_HOSTS:
        doi = unquote(parts.path.lstrip("/")).casefold()
        if doi.startswith("10.") and "/" in doi:
            return "doi", doi

    params = []
    for param in parts.query.split("&"):
        name = unquote_plus(param.partition("=")[0]).lower()
        if not name.startswith("utm_") and name not in TRACKING_KEYS:
            params.append(param)
    # Preserve path, scheme, and the order of parameters that may select different content.
    normalized = parts._replace(
        netloc=parts.netloc.rstrip("."), query="&".join(params), fragment=""
    )
    return "url", normalized.geturl()


def unique_sources(items: Iterable[Evidence]) -> list[Evidence]:
    by_source = {}
    for item in items:
        by_source.setdefault(source_key(item), item)
    return list(by_source.values())
