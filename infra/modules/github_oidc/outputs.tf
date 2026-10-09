output "client_id" {
  description = "Set as the AZURE_CLIENT_ID repository variable for the deploy workflow."
  value       = azuread_application.deploy.client_id
}
