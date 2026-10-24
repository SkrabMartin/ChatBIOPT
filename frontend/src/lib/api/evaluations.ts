import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiRequest } from "./client";

const summarySchema = z.object({
  id: z.string(),
  kind: z.enum(["retrieval", "answers", "entities"]),
  dataset: z.string(),
  dataset_version: z.string(),
  dataset_sha256: z.string(),
  split: z.enum(["dev", "test"]),
  git_commit: z.string().nullable(),
  summary: z.record(z.string(), z.record(z.string(), z.number())),
  created_at: z.string(),
});
export type EvaluationSummary = z.infer<typeof summarySchema>;

/** A metric is either a bootstrap summary or a plain aggregate (or absent for a configuration). */
const metricSchema = z.union([
  z.object({
    mean: z.number(),
    ci95_low: z.number(),
    ci95_high: z.number(),
    n: z.number().optional(),
  }),
  z.number(),
  z.null(),
]);
export type MetricValue = z.infer<typeof metricSchema>;

const resultSchema = z
  .object({
    metrics: z.record(z.string(), metricSchema),
    latency_ms: z
      .object({ p50: z.number(), p95: z.number() })
      .nullable()
      .optional(),
    failed_runs: z
      .array(z.object({ id: z.string(), error_code: z.string().nullable() }))
      .optional(),
    warnings: z.array(z.string()).optional(),
  })
  .passthrough();

const reportSchema = z
  .object({
    evaluation_run: z.record(z.string(), z.unknown()),
    results: z.record(z.string(), resultSchema),
    injection: z
      .object({
        resistance: z.number().nullable(),
        model_resistance: z.number().nullable().optional(),
      })
      .passthrough()
      .optional(),
  })
  .passthrough();

const evaluationSchema = summarySchema.extend({ report: reportSchema });
export type Evaluation = z.infer<typeof evaluationSchema>;

export function useEvaluations() {
  return useQuery({
    queryKey: ["evaluations"],
    queryFn: ({ signal }) =>
      apiRequest("GET", "/evaluations", z.array(summarySchema), { signal }),
  });
}

export function useEvaluation(id: string | null) {
  return useQuery({
    queryKey: ["evaluations", id],
    queryFn: ({ signal }) =>
      apiRequest("GET", `/evaluations/${id}`, evaluationSchema, { signal }),
    enabled: id !== null,
    staleTime: Infinity, // reports are immutable
  });
}
