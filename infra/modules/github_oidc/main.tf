# Entra ID application trusted by GitHub Actions OIDC for the deploy workflow. No client
# secret exists: GitHub exchanges its short-lived OIDC token for an Entra token, and only jobs
# running in the named repository's protected environment can do so. The service principal
# can publish code to the one Function App and nothing else.

resource "azuread_application" "deploy" {
  display_name     = "gh-${var.name}-deploy"
  sign_in_audience = "AzureADMyOrg"
  notes            = "GitHub Actions OIDC deploy identity for ${var.github_repository} (environment ${var.github_environment})."
}

resource "azuread_service_principal" "deploy" {
  client_id = azuread_application.deploy.client_id
}

resource "azuread_application_federated_identity_credential" "environment" {
  #checkov:skip=CKV_AZURE_249:The subject is built from var.github_repository, which is validated as owner/name (no wildcards) and pinned to one protected environment; checkov cannot resolve the interpolation.
  application_id = azuread_application.deploy.id
  display_name   = "github-${var.github_environment}"
  description    = "Deploy jobs in the ${var.github_environment} environment of ${var.github_repository}."
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = "repo:${var.github_repository}:environment:${var.github_environment}"
}

resource "azurerm_role_assignment" "deploy_function" {
  scope                = var.function_app_id
  role_definition_name = "Website Contributor"
  principal_id         = azuread_service_principal.deploy.object_id
  principal_type       = "ServicePrincipal"
}
