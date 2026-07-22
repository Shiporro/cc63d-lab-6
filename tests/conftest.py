"""Fixtures compartidas de pytest para PostgreSQL."""

import os

import pytest

# Variables para PostgreSQL de pruebas.
# En Cloud Build puedes usar un PostgreSQL local levantado en el paso de test.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_NAME", "cc63d_test")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", "postgres")
os.environ["FLAKY_ERROR_RATE"] = "0"

import app as appmodule  # noqa: E402


def limpiar_tablas():
    db = appmodule.get_db()
    cur = db.cursor()

    try:
        cur.execute("""
            TRUNCATE TABLE
                postmortems,
                incident_timeline,
                incidents,
                oncall,
                services
            RESTART IDENTITY CASCADE
        """)
        db.commit()
    finally:
        cur.close()


@pytest.fixture
def client():
    appmodule.app.config["TESTING"] = True

    with appmodule.app.app_context():
        appmodule.init_db()
        limpiar_tablas()

    with appmodule.app.test_client() as c:
        yield c


def crear_servicio(client, name="payments-api", team="payments"):
    """Helper: crea un servicio y devuelve su id."""
    resp = client.post("/services", json={"name": name, "team": team})
    return resp.get_json()["id"]

