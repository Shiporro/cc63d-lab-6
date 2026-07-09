# cc63d-lab-6 · Monolito de gestión de incidentes

Código base del **Lab 7** del curso CC63D (Universidad de Chile, DCC Educación
Continua). Es una aplicación monolítica de gestión de incidentes: un único
proceso Flask que sirve **la API y la interfaz web** desde el mismo contenedor,
con **SQLite** como almacenamiento.

Tu tarea en el Lab 7 es llevar este monolito a la nube: desplegarlo en
**Cloud Run**, automatizar el despliegue con **Cloud Build** y describir la
infraestructura con **Terraform**. El enunciado completo, los entregables y la
rúbrica están en el material de la clase.

> Este repositorio contiene **solo la aplicación**. No incluye `cloudbuild.yaml`
> ni la configuración de Terraform: esos artefactos son parte de lo que debes
> producir.

## Qué hace la aplicación

Gestiona el ciclo de vida de incidentes de operación:

- **Servicios** con su objetivo de nivel de servicio (SLO).
- **Incidentes** con severidad, estado y una bitácora de eventos.
- **Turnos de guardia** (on-call) por servicio.
- **Post-mortems** asociados a cada incidente.

La interfaz web se sirve desde `static/` y consume la misma API. El esquema de
la base de datos se crea solo al arrancar (idempotente).

## Estructura

```
app.py               La aplicación completa (API + rutas que sirven la UI)
static/              Interfaz web (HTML, CSS, JavaScript)
tests/               Pruebas automáticas (pytest)
Dockerfile           Imagen del contenedor
requirements.txt     Dependencias de ejecución
requirements-dev.txt Dependencias de desarrollo (pytest, ruff)
seed.sh              Carga datos de ejemplo vía la API
```

## Ejecutar en local

### Con Docker (recomendado)

```bash
docker build -t incidentes .
docker run -p 8000:8000 incidentes
```

Abre <http://localhost:8000> en el navegador.

### Con Python

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

La aplicación crea el directorio `data/` y la base SQLite en el primer arranque.

### Cargar datos de ejemplo

Con la aplicación corriendo:

```bash
bash seed.sh                      # usa http://localhost:8000 por defecto
bash seed.sh https://TU-URL       # o apunta a tu servicio en Cloud Run
```

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest
```
