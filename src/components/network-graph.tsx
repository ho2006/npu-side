"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType } from "react";

import type { GraphModel, GraphNode } from "@/lib/domain";

type GraphProps = {
  width: number;
  height: number;
  graphData: { nodes: GraphNode[]; links: { source: number; target: number }[] };
  nodeLabel: "name";
  nodeRelSize: number;
  nodeColor: (node: GraphNode) => string;
  onNodeClick: (node: GraphNode) => void;
  cooldownTicks: number;
  backgroundColor: string;
};

function rgbBackground(color: string): string {
  const sample = document.createElement("canvas");
  sample.width = 1;
  sample.height = 1;
  const context = sample.getContext("2d");
  if (!context) return color;
  context.fillStyle = color;
  context.fillRect(0, 0, 1, 1);
  const [red, green, blue, alpha] = context.getImageData(0, 0, 1, 1).data;
  return `rgba(${red}, ${green}, ${blue}, ${alpha / 255})`;
}

const ForceGraph = dynamic(() => import("react-force-graph-3d").then((mod) => mod.default), {
  ssr: false,
  loading: () => <p className="text-sm text-muted-foreground">正在加载关系图。</p>,
}) as ComponentType<GraphProps>;

export function NetworkGraph({ graph }: { graph: GraphModel }) {
  const host = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [backgroundColor, setBackgroundColor] = useState("#000000");
  const [picked, setPicked] = useState<string | null>(null);
  const graphData = useMemo(
    () => ({
      nodes: graph.nodes.map((node) => ({ ...node })),
      links: graph.links.map((link) => ({ ...link })),
    }),
    [graph],
  );

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const update = () => setWidth(node.clientWidth);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    // Computed colors can be lab(), which the WebGL clear color rejects. Resolve to rgba, and re-read when the theme class flips.
    const readBackground = () => setBackgroundColor(rgbBackground(getComputedStyle(node).backgroundColor));
    readBackground();
    const observer = new MutationObserver(readBackground);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return (
    <div className="grid gap-3">
      <div className="flex gap-4 text-sm">
        <span className="inline-flex items-center gap-2">
          <i className="size-3 rounded-full bg-[#0f766e]" />
          已触达
        </span>
        <span className="inline-flex items-center gap-2">
          <i className="size-3 rounded-full bg-[#94a3b8]" />
          未触达
        </span>
      </div>
      <div ref={host} className="h-[640px] w-full overflow-hidden rounded-xl border bg-background">
        <ForceGraph
          width={width}
          height={640}
          graphData={graphData}
          nodeLabel="name"
          nodeRelSize={6}
          nodeColor={(node) => (node.reached ? "#0f766e" : "#94a3b8")}
          onNodeClick={(node) => setPicked(node.name)}
          cooldownTicks={80}
          backgroundColor={backgroundColor}
        />
      </div>
      <p className="text-sm">{picked ?? "点击节点查看姓名。"}</p>
    </div>
  );
}
