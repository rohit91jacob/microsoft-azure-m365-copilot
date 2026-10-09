output "function_subnet_id" {
  description = "Subnet the Function App integrates with; admitted by the storage and Key Vault firewalls."
  value       = azurerm_subnet.functions.id
}

output "vnet_id" {
  description = "Virtual network ID."
  value       = azurerm_virtual_network.this.id
}
