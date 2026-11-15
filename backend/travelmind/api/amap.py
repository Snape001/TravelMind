import asyncio
import json
import re

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from travelmind.api.dependencies import SignedIn, api_error
from travelmind.research.http import MAX_RESPONSE_BYTES

router = APIRouter(prefix="/_AMapService", include_in_schema=False)
_UPSTREAMS = {
    "v3/log/init": "https://restapi.amap.com/v3/log/init",
    "v4/map/styles": "https://webapi.amap.com/v4/map/styles",
}
_HEADERS = {
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


def _js_settings(request: Request) -> tuple[str, str]:
    if request.headers.get("sec-fetch-site") not in (None, "same-origin"):
        raise api_error(403, "AMAP_ORIGIN_INVALID", "地图配置仅接受同源请求")
    settings = request.app.state.settings
    key = settings.next_public_amap_js_api_key
    code = settings.amap_security_js_code
    public_key = key.get_secret_value().strip() if key else ""
    security_code = code.get_secret_value().strip() if code else ""
    if not public_key or not security_code:
        raise api_error(
            503,
            "AMAP_JS_NOT_CONFIGURED",
            "请在后端配置 NEXT_PUBLIC_AMAP_JS_API_KEY 和 AMAP_SECURITY_JS_CODE",
        )
    return public_key, security_code


@router.get("/config")
async def map_config(request: Request, _: SignedIn) -> JSONResponse:
    key, _security_code = _js_settings(request)
    return JSONResponse({"js_api_key": key}, headers=_HEADERS)


@router.get("/{path:path}")
async def map_sdk_proxy(path: str, request: Request, _: SignedIn) -> Response:
    upstream_url = _UPSTREAMS.get(path)
    if upstream_url is None:
        raise api_error(404, "AMAP_PATH_UNKNOWN", "未提供此高德地图接口")
    key, security_code = _js_settings(request)
    params = dict(request.query_params)
    callback = params.pop("callback", None)
    if callback is not None and not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]{0,127}", callback):
        raise api_error(422, "AMAP_CALLBACK_INVALID", "地图回调参数无效")
    if params.get("key", key) != key:
        raise api_error(403, "AMAP_KEY_MISMATCH", "地图客户端使用了其他应用的 Key")
    params.pop("sig", None)
    params.update(key=key, jscode=security_code, output="JSON")
    origin = request.app.state.auth_settings.public_origin
    headers = {"Referer": f"{origin}/"} if origin else {}
    if "appname" in params:
        params["appname"] = origin or ""
    http: httpx.AsyncClient = request.app.state.amap_http
    try:
        body = bytearray()
        async with (
            asyncio.timeout(8),
            http.stream("GET", upstream_url, params=params, headers=headers) as response,
        ):
            if not response.is_success:
                raise ValueError
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ValueError
                body.extend(chunk)
        content = json.dumps(json.loads(body), ensure_ascii=True, allow_nan=False)
        if security_code in content:
            raise ValueError
    except (TimeoutError, httpx.HTTPError, ValueError, RecursionError):
        raise api_error(
            502,
            "AMAP_JS_UPSTREAM_ERROR",
            "无法读取高德地图服务响应",
            retryable=True,
        ) from None
    if callback is not None:
        return Response(
            f"{callback}({content});", media_type="application/javascript", headers=_HEADERS
        )
    return Response(content, media_type="application/json", headers=_HEADERS)
