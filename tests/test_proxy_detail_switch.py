import httpx
import pytest
from sqlalchemy import select

from app.models import Provider, RequestLog
from app.services import log_setting_service, proxy_service


@pytest.fixture(autouse=True)
def _reset_cache():
    log_setting_service.reset_cache()
    yield
    log_setting_service.reset_cache()


def _patch_httpx(monkeypatch, response_body, status_code=200):
    """让 proxy_service 内的 httpx.AsyncClient 走 MockTransport。

    必须保存原始 AsyncClient 引用：直接 patch 的是全局 httpx.AsyncClient，
    factory 内若再写 httpx.AsyncClient 会递归调用自己。
    """
    transport = httpx.MockTransport(
        lambda req: httpx.Response(status_code, json=response_body)
    )
    original_async_client = httpx.AsyncClient

    def factory(**kwargs):
        return original_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(proxy_service.httpx, "AsyncClient", factory)


async def _seed_provider(db):
    p = Provider(name="t", base_url="http://x/api", api_key="k")
    db.add(p)
    await db.commit()
    return p


async def test_non_stream_skips_details_when_disabled(db, session_maker, monkeypatch):
    await log_setting_service.set_log_detail_enabled(db, False)
    _patch_httpx(monkeypatch, {
        "id": "msg", "type": "message",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })
    provider = await _seed_provider(db)
    monkeypatch.setattr(proxy_service, "async_session", session_maker)

    body, code = await proxy_service.proxy_non_stream(
        provider, {"model": "m", "x": 1}, "127.0.0.1"
    )

    assert code == 200
    log = (await db.execute(select(RequestLog))).scalar_one()
    # 详情字段未落库
    assert log.request_body is None
    assert log.response_body is None
    # 用量列与状态码正常
    assert log.input_tokens == 10
    assert log.output_tokens == 5
    assert log.status_code == 200


async def test_non_stream_stores_details_when_enabled(db, session_maker, monkeypatch):
    await log_setting_service.set_log_detail_enabled(db, True)
    resp = {
        "id": "msg", "type": "message",
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }
    _patch_httpx(monkeypatch, resp)
    provider = await _seed_provider(db)
    monkeypatch.setattr(proxy_service, "async_session", session_maker)

    await proxy_service.proxy_non_stream(
        provider, {"model": "m", "x": 1}, "127.0.0.1"
    )

    log = (await db.execute(select(RequestLog))).scalar_one()
    assert log.request_body == {"model": "m", "x": 1}
    assert log.response_body == resp
    assert log.input_tokens == 7
    assert log.output_tokens == 3
