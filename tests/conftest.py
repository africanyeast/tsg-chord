import os

# Must be set before app modules build the engine.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ADMIN_BEARER_TOKEN"] = "test-admin"
os.environ["MCP_BEARER_TOKEN"] = "test-front"
os.environ["MCP_OWNER_BEARER_TOKEN"] = "test-owner"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
# Tests drive the reconciler with run_once; the background loop would call Paystack.
os.environ["RECONCILER_ENABLED"] = "false"

import pytest
from sqlalchemy.pool import StaticPool

import app.db as db
from app import models  # noqa: F401
from app.db import Base

db.engine.dispose()
db.engine = db.create_engine(
    os.environ["DATABASE_URL"], connect_args={"check_same_thread": False}, poolclass=StaticPool
)
db.SessionLocal.configure(bind=db.engine)


@pytest.fixture(autouse=True)
def schema():
    Base.metadata.create_all(db.engine)
    yield
    Base.metadata.drop_all(db.engine)


@pytest.fixture
def session():
    with db.SessionLocal() as s:
        yield s


@pytest.fixture(scope="session")
def client():
    # The MCP session manager's lifespan can only run once per process.
    from starlette.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
