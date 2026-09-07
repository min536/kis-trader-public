from __future__ import annotations

import json
import os
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


# server.py lives at <repo>/workspace/claude-design/kis-trader-v2/server.py, so
# the repository root is three parents up. KIS_TRADER_ROOT stays as an explicit
# override (containers, worktrees); the old hardcoded "/workspace" default broke
# every local run — `app.*` imports failed and /api/kt-data answered 500.
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = Path(os.environ.get("KIS_TRADER_ROOT") or _DEFAULT_REPO_ROOT).resolve()
PROJECT_DIR = Path(__file__).resolve().parent / "project"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _active_account_context() -> dict[str, str]:
    try:
        from app.auth.account_scope import get_account_scope_context

        context = get_account_scope_context()
    except Exception:
        return {}
    return {
        "account_signature": str(context.get("account_signature") or "").strip(),
        "masked_account_display": str(
            context.get("masked_account_display") or ""
        ).strip(),
    }


def _active_account_signature() -> str | None:
    return _active_account_context().get("account_signature") or None


class UnknownSignatureError(ValueError):
    """Raised when a requested ?signature= is not among the discovered accounts.

    Carries ``known_signatures`` so the HTTP layer can answer 400 (bad request)
    with the valid options instead of a 500 the frontend reads as a crash (C5).
    """

    def __init__(self, requested: str, known_signatures: list[str]) -> None:
        super().__init__(f"Unknown account signature: {requested}")
        self.requested = requested
        self.known_signatures = known_signatures


def _selected_signature(requested_signature: str | None) -> tuple[str | None, list[dict[str, Any]]]:
    from app.dashboard.v2_account_options import build_v2_account_options

    active_context = _active_account_context()
    active_signature = active_context.get("account_signature") or None
    options = build_v2_account_options(
        active_signature=active_signature,
        active_masked_display=active_context.get("masked_account_display") or None,
    )
    allowed = {str(option.get("id")) for option in options}
    if requested_signature:
        if requested_signature not in allowed:
            raise UnknownSignatureError(requested_signature, sorted(allowed))
        selected = requested_signature
    else:
        selected = active_signature if active_signature in allowed else None
        if selected is None and options:
            selected = str(options[0].get("id"))
    for option in options:
        option["status"] = "active" if option.get("id") == selected else "available"
    return selected, options


def _signature_from_path(path: str) -> str | None:
    query = parse_qs(urlparse(path).query)
    values = query.get("signature") or []
    signature = values[0].strip() if values else ""
    return signature or None


def build_kt_data(*, signature: str | None = None) -> dict[str, Any]:
    from app.dashboard.data_loader import load_dashboard_data
    from app.dashboard.v2_site_payload import build_v2_site_payload

    selected_signature, account_options = _selected_signature(signature)
    data = (
        load_dashboard_data(signature=selected_signature)
        if selected_signature
        else load_dashboard_data()
    )
    return build_v2_site_payload(
        data,
        account_signature=selected_signature,
        account_options=account_options,
    )


def kt_data_or_error(signature: str | None) -> tuple[int, dict[str, Any]]:
    """Return ``(status, payload)`` for the /api/kt-data endpoint.

    Unknown signatures map to 400 with the known signatures (C5); any other
    failure stays a 500 handled by the caller.
    """
    try:
        return HTTPStatus.OK, build_kt_data(signature=signature)
    except UnknownSignatureError as exc:
        return HTTPStatus.BAD_REQUEST, {
            "error": str(exc),
            "known_signatures": exc.known_signatures,
        }


def build_health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "kis-trader-v2-site",
        "payload_builder": "app.dashboard.v2_site_payload.build_v2_site_payload",
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(PROJECT_DIR), **kwargs)

    def end_headers(self) -> None:
        # Dev console served from source .jsx/.css — never let the browser cache
        # a stale build. Without this, edits to the front-end files are masked by
        # heuristic caching until a manual hard-reload.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def do_GET(self) -> None:
        request_path = self.path.split("?", 1)[0]
        if request_path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/kis-trader%20Ops%20Console.html")
            self.end_headers()
            return
        if request_path == "/api/kt-data":
            status, payload = kt_data_or_error(_signature_from_path(self.path))
            self._write_json(status, payload)
            return
        if request_path == "/api/health":
            self._send_json(build_health_payload)
            return
        return super().do_GET()

    def _send_json(self, build_payload: Any) -> None:
        try:
            payload_obj = build_payload()
            status = HTTPStatus.OK
        except Exception as exc:
            payload_obj = {"error": str(exc)}
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._write_json(status, payload_obj)

    def _write_json(self, status: Any, payload_obj: Any) -> None:
        payload = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "4173"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving kis-trader ops console on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
