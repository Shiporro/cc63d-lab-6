"""Tests de integración: el flujo completo de la app y los endpoints de operación.

A diferencia de los tests de reglas (que verifican una invariante cada uno),
estos recorren el camino que haría un usuario real de punta a punta.
"""
from conftest import crear_servicio


def test_health_responde_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_metrics_expone_formato_prometheus(client):
    # generamos algo de tráfico para que haya métricas
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body


def test_flujo_completo_incidente_a_postmortem(client):
    # 1. servicio
    sid = crear_servicio(client, name="checkout", team="payments")

    # 2. turno de on-call que cubre hoy
    oncall = {
        "service_id": sid, "person": "Ana", "email": "ana@x.cl",
        "start_date": "2020-01-01", "end_date": "2999-12-31",
    }
    assert client.post("/oncall", json=oncall).status_code == 201

    # 3. incidente → debe notificar al on-call vigente
    creado = client.post(
        "/incidents", json={"title": "pagos caídos", "service_id": sid, "severity": 1}
    ).get_json()
    iid = creado["id"]
    assert creado["notification"]["person"] == "Ana"

    # 4. la línea de tiempo registra creación y notificación
    incidente = client.get(f"/incidents/{iid}").get_json()
    assert len(incidente["timeline"]) >= 2

    # 5. resolver y publicar post-mortem
    client.patch(f"/incidents/{iid}", json={"status": "resolved", "author": "ana"})
    pm = client.post("/postmortems", json={
        "incident_id": iid, "summary": "s", "root_cause": "deploy malo",
        "impact": "30 min sin pagos", "action_items": "rollback automático",
    })
    assert pm.status_code == 201


def test_listar_incidentes_filtra_por_status(client):
    sid = crear_servicio(client)
    abierto = client.post(
        "/incidents", json={"title": "a", "service_id": sid, "severity": 2}
    ).get_json()["id"]
    otro = client.post(
        "/incidents", json={"title": "b", "service_id": sid, "severity": 3}
    ).get_json()["id"]
    client.patch(f"/incidents/{otro}", json={"status": "resolved"})

    abiertos = client.get("/incidents?status=open").get_json()
    ids = [i["id"] for i in abiertos]
    assert abierto in ids
    assert otro not in ids
