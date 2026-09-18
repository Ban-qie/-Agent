"""Opt-in project profile: close legacy execution surfaces before route handlers."""
import os


def restricted_mode():
    return os.environ.get("ECOMMERCE_RESTRICTED", "false").lower() == "true"


def deny_free_code():
    if restricted_mode():
        raise PermissionError("Free code execution is disabled in the ecommerce profile")


def install_profile(app):
    from flask import request
    from data_formulator.ecommerce.contracts import error_result
    if app.config.get('ECOMMERCE_PROFILE') == 'multiuser':
        # App-scoped, fail-closed entry. No wildcard auth or upstream routes.
        allowed_multiuser = {
            ('GET', '/api/ecommerce/auth/status'),
            ('POST', '/api/ecommerce/auth/login'),
            ('POST', '/api/ecommerce/auth/logout'),
        }
        @app.before_request
        def multiuser_guard():
            if 'v3_accounts' not in app.extensions:
                return error_result('AUTH_UNAVAILABLE', 'Authentication unavailable'), 503
            allowed = allowed_multiuser | ({('GET', '/api/ecommerce/workspace'), ('POST', '/api/ecommerce/analyze'),
                                            ('GET', '/api/ecommerce/catalog')}
                                          if 'v3_service' in app.extensions else set())
            if hasattr(app.extensions.get('v3_service'), 'submit'):
                import re
                if ((request.method == 'GET' and re.fullmatch(r'/api/ecommerce/tasks/[a-f0-9]{32}', request.path))
                        or (request.method == 'POST' and re.fullmatch(r'/api/ecommerce/tasks/[a-f0-9]{32}/cancel', request.path))):
                    return None
            if request.path.startswith('/api/') and (request.method, request.path) not in allowed:
                return error_result('TOOL_NOT_ALLOWED', 'Entry not enabled'), 403
        return
    # Diagnostics, fixed queries and the constrained Analyst path only.
    # The legacy UI execution APIs remain disabled until its V0-7 adaptation.
    allowed = {
        ("GET", "/api/app-config"), ("GET", "/api/auth/info"),
        ("GET", "/api/agent/list-global-models"), ("POST", "/api/agent/list-global-models"),
        ("GET", "/api/ecommerce/catalog"), ("POST", "/api/ecommerce/query"),
        ("POST", "/api/ecommerce/analyze"),
        ("GET", "/api/ecommerce/workspace"),
    }
    origins = {"http://127.0.0.1:5567", "http://localhost:5567", "http://127.0.0.1:5173", "http://localhost:5173"}
    hosts = {"localhost", "127.0.0.1", "[::1]"} | {
        f"{host}:{port}" for host in ("localhost", "127.0.0.1", "[::1]") for port in (5567, 5173)
    }
    @app.before_request
    def ecommerce_guard():
        if not restricted_mode():
            return None
        from data_formulator.auth.identity import is_local_mode
        if not is_local_mode() or request.remote_addr not in ("127.0.0.1", "::1"):
            return error_result("ACCESS_DENIED", "Ecommerce profile requires single-user loopback mode"), 403
        if request.host.lower() not in hosts:
            return error_result("ACCESS_DENIED", "Only the local application host is enabled"), 403
        if request.headers.get("Origin") and request.headers["Origin"] not in origins:
            return error_result("ACCESS_DENIED", "Cross-origin requests are not enabled"), 403
        if request.path.startswith("/api/") and (request.method, request.path) not in allowed:
            return error_result("TOOL_NOT_ALLOWED", "This entry is disabled in the restricted ecommerce profile"), 403
        if request.path in ("/api/ecommerce/query", "/api/ecommerce/analyze") and (request.content_length is None or request.content_length > 8192):
            return error_result("RESOURCE_LIMIT", "Query body must have a known size no larger than 8192 bytes"), 413
        if request.path in ("/api/ecommerce/query", "/api/ecommerce/analyze"):
            import json
            try:
                # WSGI framing remains the server's responsibility. A terminated
                # stream can expose more bytes than the declared Content-Length.
                raw = request.stream.read(8193)
                if len(raw) > 8192:
                    return error_result('RESOURCE_LIMIT', 'Query body exceeds 8192 bytes'), 413
                request._cached_data = raw
                json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeError, RecursionError):
                return error_result('INVALID_REQUEST', 'A bounded UTF-8 JSON request is required'), 400
    # Run before existing authentication/connector middleware can do work.
    app.before_request_funcs[None].insert(0, app.before_request_funcs[None].pop())
