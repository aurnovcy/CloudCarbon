/**
 * @cloudcarbon/carbon-models
 *
 * Carbon and water estimation logic.
 * TypeScript models mirror the Python implementations in apps/api/src/services/carbon/.
 * Session 2 will populate the actual estimation algorithms.
 */

export type CarbonEstimationInput = {
  provider: "aws" | "azure" | "gcp" | "alibaba";
  region: string;
  serviceName: string;
  usageQuantity: number;
  usageUnit: string;
  costUsd: number;
};

export type CarbonEstimationResult = {
  scope1Co2eKg: number | null;
  scope2Co2eKgLocation: number | null;
  scope2Co2eKgMarket: number | null;
  scope3TotalCo2eKg: number | null;
  totalCo2eKg: number | null;
  estimatedKwh: number | null;
  carbonIntensityGco2Kwh: number | null;
  confidence: "high" | "medium" | "low";
  methodologyRef: string;
};

export type WaterEstimationInput = {
  provider: "aws" | "azure" | "gcp" | "alibaba";
  region: string;
  estimatedKwh: number;
};

export type WaterEstimationResult = {
  waterLitres: number | null;
  wueLitresPerKwh: number | null;
  waterStressScore: number | null;
  waterStressAdjustedLitres: number | null;
  dataSource: "provider_disclosed" | "cooling_type_estimate";
};

// Stub implementations — will be replaced with real logic in Session 2
export function estimateCarbon(
  _input: CarbonEstimationInput
): CarbonEstimationResult {
  throw new Error("Not implemented — Session 2");
}

export function estimateWater(
  _input: WaterEstimationInput
): WaterEstimationResult {
  throw new Error("Not implemented — Session 2");
}
