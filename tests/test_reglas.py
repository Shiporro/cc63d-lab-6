"""Tests unitarios de las reglas de negocio del dominio de incidentes.

Verifican que la app hace cumplir las invariantes del modelo: severidades
válidas, unicidad de servicios, y el ciclo de vida incidente → post-mortem.
"""
from conftest import crear_servicio


def test_severidad_fuera_de_rango_se_rechaza(client):
    sid = crear_servicio(client)
    # severidad válida: 1..4
    for sev in (0, 5, 99):
        resp = client.post("/incidents", json={"title": "x", "service_id": sid, "severity": sev})
        assert resp.status_code == 400


def test_severidad_valida_se_acepta(client):
    sid = crear_servicio(client)
    resp = client.post("/incidents", json={"title": "DB caída", "service_id": sid, "severity": 1})
    assert resp.status_code == 201
    assert "id" in resp.get_json()


def test_servicio_duplicado_devuelve_409(client):
    crear_servicio(client, name="auth")
    resp = client.post("/services", json={"name": "auth", "team": "identity"})
    assert resp.status_code == 409


def test_servicio_requiere_nombre_y_equipo(client):
    assert client.post("/services", json={"name": "x"}).status_code == 400
    assert client.post("/services", json={"team": "y"}).status_code == 400


def test_incidente_en_servicio_inexistente_da_404(client):
    resp = client.post("/incidents", json={"title": "x", "service_id": 999, "severity": 2})
    assert resp.status_code == 404


def test_postmortem_requiere_incidente_resuelto(client):
    sid = crear_servicio(client)
    iid = client.post(
        "/incidents", json={"title": "outage", "service_id": sid, "severity": 1}
    ).get_json()["id"]

    # incidente abierto → no se permite post-mortem
    pm = {
        "incident_id": iid, "summary": "s", "root_cause": "rc",
        "impact": "i", "action_items": "ai",
    }
    assert client.post("/postmortems", json=pm).status_code == 400

    # tras resolver, sí se permite
    client.patch(f"/incidents/{iid}", json={"status": "resolved"})
    assert client.post("/postmortems", json=pm).status_code == 201


def test_resolver_incidente_setea_resolved_at(client):
    sid = crear_servicio(client)
    iid = client.post(
        "/incidents", json={"title": "x", "service_id": sid, "severity": 3}
    ).get_json()["id"]

    client.patch(f"/incidents/{iid}", json={"status": "resolved"})
    incidente = client.get(f"/incidents/{iid}").get_json()
    assert incidente["status"] == "resolved"
    assert incidente["resolved_at"] is not None


def test_status_invalido_se_rechaza(client):
    sid = crear_servicio(client)
    iid = client.post(
        "/incidents", json={"title": "x", "service_id": sid, "severity": 2}
    ).get_json()["id"]
    assert client.patch(f"/incidents/{iid}", json={"status": "explotando"}).status_code == 400
