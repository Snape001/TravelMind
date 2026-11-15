import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from travelmind.api.dependencies import current_session
from travelmind.api.oidc import AuthSettings
from travelmind.config import Settings
from travelmind.main import create_app
from travelmind.server_logging import amap_log_filter


def test_amap_dotenv_credentials_keep_the_js_config_public_key_only(tmp_path, monkeypatch):
    for name in ("AMAP_WEB_API_KEY", "NEXT_PUBLIC_AMAP_JS_API_KEY", "AMAP_SECURITY_JS_CODE"):
        monkeypatch.delenv(name, raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "AMAP_WEB_API_KEY=fixture-web\n"
        "NEXT_PUBLIC_AMAP_JS_API_KEY=fixture-browser\n"
        "AMAP_SECURITY_JS_CODE=fixture-security\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=dotenv, database_url=None, redis_url=None)
    assert settings.amap_web_api_key.get_secret_value() == "fixture-web"
    assert settings.amap_security_js_code.get_secret_value() == "fixture-security"
    assert "fixture-security" not in repr(settings)
    app = create_app(settings, auth_settings=AuthSettings(_env_file=None))
    app.dependency_overrides[current_session] = object
    with TestClient(app) as client:
        config = client.get("/_AMapService/config")
        assert config.status_code == 200
        assert config.headers["cache-control"] == "no-store"
        assert config.json() == {"js_api_key": "fixture-browser"}
        assert (
            client.get("/_AMapService/config", headers={"sec-fetch-site": "same-site"}).status_code
            == 403
        )
        assert client.get("/_AMapService/v3/place/text").status_code == 404
        mismatch = client.get("/_AMapService/v3/log/init", params={"key": "other-key"})
        assert mismatch.status_code == 403
        invalid_callback = client.get(
            "/_AMapService/v3/log/init", params={"callback": "not-a-callback()"}
        )
        assert invalid_callback.status_code == 422


def test_map_config_requires_web_js_key_and_security_code():
    app = create_app(
        Settings(
            _env_file=None,
            next_public_amap_js_api_key="fixture-browser",
            amap_security_js_code=None,
        ),
        auth_settings=AuthSettings(_env_file=None),
    )
    app.dependency_overrides[current_session] = object
    with TestClient(app) as client:
        response = client.get("/_AMapService/config")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "AMAP_JS_NOT_CONFIGURED"


def test_httpx_transport_log_redacts_amap_query():
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        "",
        0,
        "Request %s %s",
        ("GET", httpx.URL("https://restapi.amap.com/v5/place/text?key=fixture&jscode=secret")),
        None,
    )
    assert amap_log_filter.filter(record)
    assert record.getMessage() == "Request GET https://restapi.amap.com/v5/place/text"


def test_amap_cli_refuses_to_contact_supplier_with_empty_key():
    env = {name: value for name, value in os.environ.items() if name.upper() != "AMAP_WEB_API_KEY"}
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.NamedTemporaryFile(delete=False) as file:
        dotenv = Path(file.name)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "travelmind.research.check_amap", "--env-file", str(dotenv)],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            encoding="utf-8",
            timeout=10,
        )
    finally:
        dotenv.unlink()
    assert result.returncode == 2
    assert "AMAP_WEB_API_KEY" in result.stderr
    assert "PASS" not in result.stdout
