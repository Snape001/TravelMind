"""Check Amap Web Service connectivity against the official HTTPS API."""

import argparse
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import ValidationError

from travelmind.config import Settings
from travelmind.schemas.phase4_intake import Gcj02Point
from travelmind.server_logging import amap_log_filter

_ORIGIN = "https://restapi.amap.com"


class ProbeFailure(Exception):
    pass


def _get(http: httpx.Client, key: str, path: str, **params: str) -> dict:
    try:
        response = http.get(_ORIGIN + path, params={**params, "key": key}, follow_redirects=False)
        if not response.is_success:
            raise ProbeFailure(f"HTTP {response.status_code}")
        payload = response.json()
    except httpx.TimeoutException:
        raise ProbeFailure("请求超时") from None
    except httpx.RequestError:
        raise ProbeFailure("HTTPS 连接失败，请检查网络、代理和证书") from None
    except (ValueError, RecursionError):
        raise ProbeFailure("高德未返回有效 JSON") from None
    if not isinstance(payload, dict):
        raise ProbeFailure("高德返回了未知响应结构")
    if payload.get("status") not in ("1", 1):
        code = str(payload.get("infocode", ""))
        detail = f"（infocode {code}）" if re.fullmatch(r"[0-9]{5}", code) else ""
        raise ProbeFailure(f"高德拒绝请求{detail}，请检查 Web 服务 Key、权限及配额")
    return payload


def _poi(http: httpx.Client, key: str, name: str) -> str:
    response = _get(http, key, "/v5/place/text", keywords=name, region="010", city_limit="true")
    places = response.get("pois")
    for item in places if isinstance(places, list) else []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        location = item.get("location")
        if not isinstance(location, str):
            continue
        try:
            longitude, latitude = map(float, location.split(","))
            point = Gcj02Point(longitude=longitude, latitude=latitude)
        except ValueError:
            continue
        return f"{point.longitude:.6f},{point.latitude:.6f}"
    raise ProbeFailure("未返回带 POI ID 和合法坐标的地点")


def _tips(http: httpx.Client, key: str) -> None:
    response = _get(
        http,
        key,
        "/v3/assistant/inputtips",
        keywords="天安门",
        city="010",
        datatype="poi",
    )
    tips = response.get("tips")
    if not isinstance(tips, list) or not any(
        isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()
        for item in tips
    ):
        raise ProbeFailure("未返回有效的目的地输入提示")


def _driving(http: httpx.Client, key: str, start: str, end: str) -> None:
    response = _get(
        http,
        key,
        "/v5/direction/driving",
        origin=start,
        destination=end,
        show_fields="cost",
    )
    route = response.get("route")
    paths = route.get("paths") if isinstance(route, dict) else None
    if isinstance(paths, list):
        for path in paths:
            cost = path.get("cost") if isinstance(path, dict) else None
            if not isinstance(cost, dict):
                continue
            distance, duration = str(path.get("distance", "")), str(cost.get("duration", ""))
            if all(
                re.fullmatch(r"[0-9]{1,12}", value) and int(value) > 0
                for value in (distance, duration)
            ):
                return
    raise ProbeFailure("未返回含有效距离和预计耗时的可用路线")


def _check(name: str, operation: Callable[[], object]) -> bool:
    try:
        operation()
    except ProbeFailure as exc:
        print(f"FAIL {name} · {exc}", file=sys.stderr)
        return False
    print(f"PASS {name}")
    return True


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        configured = Settings(
            _env_file=Path(__file__).resolve().parents[2] / ".env"
        ).amap_web_api_key
    except ValidationError:
        print("无法读取 backend/.env，请检查环境变量格式。", file=sys.stderr)
        return 2
    key = configured.get_secret_value().strip() if configured else ""
    if not key or key == "【我填写】":
        print("请先在 backend/.env 填写 AMAP_WEB_API_KEY。", file=sys.stderr)
        return 2

    logging.getLogger("httpx").addFilter(amap_log_filter)
    with httpx.Client(timeout=10) as http:
        try:
            start = _poi(http, key, "天安门广场")
            end = _poi(http, key, "天坛公园")
            if start == end:
                raise ProbeFailure("两次 POI 搜索返回了相同坐标")
        except ProbeFailure as exc:
            print(f"FAIL POI 搜索 · {exc}", file=sys.stderr)
            points_found = False
        else:
            print("PASS POI 搜索")
            points_found = True
        tips_found = _check("目的地输入提示", lambda: _tips(http, key))
        if not points_found:
            print("SKIP 驾车路径规划 · 需要两个有效 POI", file=sys.stderr)
            return 1
        route_found = _check("驾车路径规划", lambda: _driving(http, key, start, end))
    return 0 if tips_found and route_found else 1


if __name__ == "__main__":
    raise SystemExit(main())
