"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { NetworkGraph } from "@/components/network-graph";
import { apiJson, errorText } from "@/lib/api";
import { GRAPH_EVENT_LIMIT, readGraph, type GraphModel } from "@/lib/domain";
import { rememberExperiment } from "@/lib/selection";

export default function GraphPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [graph, setGraph] = useState<GraphModel | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!id) return;
    rememberExperiment(id);
    let cancelled = false;
    Promise.all([
      apiJson(`/api/experiments/${id}/population`),
      apiJson(`/api/experiments/${id}/events?limit=${GRAPH_EVENT_LIMIT}`),
    ])
      .then(([population, events]) => {
        if (cancelled) return;
        const model = readGraph(population, events);
        if (!model) {
          setError("人口或事件响应无法组成关系图。");
          setGraph(null);
          return;
        }
        setGraph(model);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(errorText(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-2xl font-semibold">关系图</h1>
        <p className="mt-2 text-sm text-muted-foreground">节点和边来自人口接口。触达来自 feed_observed 的 policy_post_ids，或 direct_exposure。这里没有检索语料。</p>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {!graph && !error ? <p className="text-sm text-muted-foreground">正在读取人口和事件。</p> : null}
      {graph?.eventNote ? <p className="text-sm text-muted-foreground">{graph.eventNote}</p> : null}
      {graph ? <NetworkGraph graph={graph} /> : null}
    </div>
  );
}
