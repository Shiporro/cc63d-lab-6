terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

# ---------------------------------------------------------
# Configuración del proveedor de Google Cloud
# ---------------------------------------------------------

provider "google" {
  project = "monolito-501911"
  region  = "southamerica-west1"
}

# Obtiene información del proyecto, incluido su número.
data "google_project" "proyecto" {
  project_id = "monolito-501911"
}

# Lee el secreto existente, pero no lo vuelve a crear.
data "google_secret_manager_secret" "db_password" {
  secret_id = "db-password"
  project   = "monolito-501911"
}

# ---------------------------------------------------------
# Artifact Registry
# ---------------------------------------------------------

resource "google_artifact_registry_repository" "repo" {
  location      = "southamerica-west1"
  repository_id = "tarea-6"
  description   = "Repositorio Docker para imagenes del monolito"
  format        = "DOCKER"
}

# ---------------------------------------------------------
# Permisos de la cuenta de servicio de Cloud Run
# ---------------------------------------------------------

# Cuenta de servicio predeterminada utilizada por Cloud Run.
locals {
  cloud_run_service_account = "${data.google_project.proyecto.number}-compute@developer.gserviceaccount.com"
}

# Permite que Cloud Run se conecte a Cloud SQL.
resource "google_project_iam_member" "cloud_sql_client" {
  project = "monolito-501911"
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${local.cloud_run_service_account}"
}

# Permite que Cloud Run lea el secreto db-password.
resource "google_secret_manager_secret_iam_member" "secret_accessor" {
  project   = "monolito-501911"
  secret_id = data.google_secret_manager_secret.db_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.cloud_run_service_account}"
}

# ---------------------------------------------------------
# Servicio Cloud Run
# ---------------------------------------------------------

resource "google_cloud_run_v2_service" "monolito" {
  name     = "monolitonuebe"
  location = "southamerica-west1"

  deletion_protection = false

  lifecycle {
    ignore_changes = [
      scaling
    ]
  }

  template {
    # Cuenta de servicio utilizada por el contenedor.
    service_account = local.cloud_run_service_account

    # Declara la conexión con la instancia Cloud SQL.
    volumes {
      name = "cloudsql"

      cloud_sql_instance {
        instances = [
          "monolito-501911:southamerica-west1:monolito-postgres"
        ]
      }
    }

    containers {
      image = "southamerica-west1-docker.pkg.dev/monolito-501911/tarea-6/cc63d-lab-6:latest"

      ports {
        container_port = 8080
      }

      # Monta el socket de Cloud SQL dentro del contenedor.
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # Dirección del socket de PostgreSQL.
      env {
        name  = "DB_HOST"
        value = "/cloudsql/monolito-501911:southamerica-west1:monolito-postgres"
      }

      # Nombre de la base de datos existente.
      env {
        name  = "DB_NAME"
        value = "monolito_db"
      }

      # Usuario existente de PostgreSQL.
      env {
        name  = "DB_USER"
        value = "monolito_user"
      }

      # La contraseña se obtiene desde Secret Manager.
      env {
        name = "DB_PASSWORD"

        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  # Cloud Run se actualizará después de configurar los permisos.
  depends_on = [
    google_artifact_registry_repository.repo,
    google_project_iam_member.cloud_sql_client,
    google_secret_manager_secret_iam_member.secret_accessor
  ]
}

# ---------------------------------------------------------
# Acceso público a Cloud Run
# ---------------------------------------------------------

resource "google_cloud_run_v2_service_iam_member" "public_access" {
  project  = "monolito-501911"
  location = "southamerica-west1"
  name     = google_cloud_run_v2_service.monolito.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------
# Resultado mostrado después de terraform apply
# ---------------------------------------------------------

output "cloud_run_url" {
  description = "URL publica del servicio Cloud Run"
  value       = google_cloud_run_v2_service.monolito.uri
}