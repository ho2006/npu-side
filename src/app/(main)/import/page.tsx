"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { apiJson, errorText } from "@/lib/api";
import {
  baselineOptions,
  calibrationTemplate,
  isSourceKind,
  isStage,
  readExperiments,
  readImportResult,
  readProtocol,
  type ListedExperiment,
  type ProtocolRecord,
  type SourceKind,
  type Stage,
} from "@/lib/domain";
import { rememberProtocol } from "@/lib/selection";

export default function ImportPage() {
  const [experiments, setExperiments] = useState<ListedExperiment[]>([]);
  const [sourceKind, setSourceKind] = useState<SourceKind>("fixture");
  const [stage, setStage] = useState<Stage>("pilot");
  const [baselineId, setBaselineId] = useState("");
  const [authorizationReference, setAuthorizationReference] = useState("");
  const [consentVersion, setConsentVersion] = useState("");
  const [authorizationConfirmed, setAuthorizationConfirmed] = useState(false);
  const [protocol, setProtocol] = useState<ProtocolRecord | null>(null);
  const [importResult, setImportResult] = useState<{ status: string; externalValidity: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiJson("/api/experiments")
      .then((payload) => {
        if (cancelled) return;
        const rows = readExperiments(payload);
        if (!rows) {
          setError("实验列表不是数组。");
          return;
        }
        setExperiments(rows);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(errorText(caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const baselines = baselineOptions(experiments, sourceKind);
  const humanReady = authorizationConfirmed && authorizationReference.trim() && consentVersion.trim();
  const canFreeze = Boolean(baselineId) && (sourceKind === "fixture" || humanReady);

  async function freezeProtocol() {
    if (!canFreeze) return;
    setBusy(true);
    setError("");
    setImportResult(null);
    const body: Record<string, unknown> = {
      baseline_id: baselineId,
      source_kind: sourceKind,
      stage,
    };
    if (sourceKind === "human") {
      body.authorization_confirmed = true;
      body.authorization_reference = authorizationReference.trim();
      body.consent_version = consentVersion.trim();
    }
    try {
      const payload = await apiJson("/api/calibrations", { method: "POST", body: JSON.stringify(body) });
      const created = readProtocol(payload);
      if (!created) {
        setError("协议响应缺少 id、protocol_sha256 或材料哈希。");
        return;
      }
      setProtocol(created);
      rememberProtocol(created.id);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  function downloadTemplate() {
    if (!protocol) return;
    const blob = new Blob([calibrationTemplate(protocol)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `calibration-${protocol.id}-template.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function importFile(file: File) {
    if (!protocol) return;
    setImportResult(null);
    setError("");
    if (file.size > 1_048_576) {
      setError("文件超过 1MiB，未发送。");
      return;
    }
    setBusy(true);
    try {
      const text = await file.text();
      const parsed: unknown = JSON.parse(text);
      const payload = await apiJson(`/api/calibrations/${protocol.id}/import`, {
        method: "POST",
        body: JSON.stringify(parsed),
      });
      const result = readImportResult(payload);
      if (!result) {
        setError("导入响应里没有 result.status 或 result.external_validity。");
        return;
      }
      setImportResult(result);
    } catch (caught) {
      setError(caught instanceof SyntaxError ? "文件不是 JSON。" : errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto grid max-w-3xl gap-6">
      <div>
        <h1 className="text-2xl font-semibold">导入</h1>
        <p className="mt-2 text-sm text-muted-foreground">校准数据留在本机，不会发送给 Qwen。</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>冻结协议</CardTitle>
          <CardDescription>基线必须是已完成的直接阅读实验。真人数据还需要 live 基线、授权记录和同意版本。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label>来源</Label>
              <Select
                value={sourceKind}
                onValueChange={(value) => {
                  if (!isSourceKind(value)) return;
                  setSourceKind(value);
                  setBaselineId("");
                  setAuthorizationConfirmed(false);
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="fixture">fixture</SelectItem>
                  <SelectItem value="human">human</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>阶段</Label>
              <Select
                value={stage}
                onValueChange={(value) => {
                  if (isStage(value)) setStage(value);
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="pilot">pilot</SelectItem>
                  <SelectItem value="heldout">heldout</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-2">
            <Label>直接阅读基线</Label>
            <Select value={baselineId} onValueChange={setBaselineId}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="选择已完成的基线" />
              </SelectTrigger>
              <SelectContent>
                {baselines.map((row) => (
                  <SelectItem key={row.id} value={row.id}>
                    {row.title} · {row.mode} · {row.id.slice(0, 8)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {baselines.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {sourceKind === "human" ? "没有已完成的 live 直接阅读实验。" : "没有已完成的直接阅读实验。"}
              </p>
            ) : null}
          </div>
          {sourceKind === "human" ? (
            <>
              <div className="grid gap-2">
                <Label htmlFor="authorization-reference">authorization_reference</Label>
                <Input
                  id="authorization-reference"
                  value={authorizationReference}
                  onChange={(event) => setAuthorizationReference(event.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="consent-version">consent_version</Label>
                <Input id="consent-version" value={consentVersion} onChange={(event) => setConsentVersion(event.target.value)} />
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={authorizationConfirmed}
                  onChange={(event) => setAuthorizationConfirmed(event.target.checked)}
                />
                authorization_confirmed
              </label>
            </>
          ) : null}
          <Button disabled={busy || !canFreeze || Boolean(protocol)} onClick={() => void freezeProtocol()}>
            冻结协议
          </Button>
        </CardContent>
      </Card>
      {protocol ? (
        <Card>
          <CardHeader>
            <CardTitle>导入文件</CardTitle>
            <CardDescription>
              协议 {protocol.id}。模板只带协议哈希和空的 participants，问卷正文只在选择文件后发给导入接口。
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <p className="text-sm break-all">protocol_sha256 {protocol.protocol_sha256}</p>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={downloadTemplate}>
                下载空白 JSON 模板
              </Button>
              <Button asChild variant="outline">
                <a href={`/api/calibrations/${protocol.id}/export`}>下载校准证据包</a>
              </Button>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="calibration-file">JSON 文件</Label>
              <Input
                id="calibration-file"
                type="file"
                accept=".json,application/json"
                className="h-auto py-1"
                disabled={busy}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) void importFile(file);
                }}
              />
            </div>
            {importResult ? (
              <div className="grid gap-1 text-sm">
                <p>result.status {importResult.status}</p>
                <p>result.external_validity {importResult.externalValidity}</p>
              </div>
            ) : null}
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
