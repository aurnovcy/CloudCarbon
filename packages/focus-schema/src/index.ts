/**
 * CloudCarbon FOCUS Schema Package
 *
 * Exports:
 *   FocusRecordSchema          — Zod schema for FOCUS 1.0 core fields (PascalCase per spec)
 *   CloudCarbonRecordSchema    — Zod schema for FOCUS 1.0 + enrichment extensions
 *   FocusRecord                — TypeScript type (inferred from FocusRecordSchema)
 *   CloudCarbonRecord          — TypeScript type (inferred from CloudCarbonRecordSchema)
 *
 * Also re-exports the DB-oriented schemas (camelCase) used by the dashboard.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Enumerations
// ---------------------------------------------------------------------------

export const ChargeCategoryEnum = z.enum([
  "Usage",
  "Purchase",
  "Tax",
  "Adjustment",
  "Credit",
]);
export type ChargeCategory = z.infer<typeof ChargeCategoryEnum>;

export const ChargeClassEnum = z.enum(["Regular", "Correction"]);
export type ChargeClass = z.infer<typeof ChargeClassEnum>;

export const ChargeFrequencyEnum = z.enum([
  "One-Time",
  "Recurring",
  "Usage-Based",
]);
export type ChargeFrequency = z.infer<typeof ChargeFrequencyEnum>;

export const PricingCategoryEnum = z.enum([
  "On-Demand",
  "Commitment-Based",
  "Spot",
  "Other",
]);
export type PricingCategory = z.infer<typeof PricingCategoryEnum>;

export const Scope3ConfidenceEnum = z.enum(["high", "medium", "low"]);
export type Scope3Confidence = z.infer<typeof Scope3ConfidenceEnum>;

export const WaterDataSourceEnum = z.enum([
  "provider_disclosed",
  "cooling_type_estimate",
]);
export type WaterDataSource = z.infer<typeof WaterDataSourceEnum>;

export const CloudProviderEnum = z.enum(["aws", "azure", "gcp", "alibaba"]);
export type CloudProvider = z.infer<typeof CloudProviderEnum>;

// ---------------------------------------------------------------------------
// FOCUS 1.0 Core Schema (PascalCase field names per FOCUS specification)
// ---------------------------------------------------------------------------

export const FocusRecordSchema = z.object({
  // Required FOCUS 1.0 fields
  BillingPeriodStart: z.coerce.date(),
  BillingPeriodEnd: z.coerce.date(),
  ChargeCategory: ChargeCategoryEnum,
  EffectiveCost: z.number(),
  InvoiceIssuerName: z.string(),
  ProviderName: z.string(),
  ServiceCategory: z.string(),
  ServiceName: z.string(),

  // Optional FOCUS 1.0 fields
  ChargeClass: ChargeClassEnum.nullable().optional(),
  ChargeDescription: z.string().nullable().optional(),
  ChargeFrequency: ChargeFrequencyEnum.nullable().optional(),
  ChargePeriodStart: z.coerce.date().nullable().optional(),
  ChargePeriodEnd: z.coerce.date().nullable().optional(),
  ConsumedQuantity: z.number().nullable().optional(),
  ConsumedUnit: z.string().nullable().optional(),
  ContractedCost: z.number().nullable().optional(),
  ContractedUnitPrice: z.number().nullable().optional(),
  ListCost: z.number().nullable().optional(),
  ListUnitPrice: z.number().nullable().optional(),
  PricingCategory: PricingCategoryEnum.nullable().optional(),
  PricingQuantity: z.number().nullable().optional(),
  PricingUnit: z.string().nullable().optional(),
  PublisherName: z.string().nullable().optional(),
  RegionId: z.string().nullable().optional(),
  RegionName: z.string().nullable().optional(),
  ResourceId: z.string().nullable().optional(),
  ResourceName: z.string().nullable().optional(),
  ResourceType: z.string().nullable().optional(),
  SkuId: z.string().nullable().optional(),
  SkuPriceId: z.string().nullable().optional(),
  SubAccountId: z.string().nullable().optional(),
  SubAccountName: z.string().nullable().optional(),
  Tags: z.record(z.string(), z.string()).nullable().optional(),
});

export type FocusRecord = z.infer<typeof FocusRecordSchema>;

// ---------------------------------------------------------------------------
// CloudCarbon Enrichment Extensions
// ---------------------------------------------------------------------------

export const CloudCarbonRecordSchema = FocusRecordSchema.extend({
  // GHG Protocol Scope 1
  scope1_co2e_kg: z.number().nullable().optional(),

  // GHG Protocol Scope 2
  scope2_co2e_kg_location: z.number().nullable().optional(),
  scope2_co2e_kg_market: z.number().nullable().optional(),

  // GHG Protocol Scope 3
  scope3_cat1_co2e_kg: z.number().nullable().optional(),
  scope3_cat3_co2e_kg: z.number().nullable().optional(),
  scope3_cat12_co2e_kg: z.number().nullable().optional(),
  scope3_total_co2e_kg: z.number().nullable().optional(),
  scope3_confidence: Scope3ConfidenceEnum.nullable().optional(),
  scope3_methodology_ref: z.string().nullable().optional(),

  // Totals
  total_co2e_kg: z.number().nullable().optional(),

  // Energy
  carbon_intensity_gco2_kwh: z.number().nullable().optional(),
  estimated_kwh: z.number().nullable().optional(),

  // Hardware
  hardware_family: z.string().nullable().optional(),
  resource_share: z.number().min(0).max(1).nullable().optional(),

  // Water
  water_litres: z.number().nullable().optional(),
  wue_litres_per_kwh: z.number().nullable().optional(),
  water_stress_score: z.number().nullable().optional(),
  water_stress_adjusted_litres: z.number().nullable().optional(),
  water_data_source: WaterDataSourceEnum.nullable().optional(),
});

export type CloudCarbonRecord = z.infer<typeof CloudCarbonRecordSchema>;

// ---------------------------------------------------------------------------
// DB-oriented schemas (camelCase) — used by the dashboard / API responses
// ---------------------------------------------------------------------------

export const EnrichedRecordSchema = z.object({
  id: z.string().uuid(),
  focusRecordId: z.string().uuid(),
  tenantId: z.string().uuid(),
  scope1Co2eKg: z.number().nullable().optional(),
  scope2Co2eKgLocation: z.number().nullable().optional(),
  scope2Co2eKgMarket: z.number().nullable().optional(),
  scope3Cat1Co2eKg: z.number().nullable().optional(),
  scope3Cat3Co2eKg: z.number().nullable().optional(),
  scope3Cat12Co2eKg: z.number().nullable().optional(),
  scope3TotalCo2eKg: z.number().nullable().optional(),
  scope3Confidence: Scope3ConfidenceEnum.nullable().optional(),
  scope3MethodologyRef: z.string().nullable().optional(),
  totalCo2eKg: z.number().nullable().optional(),
  carbonIntensityGco2Kwh: z.number().nullable().optional(),
  estimatedKwh: z.number().nullable().optional(),
  hardwareFamily: z.string().nullable().optional(),
  resourceShare: z.number().nullable().optional(),
  waterLitres: z.number().nullable().optional(),
  wueLitresPerKwh: z.number().nullable().optional(),
  waterStressScore: z.number().nullable().optional(),
  waterStressAdjustedLitres: z.number().nullable().optional(),
  waterDataSource: WaterDataSourceEnum.nullable().optional(),
  enrichedAt: z.string().datetime(),
  enrichmentVersion: z.string().nullable().optional(),
});

export type EnrichedRecord = z.infer<typeof EnrichedRecordSchema>;

// ---------------------------------------------------------------------------
// Validation helpers
// ---------------------------------------------------------------------------

export function parseFocusRecord(data: unknown): FocusRecord {
  return FocusRecordSchema.parse(data);
}

export function parseCloudCarbonRecord(data: unknown): CloudCarbonRecord {
  return CloudCarbonRecordSchema.parse(data);
}

export function safeParseFocusRecord(data: unknown) {
  return FocusRecordSchema.safeParse(data);
}

export function safeParseCloudCarbonRecord(data: unknown) {
  return CloudCarbonRecordSchema.safeParse(data);
}

// Re-export zod for convenience
export { z };
