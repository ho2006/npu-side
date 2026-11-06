"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { DataTable } from "@/components/data-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiJson, errorText } from "@/lib/api";
import { readMetrics, RUN_COLUMNS, type MetricsView } from "@/lib/domain";
import { rememberExperiment } from "@/lib/selection";

export default function ComparePage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [metrics, setMetrics] = useState<MetricsView | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!id) return;
    rememberExperiment(id);
    let cancelled = false;
    apiJson(`/api/experiments/${id}/metrics`)
      .then((payload) => {
        if (cancelled) return;
        const view = readMetrics(payload);
        if (!view) {
          setError("指标响应里没有 comparison。");
          setMetrics(null);
          return;
        }
        setMetrics(view);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(errorText(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div className="mx-auto grid max-w-5xl gap-6">
      <div>
        <h1 className="text-2xl font-semibold">对比</h1>
        <p className="mt-2 text-sm text-muted-foreground">数字来自指标接口。页面不补算缺失结果。</p>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {!metrics && !error ? <p className="text-sm text-muted-foreground">正在读取指标。</p> : null}
      {metrics ? (
        <>
          {metrics.unit ? <p className="text-sm text-muted-foreground">{metrics.unit}</p> : null}
          <Card>
            <CardHeader>
              <CardTitle>comparison</CardTitle>
            </CardHeader>
            <CardContent>
              <DataTable
                columns={[
                  { key: "metric", header: "metric" },
                  { key: "A0", header: "A0" },
                  { key: "A1", header: "A1" },
                  { key: "difference", header: "difference" },
                  { key: "seed_min", header: "seed_min" },
                  { key: "seed_max", header: "seed_max" },
                ]}
                rows={metrics.comparison.map((row) => ({ key: row.metric, cells: row }))}
              />
            </CardContent>
          </Card>
          {metrics.factorial.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>factorial_effects</CardTitle>
              </CardHeader>
              <CardContent>
                <DataTable
                  columns={[
                    { key: "factor", header: "factor" },
                    { key: "metric", header: "metric" },
                    { key: "mean", header: "mean" },
                    { key: "seed_min", header: "seed_min" },
                    { key: "seed_max", header: "seed_max" },
                  ]}
                  rows={metrics.factorial.map((row) => ({
                    key: `${row.factor}-${row.metric}`,
                    cells: row,
                  }))}
                />
              </CardContent>
            </Card>
          ) : null}
          {metrics.runs.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>runs</CardTitle>
              </CardHeader>
              <CardContent>
                <DataTable
                  columns={RUN_COLUMNS.map((key) => ({ key, header: key }))}
                  rows={metrics.runs}
                />
              </CardContent>
            </Card>
          ) : null}
          {metrics.definitions.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>definitions</CardTitle>
              </CardHeader>
              <CardContent>
                <DataTable
                  columns={[
                    { key: "metric", header: "metric" },
                    { key: "text", header: "definition" },
                  ]}
                  rows={metrics.definitions.map((row) => ({ key: row.metric, cells: row }))}
                />
              </CardContent>
            </Card>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
