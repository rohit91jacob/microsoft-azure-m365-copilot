# Monthly cost budget on the resource group: email at 50% and 80% of actual spend, at 100% of
# actual spend, and as soon as the month is forecast to exceed the budget.

resource "azurerm_consumption_budget_resource_group" "this" {
  name              = "budget-${var.name}"
  resource_group_id = var.resource_group_id
  amount            = var.amount
  time_grain        = "Monthly"

  time_period {
    start_date = var.start_date
  }

  dynamic "notification" {
    for_each = { for t in var.actual_thresholds : tostring(t) => t }
    content {
      enabled        = true
      threshold      = notification.value
      operator       = "GreaterThanOrEqualTo"
      threshold_type = "Actual"
      contact_emails = var.contact_emails
      contact_groups = [var.action_group_id]
    }
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = var.contact_emails
    contact_groups = [var.action_group_id]
  }
}
