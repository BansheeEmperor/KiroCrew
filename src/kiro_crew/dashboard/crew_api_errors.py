"""The Crew API's error body, request id and version header, for every ``/v1/`` answer.

A leaf module with no dashboard imports, so token auth can answer a ``/v1/`` refusal
in the same shape as an Owner route does without loading the handlers.

The error body is ``{"error": {"type", "code", "message", "param", "request_id"}}``.
"""

from __future__ import annotations

import json
import re
import uuid

from aiohttp import web

CREW_API_PATH_PREFIX = "/v1/"

# shortcut: one fixed server version, and the request's own Crew-API-Version header is
# not read yet. Upgrade when the minor-version format is settled and a second minor
# version exists to negotiate between.
CREW_API_VERSION = "1.0"
VERSION_HEADER = "Crew-API-Version"
REQUEST_ID_HEADER = "X-Request-Id"

_REQUEST_ID_RE = re.compile(r"^req_[A-Za-z0-9_-]{1,64}$")

_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    409: "conflict_error",
    410: "resync_required_error",
    429: "rate_limit_error",
}
# The code an error gets when the handler's body names none.
_DEFAULT_CODES = {
    400: "invalid_request",
    401: "invalid_token",
    403: "owner_only",
    404: "not_found",
    429: "rate_limited",
}


def is_crew_api_path(path: str) -> bool:
    """Whether *path* is answered under the Crew API's contract."""
    return path.startswith(CREW_API_PATH_PREFIX)


def request_id_for(request: web.Request) -> str:
    """The caller's ``req_``-shaped ``X-Request-Id``, else a new one."""
    supplied = request.headers.get(REQUEST_ID_HEADER, "")
    if _REQUEST_ID_RE.fullmatch(supplied):
        return supplied
    return "req_" + uuid.uuid4().hex


def error_type(status: int) -> str:
    if status >= 500:
        return "server_error"
    return _ERROR_TYPES.get(status, "invalid_request_error")


def default_code(status: int) -> str:
    if status >= 500:
        return "internal_error"
    return _DEFAULT_CODES.get(status, "invalid_request")


def contract_error(
    status: int,
    code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
    *,
    type_: str = "",
) -> web.Response:
    """A Crew API error response, with its request id and version headers set."""
    body = {
        "error": {
            "type": type_ or error_type(status),
            "code": code,
            "message": message,
            "param": None,
            "request_id": request_id,
        }
    }
    # The code is at ``error.code``. The error-code ratchet
    # (test_error_code_contract.py) reads only a top-level ``code`` on a
    # ``json_response``, so this sink is outside it;
    # test_crew_api_owner.py pins the code on every error instead.
    response = web.Response(
        text=json.dumps(body),
        status=status,
        content_type="application/json",
        headers=headers,
    )
    response.headers[REQUEST_ID_HEADER] = request_id
    response.headers[VERSION_HEADER] = CREW_API_VERSION
    return response
