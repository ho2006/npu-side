"use client";

import { useEffect, useState } from "react";

import { DataTable } from "@/components/data-table";
import { NetworkGraph } from "@/components/network-graph";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiJson, errorText } from "@/lib/api";
import {
  readCheckView,
  readGraph,
  readMetrics,
  readReproductionCard,
  readReproductionDetail,
  readReproductionList,
  reachSummary,
  RUN_COLUMNS,
  type CheckView,
  type GraphModel,
  type MetricsView,
  type ReproductionCard,
} from "@/lib/domain";

export default function ReproductionsPage() {
  const [cards, setCards] = useState<ReproductionCard[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [metrics, setMetrics] = useState<MetricsView | null>(null);
  const [graph, setGraph] = useState<GraphModel | null>(null);
  const [occupancy, setOccupancy] = useState<ReproductionCard["occupancy"]>(null);
  const [check, setCheck] = useState<CheckView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function refreshList() {
    const rows = readReproductionList(await apiJson("/api/reproductions"));
    if (!rows) throw new ApiError("复现包列表无法读取。", 200);
    setCards(rows);
  }

  useEffect(() => {
    let cancelled = false;
    apiJson("/api/reproductions")
      .then((payload) => {
        if (cancelled) return;
        const rows = readReproductionList(payload);
        if (!rows) {
          setError("复现包列表无法读取。");
          return;
        }
        setCards(rows);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(errorText(caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function importArchive(file: File) {
    if (file.size > 1_048_576) {
      setError("文件超过 1MiB，未发送。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = await apiJson("/api/reproductions", {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: await file.arrayBuffer(),
      });
      const card = readReproductionCard(payload);
      if (!card) {
        setError("复现包响应无法读取。");
        return;
      }
      setSelectedId(card.research_id);
      await refreshList();
      await openPackage(card.research_id);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function openPackage(researchId: string) {
    setSelectedId(researchId);
    setCheck(null);
    setMetrics(null);
    setGraph(null);
    setOccupancy(null);
    const detail = readReproductionDetail(await apiJson(`/api/reproductions/${researchId}`));
    if (!detail) throw new ApiError("复现包响应无法读取。", 200);
    const metricsView = readMetrics(detail.metrics);
    const graphModel = readGraph(detail.population, detail.events);
    setMetrics(metricsView);
    setGraph(graphModel);
    setOccupancy(detail.card.occupancy);
    if (!metricsView) throw new ApiError("指标无法读取。", 200);
    if (!graphModel) throw new ApiError("人口或事件响应无法组成关系图。", 200);
  }

  async function launch(researchId: string) {
    setBusy(true);
    setError("");
    try {
      const payload = await apiJson(`/api/reproductions/${researchId}/launch`, {
        method: "POST",
        body: "{}",
      });
      const card = readReproductionCard(payload);
      if (!card?.occupancy) throw new ApiError("编译响应里没有本次进程的 NPU 占用。", 200);
      setOccupancy(card.occupancy);
      setCards((current) => current.map((item) => (item.research_id === card.research_id ? card : item)));
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function verify(researchId: string) {
    setBusy(true);
    setError("");
    try {
      const view = readCheckView(
        await apiJson(`/api/reproductions/${researchId}/verify`, { method: "POST", body: "{}" }),
      );
      if (!view) throw new ApiError("验证响应无法组成对照表。", 200);
      setCheck(view);
      await refreshList();
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function selectPackage(researchId: string) {
    setError("");
    try {
      await openPackage(researchId);
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  const selected = cards.find((card) => card.research_id === selectedId) ?? null;
  const reach = graph ? reachSummary(graph) : null;

  return (
    <div className="mx-auto grid max-w-5xl gap-6">
      <div>
        <h1 className="text-2xl font-semibold">复现包</h1>
        <p className="mt-2 text-sm text-muted-foreground">把已经完成的社交复现包留在本机。编译和验证使用包里的问卷，不会重写答案。</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>导入</CardTitle>
          <CardDescription>选择一个 zip。同一份包再次导入会返回原来的卡片，问卷文件保持不变。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2">
          <Label htmlFor="reproduction-file">zip 文件</Label>
          <Input
            id="reproduction-file"
            type="file"
            accept=".zip,application/zip"
            className="h-auto py-1"
            disabled={busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void importArchive(file);
            }}
          />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>已导入</CardTitle>
          <CardDescription>{cards.length === 0 ? "还没有复现包。" : `${cards.length} 个包。点一行查看指标和人口。`}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-2">
          {cards.map((card) => (
            <Button
              key={card.research_id}
              type="button"
              variant={card.research_id === selectedId ? "default" : "outline"}
              className="h-auto justify-start py-2 text-left"
              disabled={busy}
              onClick={() => void selectPackage(card.research_id)}
            >
              <span className="grid gap-1">
                <span>{card.title}</span>
                <span className="text-xs font-normal opacity-80">
                  {card.research_id} · {card.compiled ? "已编译" : "未编译"}
                </span>
              </span>
            </Button>
          ))}
        </CardContent>
      </Card>
      {selected ? (
        <Card>
          <CardHeader>
            <CardTitle>{selected.title}</CardTitle>
            <CardDescription>{selected.research_id}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <p className="text-sm">
              {selected.status} · {selected.choice.mode} · {selected.choice.design} · {selected.choice.exposure} ·{" "}
              {selected.choice.policy_id}
            </p>
            <p className="text-sm text-muted-foreground">
              {selected.choice.agent_count} 人 · {selected.choice.rounds} 轮 · 种子 {selected.choice.seeds.join(", ")}
            </p>
            {occupancy ? (
              <p className="text-sm">
                NPU {occupancy.full_name} 占用峰值 {occupancy.peak_percent.toFixed(2)}% {occupancy.engine}{" "}
                {occupancy.burst_inferences}
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">这次进程还没有采样这个包的 NPU 占用。</p>
            )}
            <p className="text-sm text-muted-foreground">{fitLine(selected.compiled, selected.matches_fit)}</p>
            <div className="flex flex-wrap gap-2">
              <Button disabled={busy} onClick={() => void launch(selected.research_id)}>
                编译
              </Button>
              <Button disabled={busy} variant="outline" onClick={() => void verify(selected.research_id)}>
                验证
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}
      {selected && metrics ? (
        <Card>
          <CardHeader>
            <CardTitle>指标</CardTitle>
            {metrics.unit ? <CardDescription>{metrics.unit}</CardDescription> : null}
          </CardHeader>
          <CardContent className="grid gap-4">
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
            {metrics.factorial.length > 0 ? (
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
            ) : null}
            {metrics.runs.length > 0 ? (
              <DataTable columns={RUN_COLUMNS.map((key) => ({ key, header: key }))} rows={metrics.runs} />
            ) : null}
            {metrics.definitions.length > 0 ? (
              <DataTable
                columns={[
                  { key: "metric", header: "metric" },
                  { key: "text", header: "definition" },
                ]}
                rows={metrics.definitions.map((row) => ({ key: row.metric, cells: row }))}
              />
            ) : null}
          </CardContent>
        </Card>
      ) : null}
      {selected && graph ? (
        <Card>
          <CardHeader>
            <CardTitle>人口</CardTitle>
            {reach ? (
              <CardDescription>
                触达 {reach.reached} / {reach.total}
              </CardDescription>
            ) : null}
          </CardHeader>
          <CardContent className="grid gap-4">
            {graph.eventNote ? <p className="text-sm text-muted-foreground">{graph.eventNote}</p> : null}
            <NetworkGraph graph={graph} />
          </CardContent>
        </Card>
      ) : null}
      {selected && check ? (
        <Card>
          <CardHeader>
            <CardTitle>验证</CardTitle>
            <CardDescription>{matchText(check.matchesFit)}</CardDescription>
          </CardHeader>
          <CardContent>
            <DataTable columns={check.agents.columns} rows={check.agents.rows} />
          </CardContent>
        </Card>
      ) : null}
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function matchText(matchesFit: boolean | null) {
  if (matchesFit === true) return "与拟合记录一致";
  if (matchesFit === false) return "与拟合记录不一致";
  return "新的实验分数";
}

function fitLine(compiled: boolean, matchesFit: boolean | null) {
  if (!compiled) return "尚未在 NPU 上编译。";
  if (matchesFit === true) return "已编译。验证与拟合记录一致。";
  if (matchesFit === false) return "已编译。验证与拟合记录不一致。";
  return "已编译。还没有验证。";
}
