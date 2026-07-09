import os
import random
import psycopg2
import psycopg2.extras
import time
from datetime import datetime, timezone

from flask import Flask, Response, g, jsonify, request, send_from_directory
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

app = Flask(__name__, static_folder="static")
DATABASE = os.environ.get("DATABASE_PATH", "data/incidents.db")


# ============================================================================
#  Instrumentación SRE — métricas Prometheus (los SLIs viven aquí)
# ============================================================================
#
#   - Counter   : solo sube. Total de requests, por método, endpoint y status.
#                 Con esto calculamos disponibilidad: (no-5xx) / total.
#   - Histogram : agrupa en buckets. Permite percentiles (p50/p95/p99) en PromQL.
#
#  Aunque sea un monolito, los SLIs se miden igual que en un sistema distribuido.
# ----------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total de requests HTTP procesadas",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Latencia de las requests HTTP en segundos",
    ["endpoint"],
)

DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Latencia de las consultas a la base de datos en segundos",
)

def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(
            host=os.environ["DB_HOST"],
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def _start_timer():
    g._start_time = time.perf_counter()


@app.after_request
def _record_metrics(response):
    # No medimos el propio /metrics: lo consulta Prometheus, no el usuario.
    if request.endpoint == "metrics":
        return response
    # request.endpoint (nombre de la vista), NO request.path: así "/incidents/1"
    # y "/incidents/2" caen en el mismo label y no explota la cardinalidad.
    endpoint = request.endpoint or "unknown"
    elapsed = time.perf_counter() - getattr(g, "_start_time", time.perf_counter())
    REQUEST_LATENCY.labels(endpoint=endpoint).observe(elapsed)
    REQUEST_COUNT.labels(
        method=request.method, endpoint=endpoint, status=response.status_code
    ).inc()
    return response


@app.route("/metrics")
def metrics():
    # Prometheus hace scrape (modelo pull) a este endpoint en formato de texto.
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def init_db():
    db = get_db()
    cur = db.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            team TEXT NOT NULL,
            slo_target REAL NOT NULL DEFAULT 99.9,
            sli_type TEXT NOT NULL DEFAULT 'availability',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS oncall (
            id SERIAL PRIMARY KEY,
            service_id INTEGER NOT NULL REFERENCES services(id),
            person TEXT NOT NULL,
            email TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS incidents (
            id SERIAL PRIMARY KEY,
            service_id INTEGER NOT NULL REFERENCES services(id),
            title TEXT NOT NULL,
            severity INTEGER NOT NULL CHECK(severity BETWEEN 1 AND 4),
            status TEXT NOT NULL DEFAULT 'open',
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT,
            created_by TEXT NOT NULL DEFAULT 'system'
        );

        CREATE TABLE IF NOT EXISTS incident_timeline (
            id SERIAL PRIMARY KEY,
            incident_id INTEGER NOT NULL REFERENCES incidents(id),
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            author TEXT NOT NULL,
            message TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS postmortems (
            id SERIAL PRIMARY KEY,
            incident_id INTEGER NOT NULL UNIQUE REFERENCES incidents(id),
            summary TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            impact TEXT NOT NULL,
            action_items TEXT NOT NULL,
            lessons TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    db.commit()
    cur.close()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Frontend ---

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# --- Health ---

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# --- Endpoint del laboratorio: quema error budget de forma controlada ---
#
# Falla con código 500 una fracción de las veces (FLAKY_ERROR_RATE, default 5%).
# Sirve para ver cómo un endpoint poco fiable consume el presupuesto de error.
# En una app real esto NO existe: aquí genera fallas reproducibles para el lab.

FLAKY_ERROR_RATE = float(os.environ.get("FLAKY_ERROR_RATE", "0.05"))


@app.route("/work")
def work():
    if random.random() < FLAKY_ERROR_RATE:
        return jsonify({"error": "simulated failure"}), 500
    return jsonify({"status": "done"})


# --- Services ---

@app.route("/services", methods=["GET"])
def list_services():
    db = get_db()
    cur = db.cursor()

    try:
        cur.execute("SELECT * FROM services ORDER BY name")
        rows = cur.fetchall()

        return jsonify(rows)

    finally:
        cur.close()


@app.route("/services", methods=["POST"])
def create_service():
    data = request.json

    if not data or not data.get("name") or not data.get("team"):
        return jsonify({"error": "name and team are required"}), 400

    db = get_db()
    cur = db.cursor()

    try:
        cur.execute(
            """
            INSERT INTO services (name, team, slo_target, sli_type)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (
                data["name"],
                data["team"],
                data.get("slo_target", 99.9),
                data.get("sli_type", "availability"),
            ),
        )

        service_id = cur.fetchone()["id"]

        db.commit()

        return jsonify({"id": service_id}), 201

    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({"error": f"service '{data['name']}' already exists"}), 409

    finally:
        cur.close()


@app.route("/services/<int:service_id>", methods=["GET"])
def get_service(service_id):
    db = get_db()
    cur = db.cursor()

    try:
        cur.execute(
            "SELECT * FROM services WHERE id = %s",
            (service_id,),
        )

        row = cur.fetchone()

        if not row:
            return jsonify({"error": "service not found"}), 404

        return jsonify(row)

    finally:
        cur.close()


# --- On-Call ---

@app.route("/oncall", methods=["GET"])
def list_oncall():
    db = get_db()
    cur = db.cursor()

    try:
        cur.execute("""
            SELECT o.*, s.name AS service_name
            FROM oncall o
            JOIN services s ON o.service_id = s.id
            ORDER BY o.start_date DESC
        """)

        rows = cur.fetchall()

        return jsonify(rows)

    finally:
        cur.close()


@app.route("/oncall", methods=["POST"])
def create_oncall():
    data = request.json

    if not data or not all(k in data for k in ("service_id", "person", "email", "start_date", "end_date")):
        return jsonify({"error": "service_id, person, email, start_date and end_date are required"}), 400

    db = get_db()
    cur = db.cursor()

    try:
        cur.execute(
            "SELECT id FROM services WHERE id = %s",
            (data["service_id"],),
        )
        service = cur.fetchone()

        if not service:
            return jsonify({"error": "service not found"}), 404

        cur.execute(
            """
            INSERT INTO oncall (service_id, person, email, start_date, end_date)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                data["service_id"],
                data["person"],
                data["email"],
                data["start_date"],
                data["end_date"],
            ),
        )

        oncall_id = cur.fetchone()["id"]
        db.commit()

        return jsonify({"id": oncall_id}), 201

    finally:
        cur.close()


@app.route("/oncall/current/<int:service_id>", methods=["GET"])
def get_current_oncall(service_id):
    db = get_db()
    cur = db.cursor()

    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        cur.execute(
            """
            SELECT *
            FROM oncall
            WHERE service_id = %s
              AND start_date <= %s
              AND end_date >= %s
            LIMIT 1
            """,
            (service_id, today, today),
        )

        row = cur.fetchone()

        if not row:
            return jsonify({"error": "no one on-call for this service today"}), 404

        return jsonify(row)

    finally:
        cur.close()


# --- Incidents ---

@app.route("/incidents", methods=["GET"])
def list_incidents():
    db = get_db()
    cur = db.cursor()

    try:
        status = request.args.get("status")

        if status:
            cur.execute("""
                SELECT i.*, s.name AS service_name
                FROM incidents i
                JOIN services s ON i.service_id = s.id
                WHERE i.status = %s
                ORDER BY i.started_at DESC
            """, (status,))
        else:
            cur.execute("""
                SELECT i.*, s.name AS service_name
                FROM incidents i
                JOIN services s ON i.service_id = s.id
                ORDER BY i.started_at DESC
            """)

        rows = cur.fetchall()

        return jsonify(rows)

    finally:
        cur.close()


@app.route("/incidents", methods=["POST"])
def create_incident():
    data = request.json

    if not data or not data.get("title") or not data.get("service_id") or not data.get("severity"):
        return jsonify({"error": "title, service_id and severity are required"}), 400

    if data["severity"] not in (1, 2, 3, 4):
        return jsonify({"error": "severity must be between 1 and 4"}), 400

    db = get_db()
    cur = db.cursor()

    try:
        cur.execute(
            "SELECT * FROM services WHERE id = %s",
            (data["service_id"],),
        )
        service = cur.fetchone()

        if not service:
            return jsonify({"error": "service not found"}), 404

        ts = now()

        cur.execute(
            """
            INSERT INTO incidents (
                service_id, title, severity, status, started_at, created_by
            )
            VALUES (%s, %s, %s, 'open', %s, %s)
            RETURNING id
            """,
            (
                data["service_id"],
                data["title"],
                data["severity"],
                ts,
                data.get("created_by", "system"),
            ),
        )

        incident_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO incident_timeline (
                incident_id, timestamp, author, message
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                incident_id,
                ts,
                data.get("created_by", "system"),
                f"Incident created: {data['title']}",
            ),
        )

        today = datetime.now(timezone.utc).date()

        cur.execute(
            """
            SELECT *
            FROM oncall
            WHERE service_id = %s
              AND start_date <= %s
              AND end_date >= %s
            LIMIT 1
            """,
            (data["service_id"], today, today),
        )
        oncall = cur.fetchone()

        notification = None

        if oncall:
            notification = {
                "person": oncall["person"],
                "email": oncall["email"],
                "message": f"[SEV{data['severity']}] {data['title']} on {service['name']}",
            }

            cur.execute(
                """
                INSERT INTO incident_timeline (
                    incident_id, timestamp, author, message
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    incident_id,
                    ts,
                    "system",
                    f"Notified {oncall['person']} ({oncall['email']})",
                ),
            )

            app.logger.info(
                f"NOTIFICATION: {notification['message']} -> {oncall['person']} <{oncall['email']}>"
            )

        else:
            cur.execute(
                """
                INSERT INTO incident_timeline (
                    incident_id, timestamp, author, message
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    incident_id,
                    ts,
                    "system",
                    "WARNING: No on-call found for this service",
                ),
            )

            app.logger.warning(f"No on-call found for service {service['name']}")

        db.commit()

        return jsonify({"id": incident_id, "notification": notification}), 201

    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({"error": "failed to create incident"}), 400

    finally:
        cur.close()


@app.route("/incidents/<int:incident_id>", methods=["GET"])
def get_incident(incident_id):
    db = get_db()
    cur = db.cursor()

    try:
        cur.execute("""
            SELECT i.*, s.name AS service_name, s.team
            FROM incidents i
            JOIN services s ON i.service_id = s.id
            WHERE i.id = %s
        """, (incident_id,))

        incident = cur.fetchone()

        if not incident:
            return jsonify({"error": "incident not found"}), 404

        cur.execute(
            """
            SELECT *
            FROM incident_timeline
            WHERE incident_id = %s
            ORDER BY timestamp
            """,
            (incident_id,),
        )

        timeline = cur.fetchall()

        result = incident
        result["timeline"] = timeline

        return jsonify(result)

    finally:
        cur.close()


@app.route("/incidents/<int:incident_id>", methods=["PATCH"])
def update_incident(incident_id):
    data = request.json

    if not data:
        return jsonify({"error": "request body required"}), 400

    db = get_db()
    cur = db.cursor()

    try:
        cur.execute(
            "SELECT * FROM incidents WHERE id = %s",
            (incident_id,),
        )
        incident = cur.fetchone()

        if not incident:
            return jsonify({"error": "incident not found"}), 404

        ts = now()
        author = data.get("author", "system")

        if "status" in data:
            new_status = data["status"]

            if new_status not in ("open", "investigating", "mitigated", "resolved"):
                return jsonify({"error": "invalid status"}), 400

            resolved_at = ts if new_status == "resolved" else None

            cur.execute(
                """
                UPDATE incidents
                SET status = %s,
                    resolved_at = COALESCE(%s, resolved_at)
                WHERE id = %s
                """,
                (new_status, resolved_at, incident_id),
            )

            cur.execute(
                """
                INSERT INTO incident_timeline (
                    incident_id, timestamp, author, message
                )
                VALUES (%s, %s, %s, %s)
                """,
                (incident_id, ts, author, f"Status changed to {new_status}"),
            )

        if "message" in data:
            cur.execute(
                """
                INSERT INTO incident_timeline (
                    incident_id, timestamp, author, message
                )
                VALUES (%s, %s, %s, %s)
                """,
                (incident_id, ts, author, data["message"]),
            )

        db.commit()

        return jsonify({"ok": True})

    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({"error": "failed to update incident"}), 400

    finally:
        cur.close()


# --- Post-Mortems ---

@app.route("/postmortems", methods=["POST"])
def create_postmortem():
    data = request.json
    required = ("incident_id", "summary", "root_cause", "impact", "action_items")

    if not data or not all(k in data for k in required):
        return jsonify({"error": f"{', '.join(required)} are required"}), 400

    db = get_db()
    cur = db.cursor()

    try:
        # Verificar que el incidente exista
        cur.execute(
            "SELECT * FROM incidents WHERE id = %s",
            (data["incident_id"],),
        )
        incident = cur.fetchone()

        if not incident:
            return jsonify({"error": "incident not found"}), 404

        if incident["status"] != "resolved":
            return jsonify({"error": "incident must be resolved before writing a post-mortem"}), 400

        # Crear el post-mortem
        cur.execute(
            """
            INSERT INTO postmortems (
                incident_id,
                summary,
                root_cause,
                impact,
                action_items,
                lessons
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                data["incident_id"],
                data["summary"],
                data["root_cause"],
                data["impact"],
                data["action_items"],
                data.get("lessons", ""),
            ),
        )

        postmortem_id = cur.fetchone()["id"]

        # Agregar evento a la línea de tiempo
        ts = now()

        cur.execute(
            """
            INSERT INTO incident_timeline (
                incident_id,
                timestamp,
                author,
                message
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                data["incident_id"],
                ts,
                data.get("author", "system"),
                "Post-mortem published",
            ),
        )

        db.commit()

        return jsonify({"id": postmortem_id}), 201

    except psycopg2.IntegrityError:
        db.rollback()
        return jsonify({"error": "post-mortem already exists for this incident"}), 409

    finally:
        cur.close()


@app.route("/postmortems/<int:postmortem_id>", methods=["GET"])
def get_postmortem(postmortem_id):
    db = get_db()
    cur = db.cursor()

    try:
        cur.execute("""
            SELECT
                p.*,
                i.title AS incident_title,
                i.severity,
                s.name AS service_name
            FROM postmortems p
            JOIN incidents i ON p.incident_id = i.id
            JOIN services s ON i.service_id = s.id
            WHERE p.id = %s
        """, (postmortem_id,))

        row = cur.fetchone()

        if not row:
            return jsonify({"error": "post-mortem not found"}), 404

        return jsonify(row)

    finally:
        cur.close()


@app.route("/postmortems", methods=["GET"])
def list_postmortems():
    db = get_db()
    cur = db.cursor()

    try:
        cur.execute("""
            SELECT
                p.*,
                i.title AS incident_title,
                i.severity,
                s.name AS service_name
            FROM postmortems p
            JOIN incidents i ON p.incident_id = i.id
            JOIN services s ON i.service_id = s.id
            ORDER BY p.created_at DESC
        """)

        rows = cur.fetchall()

        return jsonify(rows)

    finally:
        cur.close()


# El schema se crea al arrancar (idempotente). En la versión distribuida esto lo
# hace Flyway; aquí, el monolito crea el esquema de forma idempotente al importarse
# (gunicorn no ejecuta el bloque __main__, así que init_db va a nivel de módulo).
with app.app_context():
    init_db()


if __name__ == "__main__":
    # Solo para desarrollo local. En contenedor corre gunicorn (ver Dockerfile).
    app.run(host="0.0.0.0", port=8000, debug=True)
