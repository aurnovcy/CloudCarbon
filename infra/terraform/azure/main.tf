# CloudCarbon — Azure Terraform skeleton
# Populated in Session 4+

terraform {
  required_version = ">= 1.7.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "East US"
}

variable "resource_group_name" {
  description = "Azure resource group name"
  type        = string
  default     = "cloudcarbon-rg"
}
