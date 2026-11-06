"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { DataTable } from "@/components/data-table";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiJson, errorText } from "@/lib/api";
import {
  GRAPH_EVENT_LIMIT,
  readBaselineId,
  readBoardView,
  readCheckView,
  readExperimentHeader,
  readGraph,
  readMetrics,
  readRunStatus,
  reachSummary,
  RUN_COLUMNS,
  type BoardView,
  type CheckView,
  type ExperimentHeader,
  type GraphModel,
  type MetricsView,
} from "@/lib/domain";
import { rememberExperiment, rememberProtocol, storedProtocolId } from "@/lib/selection";

type BaselineState = "unknown" | "match" | "miss";

function matchText(matchesFit: boolean | null) {
  if (matchesFit === true) return "与拟合记录一致";
  if (matchesFit === false) return "与拟合记录不一致";
  return "新的实验分数";
}

export default function BoardPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [protocolId, setProtocolId] = useState("");
  const [header, setHeader] = useState<ExperimentHeader | null>(null);
  const [headerError, setHeaderError] = useState("");
  const [metrics, setMetrics] = useState<MetricsView | null>(null);
  const [metricsError, setMetricsError] = useState("");
  const [graph, setGraph] = useState<GraphModel | null>(null);
  const [graphError, setGraphError] = useState("");
  const [baseline, setBaseline] = useState<BaselineState>("unknown");
  const [board, setBoard] = useState<BoardView | null>(null);
  const [sideError, setSideError] = useState("");
  const [sideEmpty, setSideEmpty] = useState("");
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [check, setCheck] = useState<CheckView | null>(null);
  const [verifyError, setVerifyError] = useState("");
  const [busy, setBusy] = useState(false);
  const protocol = protocolId.trim();
  const reach = graph ? reachSummary(graph) : null;

  useEffect(() => {
    setProtocolId(storedProtocolId());
  }, []);

  useEffect(() => {
    if (!id) return;
    rememberExperiment(id);
    let cancelled = false;
    setHeader(null);
    setHeaderError("");
    setMetrics(null);
    setMetricsError("");
    setGraph(null);
    setGraphError("");

    apiJson(`/api/experiments/${id}`)
      .then((payload) => {
        if (cancelled) return;
        const next = readExperimentHeader(payload);
        if (!next) {
          setHeader(null);
          setHeaderError("实验响应无法读取。");
          return;
        }
        setHeader(next);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setHeader(null);
        setHeaderError(errorText(caught));
      });

    apiJson(`/api/experiments/${id}/metrics`)
      .then((payload) => {
        if (cancelled) return;
        const view = readMetrics(payload);
        if (!view) {
          setMetrics(null);
          setMetricsError("指标响应里没有 comparison。");
          return;
        }
        setMetrics(view);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setMetrics(null);
        setMetricsError(errorText(caught));
      });

    Promise.all([
      apiJson(`/api/experiments/${id}/population`),
      apiJson(`/api/experiments/${id}/events?limit=${GRAPH_EVENT_LIMIT}`),
    ])
      .then(([population, events]) => {
        if (cancelled) return;
        const model = readGraph(population, events);
        if (!model) {
          setGraph(null);
          setGraphError("人口或事件响应无法组成关系图。");
          return;
        }
        setGraph(model);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setGraph(null);
        setGraphError(errorText(caught));
      });

    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (!protocol) {
      setBaseline("unknown");
      setBoard(null);
      setSideError("");
      setSideEmpty("");
      setRunStatus(null);
      setCheck(null);
      setVerifyError("");
      return;
    }
    let cancelled = false;
    setBaseline("unknown");
    setBoard(null);
    setSideError("");
    setSideEmpty("");
    setRunStatus(null);
    setCheck(null);
    setVerifyError("");
    apiJson(`/api/calibrations/${protocol}`)
      .then((payload) => {
        if (cancelled) return null;
        const matches = readBaselineId(payload) === id;
        setBaseline(matches ? "match" : "miss");
        return apiJson(`/api/calibrations/${protocol}/side`).then(
          (body) => {
            if (cancelled) return;
            const view = readBoardView(body);
            setBoard(view);
            if (!view) setSideError("侧端响应无法组成对照表。");
          },
          (caught: unknown) => {
            if (cancelled) return;
            setBoard(null);
            if (caught instanceof ApiError && caught.status === 404) {
              setSideEmpty(errorText(caught));
              return;
            }
            setSideError(errorText(caught));
          },
        );
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setBaseline("unknown");
        setBoard(null);
        setSideError(errorText(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [id, protocol]);

  async function fitSide() {
    if (!protocol || baseline !== "match") return;
    setBusy(true);
    setSideError("");
    setCheck(null);
    setVerifyError("");
    try {
      const posted = await apiJson(`/api/calibrations/${protocol}/side`, { method: "POST", body: "{}" });
      setRunStatus(readRunStatus(posted));
    } catch (caught) {
      setRunStatus(errorText(caught));
    } finally {
      // 503 and an insufficient sample leave the previous file. The tables render this GET.
      const body = await apiJson(`/api/calibrations/${protocol}/side`).catch(() => null);
      if (body) {
        const view = readBoardView(body);
        setBoard(view);
        setSideEmpty("");
        if (!view) setSideError("侧端响应无法组成对照表。");
      } else {
        setBoard(null);
        setSideEmpty("尚未侧端拟合");
      }
      setBusy(false);
    }
  }

  async function verifyExperiment() {
    if (!protocol || !board) return;
    setBusy(true);
    setCheck(null);
    setVerifyError("");
    try {
      const posted = await apiJson(`/api/calibrations/${protocol}/verify`, {
        method: "POST",
        body: JSON.stringify({ experiment_id: id }),
      });
      const view = readCheckView(posted);
      setCheck(view);
      if (!view) setVerifyError("验证响应无法组成对照表。");
    } catch (caught: unknown) {
      setVerifyError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const runIsError = runStatus !== null && runStatus !== "INFERRED" && runStatus !== "INSUFFICIENT_SAMPLE";

  return (
    <div className="mx-auto grid max-w-5xl gap-6">
      <div>
        <h1 className="text-2xl font-semibold">仪表盘</h1>
        <p className="mt-2 text-sm text-muted-foreground">实验状态、指标、人群和触达分开读取。打开页面不拟合。</p>
      </div>

      {headerError ? (
        <p role="alert" className="text-sm text-destructive">
          {headerError}
        </p>
      ) : null}
      {!header && !headerError ? <p className="text-sm text-muted-foreground">正在读取实验。</p> : null}
      {header ? (
        <Card>
          <CardHeader>
            <CardTitle>{header.title || header.id}</CardTitle>
            <CardDescription>{header.id}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2">
            <p className="text-sm">{header.status}</p>
            {header.error ? <p className="text-sm text-destructive">{header.error}</p> : null}
          </CardContent>
        </Card>
      ) : null}

      {metricsError ? (
        <p role="alert" className="text-sm text-destructive">
          {metricsError}
        </p>
      ) : null}
      {!metrics && !metricsError ? <p className="text-sm text-muted-foreground">正在读取指标。</p> : null}
      {metrics ? (
        <>
          <p className="text-sm text-muted-foreground">数字来自指标接口。页面不补算缺失结果。</p>
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
                <DataTable columns={RUN_COLUMNS.map((key) => ({ key, header: key }))} rows={metrics.runs} />
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

      {graphError ? (
        <p role="alert" className="text-sm text-destructive">
          {graphError}
        </p>
      ) : null}
      {!graph && !graphError ? <p className="text-sm text-muted-foreground">正在读取人口和事件。</p> : null}
      {graph && reach ? (
        <Card>
          <CardHeader>
            <CardTitle>触达</CardTitle>
            <CardDescription>
              {reach.reached} / {reach.total}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            {graph.eventNote ? <p className="text-sm text-muted-foreground">{graph.eventNote}</p> : null}
            <DataTable
              columns={[
                { key: "group", header: "group" },
                { key: "n", header: "n" },
                { key: "reached", header: "reached" },
              ]}
              rows={reach.mix.map((row, index) => ({
                key: `${row.group}-${index}`,
                cells: { group: row.group, n: String(row.n), reached: String(row.reached) },
              }))}
            />
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>侧端</CardTitle>
          <CardDescription>拟合只在点击后发生，并且只针对基线。验证实验用已保存的模型给当前实验打分。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="protocol-id">协议编号</Label>
            <Input
              id="protocol-id"
              value={protocolId}
              disabled={busy}
              onChange={(event) => {
                const next = event.target.value;
                setProtocolId(next);
                rememberProtocol(next.trim());
              }}
            />
          </div>
          {!protocol ? <p className="text-sm text-muted-foreground">没有协议编号，不请求侧端。</p> : null}
          {protocol && baseline === "unknown" && !sideError ? (
            <p className="text-sm text-muted-foreground">正在读取协议。</p>
          ) : null}
          {protocol && baseline === "miss" ? (
            <p className="text-sm text-muted-foreground">基线不是当前实验。拟合只针对基线。</p>
          ) : null}
          {baseline === "match" || board ? (
            <div className="flex flex-wrap gap-2">
              {baseline === "match" ? (
                <Button disabled={busy} onClick={() => void fitSide()}>
                  拟合侧端
                </Button>
              ) : null}
              {board ? (
                <Button disabled={busy} onClick={() => void verifyExperiment()}>
                  验证实验
                </Button>
              ) : null}
            </div>
          ) : null}
          {runStatus ? (
            <p role={runIsError ? "alert" : undefined} className={runIsError ? "text-sm text-destructive" : "text-sm"}>
              {runStatus}
            </p>
          ) : null}
          {sideError ? (
            <p role="alert" className="text-sm text-destructive">
              {sideError}
            </p>
          ) : null}
          {sideEmpty && !board ? <p className="text-sm text-muted-foreground">{sideEmpty}</p> : null}
          {verifyError ? (
            <p role="alert" className="text-sm text-destructive">
              {verifyError}
            </p>
          ) : null}
        </CardContent>
      </Card>

      {board ? (
        <>
          <p className="text-sm text-muted-foreground">
            {board.device} {board.runtime} n_development {board.nDevelopment}
          </p>
          <p className="text-sm">{board.externalValidity}</p>
          <Card>
            <CardHeader>
              <CardTitle>fits</CardTitle>
            </CardHeader>
            <CardContent>
              <DataTable columns={board.fits.columns} rows={board.fits.rows} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>heldout</CardTitle>
            </CardHeader>
            <CardContent>
              <DataTable columns={board.heldout.columns} rows={board.heldout.rows} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>agents</CardTitle>
            </CardHeader>
            <CardContent>
              <DataTable columns={board.agents.columns} rows={board.agents.rows} />
            </CardContent>
          </Card>
          {check ? (
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
        </>
      ) : null}
    </div>
  );
}
