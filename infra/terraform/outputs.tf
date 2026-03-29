output "api_url" {
  description = "Cloud Run service URL for the ETL Agent API"
  value       = google_cloud_run_v2_service.etl_agent_api.uri
}

output "raw_bucket" {
  description = "GCS bucket for raw source data"
  value       = google_storage_bucket.raw_data.name
}

output "processed_bucket" {
  description = "GCS bucket for processed/output data"
  value       = google_storage_bucket.processed_data.name
}

output "artifacts_bucket" {
  description = "GCS bucket for generated artifacts and Dataproc staging"
  value       = google_storage_bucket.artifacts.name
}

output "dataproc_cluster_name" {
  description = "Dataproc cluster name"
  value       = google_dataproc_cluster.etl_agent.name
}

output "etl_agent_sa_email" {
  description = "Service account email for the ETL Agent"
  value       = google_service_account.etl_agent.email
}

output "artifact_registry_repo" {
  description = "Artifact Registry Docker repository URI"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/etl-agent"
}
