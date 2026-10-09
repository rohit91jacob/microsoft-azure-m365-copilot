# azm365 on Azure: ADLS Gen2 lake, Flex Consumption Function App, Key Vault, Data Factory,
# monitoring, a monthly budget and an optional GitHub OIDC deploy identity. Validated in CI
# (fmt, validate, tflint, checkov); it has not been applied to a real subscription.

data "azurerm_client_config" "current" {}

locals {
  name = "${var.project}-${var.environment}"
  # Deterministic suffix for globally unique names (storage accounts, Key Vault).
  suffix  = substr(sha1("${var.subscription_id}/${local.name}"), 0, 6)
  compact = substr(replace(local.name, "-", ""), 0, 12)

  tags = merge(
    {
      project     = var.project
      environment = var.environment
      managed_by  = "terraform"
      repository  = "github.com/rohit91jacob/microsoft-azure-m365-copilot"
      cost_center = var.cost_center
      data_class  = "public"
    },
    var.extra_tags,
  )
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

module "monitoring" {
  source              = "./modules/monitoring"
  name                = local.name
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  daily_quota_gb      = var.log_daily_quota_gb
  alert_emails        = var.alert_emails
  tags                = local.tags
}

module "network" {
  source              = "./modules/network"
  name                = local.name
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = var.vnet_address_space
  tags                = local.tags
}

module "key_vault" {
  source                     = "./modules/key_vault"
  name                       = "kv-${local.compact}-${local.suffix}"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.this.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  deployer_object_id         = data.azurerm_client_config.current.object_id
  allowed_subnet_ids         = [module.network.function_subnet_id]
  allowed_ip_ranges          = var.admin_ip_ranges
  key_expiration_date        = var.key_expiration_date
  log_analytics_workspace_id = module.monitoring.log_analytics_workspace_id
  tags                       = local.tags
}

# Optional GitHub token (raises the API rate limit). Ephemeral input + write-only attribute:
# the token is sent to Key Vault and never written to the Terraform plan or state.
resource "azurerm_key_vault_secret" "github_token" {
  count            = var.github_token_version > 0 ? 1 : 0
  name             = "github-token"
  key_vault_id     = module.key_vault.id
  value_wo         = var.github_token
  value_wo_version = var.github_token_version
  content_type     = "GitHub fine-grained token, public repositories read-only"
  expiration_date  = var.key_expiration_date
  tags             = local.tags
}

module "lake" {
  source                     = "./modules/lake"
  name                       = local.name
  account_name               = "st${local.compact}lake${local.suffix}"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.this.name
  replication_type           = var.lake_replication_type
  cmk_key_versionless_id     = module.key_vault.storage_cmk_versionless_id
  cmk_key_resource_id        = module.key_vault.storage_cmk_resource_id
  allowed_subnet_ids         = [module.network.function_subnet_id]
  allowed_ip_ranges          = var.admin_ip_ranges
  log_analytics_workspace_id = module.monitoring.log_analytics_workspace_id
  tags                       = local.tags
}

module "function_app" {
  source                                 = "./modules/function_app"
  name                                   = local.name
  location                               = var.location
  resource_group_name                    = azurerm_resource_group.this.name
  host_storage_account_name              = "st${local.compact}func${local.suffix}"
  subnet_id                              = module.network.function_subnet_id
  allowed_ip_ranges                      = var.admin_ip_ranges
  maximum_instance_count                 = var.function_maximum_instances
  lake_account_id                        = module.lake.account_id
  lake_blob_endpoint                     = module.lake.blob_endpoint
  key_vault_id                           = module.key_vault.id
  github_token_secret_uri                = var.github_token_version > 0 ? azurerm_key_vault_secret.github_token[0].versionless_id : ""
  application_insights_id                = module.monitoring.application_insights_id
  application_insights_connection_string = module.monitoring.application_insights_connection_string
  log_analytics_workspace_id             = module.monitoring.log_analytics_workspace_id
  action_group_id                        = module.monitoring.action_group_id
  tags                                   = local.tags
}

module "data_factory" {
  source                     = "./modules/data_factory"
  name                       = local.name
  location                   = var.location
  resource_group_name        = azurerm_resource_group.this.name
  orchestration_dir          = "${path.root}/../orchestration/adf"
  lake_account_id            = module.lake.account_id
  lake_dfs_endpoint          = module.lake.dfs_endpoint
  key_vault_id               = module.key_vault.id
  function_app_name          = module.function_app.name
  function_app_hostname      = module.function_app.default_hostname
  secret_expiration_date     = var.key_expiration_date
  log_analytics_workspace_id = module.monitoring.log_analytics_workspace_id
  action_group_id            = module.monitoring.action_group_id
  tags                       = local.tags
}

module "budget" {
  source            = "./modules/budget"
  name              = local.name
  resource_group_id = azurerm_resource_group.this.id
  amount            = var.monthly_budget
  start_date        = var.budget_start_date
  contact_emails    = var.alert_emails
  action_group_id   = module.monitoring.action_group_id
}

module "github_oidc" {
  source            = "./modules/github_oidc"
  count             = var.github_repository == "" ? 0 : 1
  name              = local.name
  github_repository = var.github_repository
  function_app_id   = module.function_app.id
}
