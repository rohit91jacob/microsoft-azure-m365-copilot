# One VNet with a delegated subnet for Flex Consumption VNet integration. Storage and Key Vault
# are reached over service endpoints (free) and their firewalls only admit this subnet plus
# trusted Azure services, instead of private endpoints (~USD 7/month each; see README cost notes).

resource "azurerm_network_security_group" "functions" {
  name                = "nsg-${var.name}-func"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags

  # Nothing initiates connections into the integration subnet; outbound stays open for the
  # public source APIs (Retail Prices, M365 roadmap, GitHub, Internet Archive).
  security_rule {
    name                       = "deny-all-inbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_virtual_network" "this" {
  name                = "vnet-${var.name}"
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = [var.address_space]
  tags                = var.tags
}

resource "azurerm_subnet" "functions" {
  name                 = "snet-functions"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [cidrsubnet(var.address_space, 4, 0)]
  # Flex Consumption routes all outbound traffic through the VNet. Default outbound access
  # keeps the public source APIs reachable without a NAT gateway (~USD 32/month); set
  # var.default_outbound_access_enabled = false and attach a NAT gateway for a fixed egress IP.
  default_outbound_access_enabled = var.default_outbound_access_enabled

  service_endpoint {
    service = "Microsoft.Storage"
  }

  service_endpoint {
    service = "Microsoft.KeyVault"
  }

  delegation {
    name = "flex-consumption"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet_network_security_group_association" "functions" {
  subnet_id                 = azurerm_subnet.functions.id
  network_security_group_id = azurerm_network_security_group.functions.id
}
