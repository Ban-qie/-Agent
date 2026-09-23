"""Fail-closed HTTP deployment profiles for the ecommerce application."""
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit


FORWARDED_HEADERS = (
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_FORWARDED_HOST",
    "HTTP_X_FORWARDED_PROTO",
    "HTTP_X_FORWARDED_PORT",
    "HTTP_X_FORWARDED_PREFIX",
)


@dataclass(frozen=True)
class HttpProfile:
    name: str
    origin: str
    host: str
    secure_cookie: bool
    trusted_proxy_cidrs: tuple[str, ...] = ()
    trusted_proxy_hops: int = 0

    @classmethod
    def local(cls, origin="http://127.0.0.1:5567"):
        if origin != "http://127.0.0.1:5567":
            raise ValueError("The local profile has a fixed loopback origin")
        return cls("local", origin, "127.0.0.1:5567", False)

    @classmethod
    def production(cls, origin, *, trusted_proxy_cidrs, trusted_proxy_hops=1):
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except (TypeError, ValueError):
            raise ValueError("Invalid production origin") from None
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or port not in (None, 443)):
            raise ValueError("Production origin must be an HTTPS authority")
        canonical = f"https://{parsed.hostname.lower()}"
        if origin.rstrip("/").lower() != canonical:
            raise ValueError("Production origin must use its canonical HTTPS form")
        if type(trusted_proxy_hops) is not int or trusted_proxy_hops != 1:
            raise ValueError("Exactly one trusted reverse proxy hop is required")
        if not isinstance(trusted_proxy_cidrs, (list, tuple)) or not trusted_proxy_cidrs:
            raise ValueError("At least one trusted proxy network is required")
        networks = []
        for value in trusted_proxy_cidrs:
            try:
                network = ip_network(value, strict=True)
            except (TypeError, ValueError):
                raise ValueError("Invalid trusted proxy network") from None
            networks.append(str(network))
        return cls("production", canonical, parsed.hostname.lower(), True,
                   tuple(networks), trusted_proxy_hops)


class TrustedProxyMiddleware:
    """Accept forwarding metadata only from the frozen reverse-proxy network."""

    def __init__(self, application, profile):
        if profile.name != "production":
            raise ValueError("Trusted proxy middleware requires a production profile")
        self.application = application
        self.profile = profile
        self.networks = tuple(ip_network(value) for value in profile.trusted_proxy_cidrs)

    @staticmethod
    def _reject(start_response):
        body = b'{"error":{"code":"ACCESS_DENIED","message":"Trusted proxy required"}}'
        start_response("403 Forbidden", [("Content-Type", "application/json"),
                                          ("Content-Length", str(len(body))),
                                          ("Cache-Control", "no-store")])
        return [body]

    def __call__(self, environ, start_response):
        try:
            remote = ip_address(environ.get("REMOTE_ADDR", ""))
        except ValueError:
            return self._reject(start_response)
        forwarded = {name: environ.get(name) for name in FORWARDED_HEADERS if environ.get(name)}
        if not any(remote in network for network in self.networks):
            return self._reject(start_response)
        expected = {
            "HTTP_X_FORWARDED_PROTO": "https",
            "HTTP_X_FORWARDED_HOST": self.profile.host,
        }
        if any(forwarded.get(name, "").lower() != value for name, value in expected.items()):
            return self._reject(start_response)
        forwarded_for = forwarded.get("HTTP_X_FORWARDED_FOR", "")
        try:
            if "," in forwarded_for:
                raise ValueError()
            ip_address(forwarded_for)
        except ValueError:
            return self._reject(start_response)
        if forwarded.keys() - {"HTTP_X_FORWARDED_FOR", "HTTP_X_FORWARDED_HOST", "HTTP_X_FORWARDED_PROTO"}:
            return self._reject(start_response)
        return self.application(environ, start_response)
