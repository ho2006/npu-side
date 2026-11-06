"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { apiJson, errorText } from "@/lib/api";
import {
  chooseDesign,
  chooseExposure,
  chooseMode,
  choosePolicy,
  formToPlan,
  initialChoice,
  isDesign,
  isExposure,
  isMode,
  isPolicy,
  parseBoundedInteger,
  parseSeeds,
  planRequest,
  planToForm,
  readId,
  readPlan,
  startBody,
  type PlanForm,
  type StudyChoice,
} from "@/lib/domain";
import { rememberExperiment } from "@/lib/selection";

export default function ExperimentsPage() {
  const router = useRouter();
  const [question, setQuestion] = useState("信息清晰度会影响政策理解吗？");
  const [choice, setChoice] = useState<StudyChoice>(initialChoice);
  const [plan, setPlan] = useState<PlanForm | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [agentCount, setAgentCount] = useState(30);
  const [rounds, setRounds] = useState(3);
  const [seedsText, setSeedsText] = useState("42");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function replaceChoice(next: StudyChoice) {
    setChoice(next);
    setPlan(null);
    setSource(null);
    setConfirmed(false);
  }

  function editPlan(patch: Partial<PlanForm>) {
    setPlan((current) => (current ? { ...current, ...patch } : current));
    setConfirmed(false);
  }

  async function loadPlan() {
    setBusy(true);
    setError("");
    try {
      const payload = await apiJson("/api/research/plan", {
        method: "POST",
        body: JSON.stringify(planRequest(question, choice)),
      });
      const loaded = readPlan(payload);
      if (!loaded) {
        setError("研究计划响应缺少题目、问题、假设、分析或局限。");
        setPlan(null);
        setSource(null);
        return;
      }
      setPlan(planToForm(loaded.plan));
      setSource(loaded.source);
      setConfirmed(false);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  async function startExperiment() {
    if (!plan || !confirmed) return;
    const count = parseBoundedInteger(agentCount, 10, 60);
    const roundCount = parseBoundedInteger(rounds, 1, 6);
    const seeds = parseSeeds(seedsText);
    if (count === null) {
      setError("消费者数量需要 10 到 60 的整数。");
      return;
    }
    if (roundCount === null) {
      setError("社交轮次需要 1 到 6 的整数。");
      return;
    }
    if (!seeds) {
      setError("随机种子需要 1 到 3 个互不重复的整数，范围是 0 到 4294967295。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const project = await apiJson("/api/research/projects", {
        method: "POST",
        body: JSON.stringify({ plan: formToPlan(plan) }),
      });
      const projectId = readId(project);
      if (!projectId) {
        setError("研究项目响应里没有 id。");
        return;
      }
      const created = await apiJson("/api/experiments", {
        method: "POST",
        body: JSON.stringify(startBody(projectId, choice, count, roundCount, seeds)),
      });
      const experimentId = readId(created);
      if (!experimentId) {
        setError("实验响应里没有 id。");
        return;
      }
      rememberExperiment(experimentId);
      await apiJson(`/api/experiments/${experimentId}/run`, { method: "POST", body: "{}" });
      router.push(`/experiments/${experimentId}/graph`);
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto grid max-w-3xl gap-6">
      <div>
        <h1 className="text-2xl font-semibold">实验表单</h1>
        <p className="mt-2 text-sm text-muted-foreground">计划、项目和实验都由接口返回。页面不在浏览器里仿真社会。</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>研究计划</CardTitle>
          <CardDescription>smoke 读取本地案例。live 也在本机生成计划，不把问题发给 Qwen。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="question">研究问题</Label>
            <Textarea
              id="question"
              value={question}
              onChange={(event) => {
                setQuestion(event.target.value);
                setPlan(null);
                setSource(null);
                setConfirmed(false);
              }}
            />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label>模式</Label>
              <Select
                value={choice.mode}
                onValueChange={(value) => {
                  if (isMode(value)) replaceChoice(chooseMode(choice, value));
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="smoke">smoke</SelectItem>
                  <SelectItem value="live">live</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>政策</Label>
              <Select
                value={choice.policy_id}
                onValueChange={(value) => {
                  if (isPolicy(value)) replaceChoice(choosePolicy(choice, value));
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="cn-digital-2026-v1">cn-digital-2026-v1</SelectItem>
                  <SelectItem value="cn-digital-2025-v1">cn-digital-2025-v1</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>设计</Label>
              <Select
                value={choice.design}
                onValueChange={(value) => {
                  if (isDesign(value)) replaceChoice(chooseDesign(choice, value));
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="clarity">clarity</SelectItem>
                  <SelectItem value="factorial" disabled={choice.exposure === "direct"}>
                    factorial
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>暴露</Label>
              <Select
                value={choice.exposure}
                onValueChange={(value) => {
                  if (isExposure(value)) replaceChoice(chooseExposure(choice, value));
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="social">social</SelectItem>
                  <SelectItem value="direct" disabled={choice.design === "factorial"}>
                    direct
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <p className="text-sm text-muted-foreground">direct 与 factorial 不能同时使用。</p>
          <Button disabled={busy} onClick={() => void loadPlan()}>
            {choice.mode === "smoke" ? "加载案例研究计划" : "生成本地研究计划"}
          </Button>
        </CardContent>
      </Card>
      {plan ? (
        <Card>
          <CardHeader>
            <CardTitle>计划正文</CardTitle>
            <CardDescription>
              {source ? `source ${source}` : "接口没有返回 source。"} 修改正文会取消确认。
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="title">研究题目</Label>
              <Input id="title" value={plan.title} onChange={(event) => editPlan({ title: event.target.value })} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="plan-question">研究问题</Label>
              <Textarea
                id="plan-question"
                value={plan.question}
                onChange={(event) => editPlan({ question: event.target.value })}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="hypotheses">假设</Label>
              <Textarea
                id="hypotheses"
                value={plan.hypotheses}
                onChange={(event) => editPlan({ hypotheses: event.target.value })}
              />
              <p className="text-sm text-muted-foreground">每行一条。</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="analysis">分析</Label>
              <Textarea id="analysis" value={plan.analysis} onChange={(event) => editPlan({ analysis: event.target.value })} />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="limitations">局限</Label>
              <Textarea
                id="limitations"
                value={plan.limitations}
                onChange={(event) => editPlan({ limitations: event.target.value })}
              />
              <p className="text-sm text-muted-foreground">每行一条。</p>
            </div>
          </CardContent>
        </Card>
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle>启动实验</CardTitle>
          <CardDescription>activation_rate 0.5，temperature 0.7。这两项随配置提交，页面不改它们。</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-4 md:grid-cols-3">
            <div className="grid gap-2">
              <Label htmlFor="agent-count">消费者数量</Label>
              <Input
                id="agent-count"
                type="number"
                min={10}
                max={60}
                value={agentCount}
                onChange={(event) => setAgentCount(Number(event.target.value))}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="rounds">社交轮次</Label>
              <Input
                id="rounds"
                type="number"
                min={1}
                max={6}
                value={rounds}
                onChange={(event) => setRounds(Number(event.target.value))}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="seeds">随机种子</Label>
              <Input id="seeds" value={seedsText} onChange={(event) => setSeedsText(event.target.value)} />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={!plan}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            确认此研究计划
          </label>
          <Button disabled={busy || !confirmed || !plan} onClick={() => void startExperiment()}>
            确认并启动实验
          </Button>
        </CardContent>
      </Card>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}
