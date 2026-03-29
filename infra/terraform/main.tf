# =============================================================================
# Terraform — GCP Infrastructure for Autonomous ETL Agent
# =============================================================================

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
  # Uncomment for remote state in production:
  # backend "gcs" {
  #   bucket = "<your-tfstate-bucket>"
  #   prefix = "etl-agent/terraform"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ---------------------------------------------------------------------------
# Enable required APIs
# ---------------------------------------------------------------------------

resource "google_project_service" "required_apis" {
  for_each = toset([
    "dataproc.googleapis.com",
    "storage.googleapis.com",
    "composer.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "iam.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# GCS Buckets
# ---------------------------------------------------------------------------

resource "google_storage_bucket" "raw_data" {
  name                        = "${var.project_id}-raw-data"
  location                    = var.region
  uniform_bucket_level_access = true
  versioning {
    enabled = true
  }
  lifecycle_rule {
    action { type = "Delete" }
    condition { age = 365 }
  }
}

resource "google_storage_bucket" "processed_data" {
  name                        = "${var.project_id}-processed-data"
  location                    = var.region
  uniform_bucket_level_access = true
  versioning {
    enabled = true
  }
}

resource "google_storage_bucket" "artifacts" {
  name                        = "${var.project_id}-artifacts"
  location                    = var.region
  uniform_bucket_level_access = true
  versioning {
    enabled = true
  }
  lifecycle_rule {
    action { type = "Delete" }
    condition { age = 90 }
  }
}

# ---------------------------------------------------------------------------
# Service Accounts
# ---------------------------------------------------------------------------

resource "google_service_account" "etl_agent" {
  account_id   = "etl-agent-sa"
  display_name = "ETL Agent Service Account"
  description  = "Service account used by the ETL Agent API and Dataproc jobs"
}

resource "google_service_account" "dataproc_worker" {
  account_id   = "etl-dataproc-worker"
  display_name = "Dataproc Worker Service Account"
  description  = "Service account for Dataproc worker nodes"
}

# ---------------------------------------------------------------------------
# IAM Bindings
# ---------------------------------------------------------------------------

locals {
  etl_agent_roles = [
    "roles/dataproc.editor",
    "roles/storage.objectAdmin",
    "roles/secretmanager.secretAccessor",
    "roles/logging.logWriter",
    "roles/run.invoker",
  ]
  dataproc_worker_roles = [
    "roles/storage.objectAdmin",
    "roles/logging.logWriter",
    "roles/dataproc.worker",
  ]
}

resource "google_project_iam_member" "etl_agent_roles" {
  for_each = toset(local.etl_agent_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.etl_agent.email}"
}

resource "google_project_iam_member" "dataproc_worker_roles" {
  for_each = toset(local.dataproc_worker_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.dataproc_worker.email}"
}

# ---------------------------------------------------------------------------
# Secret Manager — store sensitive credentials
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "github_token" {
  secret_id = "etl-agent-github-token"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "anthropic_key" {
  secret_id = "etl-agent-anthropic-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "api_key" {
  secret_id = "etl-agent-api-key"
  replication {
    auto {}
  }
}

# IAM — allow the ETL Agent SA to access all three secrets
resource "google_secret_manager_secret_iam_member" "etl_agent_github_token" {
  secret_id = google_secret_manager_secret.github_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.etl_agent.email}"
}

resource "google_secret_manager_secret_iam_member" "etl_agent_anthropic_key" {
  secret_id = google_secret_manager_secret.anthropic_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.etl_agent.email}"
}

resource "google_secret_manager_secret_iam_member" "etl_agent_api_key" {
  secret_id = google_secret_manager_secret.api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.etl_agent.email}"
}

# ---------------------------------------------------------------------------
# Artifact Registry — Docker image storage
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "etl_agent" {
  location      = var.region
  repository_id = "etl-agent"
  format        = "DOCKER"
  description   = "Docker images for the Autonomous ETL Agent"
}

# ---------------------------------------------------------------------------
# Dataproc Cluster (persistent dev cluster — replace with ephemeral in prod)
# ---------------------------------------------------------------------------

resource "google_dataproc_cluster" "etl_agent" {
  name   = "etl-agent-cluster"
  region = var.region

  cluster_config {
    staging_bucket = google_storage_bucket.artifacts.name

    master_config {
      num_instances = 1
      machine_type  = "n1-standard-4"
      disk_config {
        boot_disk_type    = "pd-ssd"
        boot_disk_size_gb = 100
      }
    }

    worker_config {
      num_instances = 2
      machine_type  = "n1-standard-4"
      disk_config {
        boot_disk_type    = "pd-standard"
        boot_disk_size_gb = 100
      }
    }

    software_config {
      image_version = "2.2-debian12"
      optional_components = ["JUPYTER", "DELTA"]
      override_properties = {
        "dataproc:dataproc.allow.zero.workers" = "true"
        "spark:spark.sql.adaptive.enabled"     = "true"
      }
    }

    gce_cluster_config {
      service_account = google_service_account.dataproc_worker.email
      service_account_scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
      ]
    }
  }
}

# ---------------------------------------------------------------------------
# Cloud Run — ETL Agent API
# ---------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "etl_agent_api" {
  name     = "etl-agent-api"
  location = var.region

  template {
    service_account = google_service_account.etl_agent.email

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/etl-agent/api:latest"

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "GCS_ARTIFACTS_BUCKET"
        value = google_storage_bucket.artifacts.name
      }
      env {
        name  = "GCS_RAW_BUCKET"
        value = google_storage_bucket.raw_data.name
      }
      env {
        name  = "GCS_PROCESSED_BUCKET"
        value = google_storage_bucket.processed_data.name
      }
      env {
        name  = "DATAPROC_CLUSTER_NAME"
        value = google_dataproc_cluster.etl_agent.name
      }
      env {
        name  = "DATAPROC_STAGING_BUCKET"
        value = google_storage_bucket.artifacts.name
      }
      # Secrets loaded from Secret Manager at runtime
      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.anthropic_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GITHUB_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.github_token.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.api_key.secret_id
            version = "latest"
          }
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

# Allow unauthenticated invocations (protected by API key at app level)
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  location = google_cloud_run_v2_service.etl_agent_api.location
  name     = google_cloud_run_v2_service.etl_agent_api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
