"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Download, LayoutDashboard, Network, Scale } from "lucide-react";
import type { LucideIcon } from "lucide-react";

const EXPERIMENT_KEY = "npu-side.experimentId";
const PROTOCOL_KEY = "npu-side.protocolId";

export const EXPERIMENT_SEGMENTS = ["compare", "export", "graph", "board"] as const;
export type ExperimentSegment = (typeof EXPERIMENT_SEGMENTS)[number];

export const EXPERIMENT_NAV = [
  { segment: "compare", label: "对比", icon: Scale },
  { segment: "export", label: "导出", icon: Download },
  { segment: "graph", label: "关系图", icon: Network },
  { segment: "board", label: "仪表盘", icon: LayoutDashboard },
] as const satisfies readonly { segment: ExperimentSegment; label: string; icon: LucideIcon }[];

type UncoveredSegment = Exclude<ExperimentSegment, (typeof EXPERIMENT_NAV)[number]["segment"]>;
const EXPERIMENT_NAV_COVERS_SEGMENTS: UncoveredSegment extends never ? true : never = true;
void EXPERIMENT_NAV_COVERS_SEGMENTS;

const EXPERIMENT_PATH = new RegExp(`^/experiments/([^/]+)/(?:${EXPERIMENT_SEGMENTS.join("|")})/?$`);

export function rememberExperiment(id: string) {
  sessionStorage.setItem(EXPERIMENT_KEY, id);
}

export function rememberProtocol(id: string) {
  sessionStorage.setItem(PROTOCOL_KEY, id);
}

export function storedProtocolId(): string {
  return sessionStorage.getItem(PROTOCOL_KEY) ?? "";
}

export function experimentIdFromPath(pathname: string): string | null {
  const match = pathname.match(EXPERIMENT_PATH);
  if (!match?.[1]) return null;
  return decodeURIComponent(match[1]);
}

export function useSelectedExperiment(): string | null {
  const pathname = usePathname();
  const [id, setId] = useState<string | null>(null);

  useEffect(() => {
    const fromPath = experimentIdFromPath(pathname);
    const fromQuery = new URLSearchParams(window.location.search).get("experiment");
    const next = fromPath || fromQuery || sessionStorage.getItem(EXPERIMENT_KEY);
    if (next) sessionStorage.setItem(EXPERIMENT_KEY, next);
    setId(next);
  }, [pathname]);

  return id;
}
