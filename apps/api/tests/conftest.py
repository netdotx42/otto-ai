from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://agent98:agent98@localhost:5432/agent98_test")
os.environ["ENV"] = "test"
os.environ["NETWORK_MODE"] = "testnet"
os.environ["BASE_PASS_ADDRESS"] = "0x9898989898989898989898989898989898989898"
os.environ["OPENAI_API_KEY"] = ""
os.environ["APP_DOMAIN"] = "localhost:3000"
os.environ["APP_ORIGIN"] = "http://localhost:3000"

from collections.abc import AsyncIterator  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_account.messages import encode_defunct  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.chains.registry import ChainClients, set_clients  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.ratelimit import set_redis  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import engine, reset_engine  # noqa: E402
from tests.fakes import FakeEvm, FakeHttp, FakeSolana  # noqa: E402

get_settings.cache_clear()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema() -> AsyncIterator[None]:
    await reset_engine()
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await reset_engine()


@pytest_asyncio.fixture(autouse=True)
async def _clean() -> AsyncIterator[None]:
    async with engine().begin() as conn:
        names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    set_redis(fakeredis.aioredis.FakeRedis(decode_responses=True))
    yield


@pytest.fixture
def chain() -> ChainClients:
    c = ChainClients(evm=FakeEvm(), solana=FakeSolana(), http=FakeHttp())  # type: ignore[arg-type]
    set_clients(c)
    return c


@pytest_asyncio.fixture
async def client(chain) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app

    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"origin": "http://localhost:3000"}) as c:
        yield c


class EvmUser:
    def __init__(self) -> None:
        self.acct = Account.create()
        self.address = self.acct.address.lower()

    def sign(self, message: str) -> str:
        return "0x" + self.acct.sign_message(encode_defunct(text=message)).signature.hex().removeprefix("0x")


async def login_evm(client: httpx.AsyncClient, user: EvmUser | None = None) -> EvmUser:
    user = user or EvmUser()
    r = await client.post("/v1/auth/nonce", json={"chain": "base", "address": user.address})
    assert r.status_code == 200, r.text
    n = r.json()
    r = await client.post("/v1/auth/verify", json={"chain": "base", "address": user.address, "nonce": n["nonce"], "signature": user.sign(n["message"])})
    assert r.status_code == 200, r.text
    return user


class SolUser:
    def __init__(self) -> None:
        from solders.keypair import Keypair

        self.kp = Keypair()
        self.address = str(self.kp.pubkey())

    def sign(self, message: str) -> str:
        import base58

        return base58.b58encode(bytes(self.kp.sign_message(message.encode()))).decode()


async def login_sol(client: httpx.AsyncClient, user: SolUser | None = None) -> SolUser:
    user = user or SolUser()
    r = await client.post("/v1/auth/nonce", json={"chain": "solana", "address": user.address})
    n = r.json()
    r = await client.post("/v1/auth/verify", json={"chain": "solana", "address": user.address, "nonce": n["nonce"], "signature": user.sign(n["message"])})
    assert r.status_code == 200, r.text
    return user


def now() -> datetime:
    return datetime.now(UTC)
