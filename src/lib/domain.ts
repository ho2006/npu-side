export const POLICIES = ["cn-digital-2026-v1", "cn-digital-2025-v1"] as const;
export const MODES = ["smoke", "live"] as const;
export const DESIGNS = ["clarity", "factorial"] as const;
export const EXPOSURES = ["social", "direct"] as const;
export const SOURCE_KINDS = ["fixture", "human"] as const;
export const STAGES = ["pilot", "heldout"] as const;

export const ACTIVATION_RATE = 0.5;
export const TEMPERATURE = 0.7;

export type Mode = (typeof MODES)[number];
export type PolicyId = (typeof POLICIES)[number];
export type Design = (typeof DESIGNS)[number];
export type Exposure = (typeof EXPOSURES)[number];
export type SourceKind = (typeof SOURCE_KINDS)[number];
export type Stage = (typeof STAGES)[number];

export type StudyChoice = {
  mode: Mode;
  policy_id: PolicyId;
} & (
  | { exposure: "social"; design: Design }
  | { exposure: "direct"; design: "clarity" }
);

export type Plan = {
  title: string;
  question: string;
  hypotheses: string[];
  analysis: string;
  limitations: string[];
};

export type PlanForm = {
  title: string;
  question: string;
  hypotheses: string;
  analysis: string;
  limitations: string;
};

export type StartBody = {
  project_id: string;
  mode: Mode;
  policy_id: PolicyId;
  design: Design;
  exposure: Exposure;
  agent_count: number;
  rounds: number;
  seeds: number[];
  activation_rate: 0.5;
  temperature: 0.7;
  confirmed: true;
};

export type ProtocolRecord = {
  id: string;
  source_kind: SourceKind;
  protocol_sha256: string;
  material: { material_sha256: string; survey_sha256: string };
};

export type ListedExperiment = {
  id: string;
  status: string;
  title: string;
  exposure: string;
  mode: string;
};

export type ComparisonRow = {
  metric: string;
  A0: string;
  A1: string;
  difference: string;
  seed_min: string;
  seed_max: string;
};

export type FactorRow = {
  factor: string;
  metric: string;
  mean: string;
  seed_min: string;
  seed_max: string;
};

export type MetricsView = {
  comparison: ComparisonRow[];
  factorial: FactorRow[];
  definitions: { metric: string; text: string }[];
  runs: { key: string; cells: Record<string, string> }[];
  unit: string | null;
};

export const RUN_COLUMNS = [
  "condition",
  "seed",
  "n",
  "comprehension",
  "item_accuracy",
  "reach",
  "intention",
  "intention_change",
] as const;

export type GraphNode = {
  id: number;
  name: string;
  group: string;
  reached: boolean;
};

export type GraphLink = { source: number; target: number };

export type GraphModel = {
  nodes: GraphNode[];
  links: GraphLink[];
  eventNote: string | null;
};

export const GRAPH_EVENT_LIMIT = 500;

export type TableModel = {
  columns: { key: string; header: string }[];
  rows: { key: string; cells: Record<string, string> }[];
};

export type BoardView = {
  device: string;
  runtime: string;
  nDevelopment: number;
  externalValidity: string;
  fits: TableModel;
  heldout: TableModel;
  agents: TableModel;
};

export type CheckView = {
  matchesFit: boolean | null;
  agents: TableModel;
};

export type ExperimentHeader = {
  id: string;
  status: string;
  title: string;
  error: string | null;
};

export type ReachSummary = {
  reached: number;
  total: number;
  mix: { group: string; n: number; reached: number }[];
};

const SEED_MAX = 2 ** 32 - 1;

export function initialChoice(): StudyChoice {
  return {
    mode: "smoke",
    policy_id: "cn-digital-2026-v1",
    exposure: "social",
    design: "clarity",
  };
}

export function chooseMode(choice: StudyChoice, mode: Mode): StudyChoice {
  return { ...choice, mode };
}

export function choosePolicy(choice: StudyChoice, policy_id: PolicyId): StudyChoice {
  return { ...choice, policy_id };
}

export function chooseExposure(choice: StudyChoice, exposure: Exposure): StudyChoice {
  if (exposure === "direct") {
    return { mode: choice.mode, policy_id: choice.policy_id, exposure: "direct", design: "clarity" };
  }
  return { mode: choice.mode, policy_id: choice.policy_id, exposure: "social", design: choice.design };
}

export function chooseDesign(choice: StudyChoice, design: Design): StudyChoice {
  if (design === "factorial") {
    return { mode: choice.mode, policy_id: choice.policy_id, exposure: "social", design: "factorial" };
  }
  if (choice.exposure === "direct") {
    return { mode: choice.mode, policy_id: choice.policy_id, exposure: "direct", design: "clarity" };
  }
  return { mode: choice.mode, policy_id: choice.policy_id, exposure: "social", design: "clarity" };
}

export function isMode(value: string): value is Mode {
  return value === "smoke" || value === "live";
}

export function isPolicy(value: string): value is PolicyId {
  return value === "cn-digital-2026-v1" || value === "cn-digital-2025-v1";
}

export function isDesign(value: string): value is Design {
  return value === "clarity" || value === "factorial";
}

export function isExposure(value: string): value is Exposure {
  return value === "social" || value === "direct";
}

export function isSourceKind(value: string): value is SourceKind {
  return value === "fixture" || value === "human";
}

export function isStage(value: string): value is Stage {
  return value === "pilot" || value === "heldout";
}

export function planRequest(question: string, choice: StudyChoice) {
  return {
    question: question.trim(),
    mode: choice.mode,
    policy_id: choice.policy_id,
    design: choice.design,
    exposure: choice.exposure,
  };
}

export function readPlan(value: unknown): { plan: Plan; source: string | null } | null {
  if (!isRecord(value) || !isRecord(value.plan)) return null;
  const row = value.plan;
  if (typeof row.title !== "string" || typeof row.question !== "string" || typeof row.analysis !== "string") return null;
  if (!stringList(row.hypotheses) || !stringList(row.limitations)) return null;
  return {
    plan: {
      title: row.title,
      question: row.question,
      hypotheses: row.hypotheses,
      analysis: row.analysis,
      limitations: row.limitations,
    },
    source: typeof value.source === "string" ? value.source : null,
  };
}

export function planToForm(plan: Plan): PlanForm {
  return {
    title: plan.title,
    question: plan.question,
    hypotheses: plan.hypotheses.join("\n"),
    analysis: plan.analysis,
    limitations: plan.limitations.join("\n"),
  };
}

export function formToPlan(form: PlanForm): Plan {
  return {
    title: form.title.trim(),
    question: form.question.trim(),
    hypotheses: lines(form.hypotheses),
    analysis: form.analysis.trim(),
    limitations: lines(form.limitations),
  };
}

export function readId(value: unknown): string | null {
  if (!isRecord(value) || typeof value.id !== "string" || !value.id) return null;
  return value.id;
}

export function parseBoundedInteger(value: number, min: number, max: number): number | null {
  if (!Number.isInteger(value) || value < min || value > max) return null;
  return value;
}

export function parseSeeds(text: string): number[] | null {
  const parts = text.split(/[\s,]+/).filter(Boolean);
  if (parts.length < 1 || parts.length > 3) return null;
  const seeds: number[] = [];
  for (const part of parts) {
    if (!/^\d+$/.test(part)) return null;
    const value = Number(part);
    if (!Number.isSafeInteger(value) || value > SEED_MAX) return null;
    seeds.push(value);
  }
  if (new Set(seeds).size !== seeds.length) return null;
  return seeds;
}

export function startBody(
  projectId: string,
  choice: StudyChoice,
  agentCount: number,
  rounds: number,
  seeds: number[],
): StartBody {
  return {
    project_id: projectId,
    mode: choice.mode,
    policy_id: choice.policy_id,
    design: choice.design,
    exposure: choice.exposure,
    agent_count: agentCount,
    rounds,
    seeds,
    activation_rate: 0.5,
    temperature: 0.7,
    confirmed: true,
  };
}

export function readExperiments(value: unknown): ListedExperiment[] | null {
  if (!Array.isArray(value)) return null;
  const rows: ListedExperiment[] = [];
  for (const item of value) {
    if (!isRecord(item) || typeof item.id !== "string") return null;
    const config = isRecord(item.config) ? item.config : {};
    const plan = isRecord(item.plan) ? item.plan : {};
    rows.push({
      id: item.id,
      status: typeof item.status === "string" ? item.status : "",
      title: typeof plan.title === "string" ? plan.title : item.id,
      exposure: typeof config.exposure === "string" ? config.exposure : "",
      mode: typeof config.mode === "string" ? config.mode : "",
    });
  }
  return rows;
}

export function baselineOptions(rows: ListedExperiment[], source: SourceKind): ListedExperiment[] {
  return rows.filter(
    (row) => row.status === "COMPLETED" && row.exposure === "direct" && (source === "fixture" || row.mode === "live"),
  );
}

export function readProtocol(value: unknown): ProtocolRecord | null {
  if (!isRecord(value) || typeof value.id !== "string" || typeof value.protocol_sha256 !== "string") return null;
  if (!isRecord(value.material)) return null;
  if (typeof value.material.material_sha256 !== "string" || typeof value.material.survey_sha256 !== "string") return null;
  if (typeof value.source_kind !== "string" || !isSourceKind(value.source_kind)) return null;
  return {
    id: value.id,
    source_kind: value.source_kind,
    protocol_sha256: value.protocol_sha256,
    material: {
      material_sha256: value.material.material_sha256,
      survey_sha256: value.material.survey_sha256,
    },
  };
}

export function calibrationTemplate(protocol: ProtocolRecord): string {
  return JSON.stringify(
    {
      source_kind: protocol.source_kind,
      protocol_sha256: protocol.protocol_sha256,
      material_sha256: protocol.material.material_sha256,
      survey_sha256: protocol.material.survey_sha256,
      participants: [],
    },
    null,
    2,
  );
}

export function readImportResult(value: unknown): { status: string; externalValidity: string } | null {
  if (!isRecord(value) || !isRecord(value.result)) return null;
  if (typeof value.result.status !== "string" || typeof value.result.external_validity !== "string") return null;
  return { status: value.result.status, externalValidity: value.result.external_validity };
}

export function readMetrics(value: unknown): MetricsView | null {
  if (!isRecord(value) || !Array.isArray(value.comparison)) return null;
  const comparison: ComparisonRow[] = [];
  for (const item of value.comparison) {
    if (!isRecord(item) || typeof item.metric !== "string") return null;
    comparison.push({
      metric: item.metric,
      A0: textCell(item.A0),
      A1: textCell(item.A1),
      difference: textCell(item.difference),
      seed_min: textCell(item.seed_min),
      seed_max: textCell(item.seed_max),
    });
  }
  const factorial: FactorRow[] = [];
  if (value.factorial_effects !== undefined) {
    if (!Array.isArray(value.factorial_effects)) return null;
    for (const item of value.factorial_effects) {
      if (!isRecord(item) || typeof item.factor !== "string" || typeof item.metric !== "string") return null;
      factorial.push({
        factor: item.factor,
        metric: item.metric,
        mean: textCell(item.mean),
        seed_min: textCell(item.seed_min),
        seed_max: textCell(item.seed_max),
      });
    }
  }
  const definitions: { metric: string; text: string }[] = [];
  if (isRecord(value.definitions)) {
    for (const [metric, text] of Object.entries(value.definitions)) {
      if (typeof text === "string") definitions.push({ metric, text });
    }
  }
  const runs: MetricsView["runs"] = [];
  if (Array.isArray(value.runs)) {
    value.runs.forEach((item, index) => {
      if (!isRecord(item)) return;
      const cells: Record<string, string> = {};
      for (const key of RUN_COLUMNS) cells[key] = textCell(item[key]);
      const condition = cells.condition || String(index);
      const seed = cells.seed || String(index);
      runs.push({ key: `${condition}-${seed}-${index}`, cells });
    });
  }
  return {
    comparison,
    factorial,
    definitions,
    runs,
    unit: typeof value.unit === "string" ? value.unit : null,
  };
}

export function readGraph(population: unknown, eventsBody: unknown): GraphModel | null {
  if (!isRecord(population) || !Array.isArray(population.people) || !Array.isArray(population.edges)) return null;
  if (!isRecord(eventsBody) || !Array.isArray(eventsBody.events)) return null;
  const nodes: GraphNode[] = [];
  const ids = new Set<number>();
  for (const person of population.people) {
    if (!isRecord(person) || typeof person.name !== "string") return null;
    const id = readWholeNumber(person.id);
    if (id === null) return null;
    if (ids.has(id)) return null;
    ids.add(id);
    nodes.push({
      id,
      name: person.name,
      group: typeof person.group === "string" ? person.group : "",
      reached: false,
    });
  }
  const links: GraphLink[] = [];
  for (const edge of population.edges) {
    if (!isRecord(edge)) return null;
    const source = readWholeNumber(edge.source);
    const target = readWholeNumber(edge.target);
    if (source === null || target === null || !ids.has(source) || !ids.has(target)) return null;
    links.push({ source, target });
  }
  const reached = new Set<number>();
  for (const event of eventsBody.events) {
    if (!isRecord(event) || typeof event.action !== "string") continue;
    const agentId = readWholeNumber(event.agent_id);
    if (agentId === null || !ids.has(agentId)) continue;
    if (event.action === "direct_exposure" || (event.action === "feed_observed" && hasPolicyPosts(event.info))) {
      reached.add(agentId);
    }
  }
  for (const node of nodes) node.reached = reached.has(node.id);
  const total = typeof eventsBody.total === "number" ? eventsBody.total : null;
  const eventNote =
    total !== null && total > eventsBody.events.length
      ? `事件接口本次返回 ${eventsBody.events.length} 条，合计 ${total} 条。触达只根据返回的这些事件计算。`
      : null;
  return { nodes, links, eventNote };
}

export function readExperimentHeader(value: unknown): ExperimentHeader | null {
  if (!isRecord(value) || typeof value.id !== "string" || !value.id || typeof value.status !== "string") return null;
  const plan = isRecord(value.plan) ? value.plan : null;
  const title = plan && typeof plan.title === "string" ? plan.title : "";
  if (value.error !== undefined && value.error !== null && typeof value.error !== "string") return null;
  return {
    id: value.id,
    status: value.status,
    title,
    error: typeof value.error === "string" ? value.error : null,
  };
}

export function readBaselineId(calibrationGet: unknown): string | null {
  if (!isRecord(calibrationGet) || !isRecord(calibrationGet.protocol)) return null;
  const baselineId = calibrationGet.protocol.baseline_id;
  if (typeof baselineId !== "string" || !baselineId) return null;
  return baselineId;
}

export function readRunStatus(value: unknown): "INFERRED" | "INSUFFICIENT_SAMPLE" | null {
  if (!isRecord(value)) return null;
  if (value.status === "INFERRED" || value.status === "INSUFFICIENT_SAMPLE") return value.status;
  return null;
}

export function readBoardView(value: unknown): BoardView | null {
  if (!isRecord(value) || value.status !== "INFERRED") return null;
  if (typeof value.device !== "string" || typeof value.runtime !== "string") return null;
  if (typeof value.n_development !== "number" || !Number.isFinite(value.n_development)) return null;
  if (typeof value.external_validity !== "string") return null;
  const algorithms = readAlgorithms(value.algorithms);
  if (!algorithms) return null;
  const ids = algorithms.map((algorithm) => algorithm.id);
  if (!isRecord(value.heldout)) return null;
  const itemBrier = scoreCells(value.heldout.item_brier, ids, true);
  const intentionMae = scoreCells(value.heldout.intention_mae, ids, true);
  if (!itemBrier || !intentionMae) return null;
  if (!Array.isArray(value.agents)) return null;
  const agentRows: TableModel["rows"] = [];
  for (const [index, agent] of value.agents.entries()) {
    if (!isRecord(agent) || typeof agent.condition !== "string") return null;
    if (typeof agent.seed !== "number" || !Number.isFinite(agent.seed)) return null;
    if (typeof agent.n !== "number" || !Number.isFinite(agent.n)) return null;
    const agentBrier = scoreCells(agent.item_brier, ids, false);
    const agentMae = scoreCells(agent.intention_mae, ids, false);
    if (!agentBrier || !agentMae) return null;
    const cells: Record<string, string> = {
      condition: agent.condition,
      seed: String(agent.seed),
      n: String(agent.n),
    };
    for (const id of ids) {
      cells[`${id}.item_brier`] = agentBrier[id] ?? "";
      cells[`${id}.intention_mae`] = agentMae[id] ?? "";
    }
    agentRows.push({ key: `${agent.condition}-${agent.seed}-${index}`, cells });
  }
  return {
    device: value.device,
    runtime: value.runtime,
    nDevelopment: value.n_development,
    externalValidity: value.external_validity,
    fits: {
      columns: [
        { key: "id", header: "id" },
        { key: "fit", header: "fit" },
      ],
      rows: algorithms.map((algorithm) => ({
        key: algorithm.id,
        cells: { id: algorithm.id, fit: fitText(algorithm.fit) },
      })),
    },
    heldout: {
      columns: [{ key: "metric", header: "metric" }, ...ids.map((id) => ({ key: id, header: id }))],
      rows: [
        { key: "item_brier", cells: { metric: "item_brier", ...itemBrier } },
        { key: "intention_mae", cells: { metric: "intention_mae", ...intentionMae } },
      ],
    },
    agents: {
      columns: agentColumns(ids),
      rows: agentRows,
    },
  };
}

export function readCheckView(value: unknown): CheckView | null {
  if (!isRecord(value) || !Array.isArray(value.agents)) return null;
  const matchesFit = value.matches_fit;
  if (matchesFit !== null && typeof matchesFit !== "boolean") return null;
  const ids = algorithmIds(value.agents);
  if (!ids) return null;
  const rows: TableModel["rows"] = [];
  for (const [index, agent] of value.agents.entries()) {
    if (!isRecord(agent) || typeof agent.condition !== "string") return null;
    if (typeof agent.seed !== "number" || !Number.isFinite(agent.seed)) return null;
    if (typeof agent.n !== "number" || !Number.isFinite(agent.n)) return null;
    const itemBrier = scoreCells(agent.item_brier, ids, false);
    const intentionMae = scoreCells(agent.intention_mae, ids, false);
    if (!itemBrier || !intentionMae) return null;
    const cells: Record<string, string> = {
      condition: agent.condition,
      seed: String(agent.seed),
      n: String(agent.n),
    };
    for (const id of ids) {
      cells[`${id}.item_brier`] = itemBrier[id] ?? "";
      cells[`${id}.intention_mae`] = intentionMae[id] ?? "";
    }
    rows.push({ key: `${agent.condition}-${agent.seed}-${index}`, cells });
  }
  return {
    matchesFit,
    agents: { columns: agentColumns(ids), rows },
  };
}

export type ReproductionCard = {
  research_id: string;
  status: "COMPLETED";
  title: string;
  choice: {
    mode: "live";
    design: "clarity";
    exposure: "social";
    policy_id: "cn-digital-2026-v1";
    activation_rate: 0.5;
    temperature: 0.7;
    agent_count: number;
    rounds: number;
    seeds: number[];
  };
  occupancy: {
    device: "NPU";
    full_name: string;
    peak_percent: number;
    engine: string;
    burst_inferences: number;
  } | null;
  compiled: boolean;
  matches_fit: boolean | null;
};

export function readReproductionCard(value: unknown): ReproductionCard | null {
  if (!isRecord(value) || value.status !== "COMPLETED") return null;
  if (typeof value.research_id !== "string" || value.research_id === "") return null;
  if (typeof value.title !== "string" || value.title === "") return null;
  if (typeof value.compiled !== "boolean") return null;
  if (value.matches_fit !== null && typeof value.matches_fit !== "boolean") return null;
  const choice = readReproductionChoice(value.choice);
  if (!choice) return null;
  const occupancy = readOccupancy(value.occupancy);
  if (occupancy === undefined) return null;
  return {
    research_id: value.research_id,
    status: "COMPLETED",
    title: value.title,
    choice,
    occupancy,
    compiled: value.compiled,
    matches_fit: value.matches_fit,
  };
}

export function readReproductionList(value: unknown): ReproductionCard[] | null {
  if (!Array.isArray(value)) return null;
  const rows: ReproductionCard[] = [];
  for (const item of value) {
    const card = readReproductionCard(item);
    if (!card) return null;
    rows.push(card);
  }
  return rows;
}

export type ConsumerRow = {
  condition: "A0" | "A1";
  seed: number;
  agentId: number;
  name: string;
  group: string;
  age: number;
  budget: number;
  postIntention: number;
  highIntent: boolean;
  behavior: number | null;
  probability: number | null;
  credibility: number | null;
};

export const TRAIN_PHASES = ["idle", "fitting", "compiling", "scoring", "sampling", "done", "failed"] as const;

export type TrainPhase = (typeof TRAIN_PHASES)[number];

export type TrainStatus = {
  phase: TrainPhase;
  step: number;
  total: 6;
  message: string;
  error: string | null;
};

export function readTrainStatus(value: unknown): TrainStatus | null {
  if (!isRecord(value)) return null;
  if (typeof value.phase !== "string" || !isTrainPhase(value.phase)) return null;
  if (typeof value.step !== "number" || !Number.isInteger(value.step) || value.step < 0 || value.step > 6) return null;
  if (value.total !== 6) return null;
  if (typeof value.message !== "string" || value.message === "") return null;
  if (!("error" in value) || (value.error !== null && typeof value.error !== "string")) return null;
  if (value.phase === "done" && value.step !== 6) return null;
  if (value.phase === "idle" && value.step !== 0) return null;
  if (value.phase !== "idle" && value.phase !== "failed" && value.phase !== "done" && value.step === 0) return null;
  return {
    phase: value.phase,
    step: value.step,
    total: 6,
    message: value.message,
    error: typeof value.error === "string" ? value.error : null,
  };
}

export function readConsumers(value: unknown): ConsumerRow[] | null {
  if (!Array.isArray(value)) return null;
  const rows: ConsumerRow[] = [];
  for (const item of value) {
    const row = readConsumer(item);
    if (!row) return null;
    rows.push(row);
  }
  return rows;
}

export function readReproductionDetail(
  value: unknown,
): { card: ReproductionCard; metrics: unknown; population: unknown; events: unknown; consumers: ConsumerRow[] } | null {
  if (!isRecord(value)) return null;
  const card = readReproductionCard(value.card);
  const consumers = readConsumers(value.consumers);
  if (!card || !consumers || !isRecord(value.population) || !isRecord(value.events)) return null;
  return { card, metrics: value.metrics, population: value.population, events: value.events, consumers };
}

function isTrainPhase(value: string): value is TrainPhase {
  return (TRAIN_PHASES as readonly string[]).includes(value);
}

function readConsumer(value: unknown): ConsumerRow | null {
  if (!isRecord(value)) return null;
  if (value.condition !== "A0" && value.condition !== "A1") return null;
  if (typeof value.seed !== "number" || !Number.isInteger(value.seed)) return null;
  if (typeof value.agent_id !== "number" || !Number.isInteger(value.agent_id)) return null;
  if (typeof value.name !== "string" || value.name === "") return null;
  if (typeof value.group !== "string" || value.group === "") return null;
  if (typeof value.age !== "number" || !Number.isInteger(value.age)) return null;
  if (typeof value.budget !== "number" || !Number.isInteger(value.budget)) return null;
  if (typeof value.post_intention !== "number" || !Number.isInteger(value.post_intention)) return null;
  if (value.post_intention < 1 || value.post_intention > 5) return null;
  if (typeof value.high_intent !== "boolean") return null;
  if (value.high_intent !== (value.post_intention >= 4)) return null;
  const behavior = readScore(value.behavior);
  const probability = readScore(value.probability);
  const credibility = readScore(value.credibility);
  if (behavior === undefined || probability === undefined || credibility === undefined) return null;
  const filled = [behavior, probability, credibility].filter((item) => item !== null).length;
  if (filled !== 0 && filled !== 3) return null;
  return {
    condition: value.condition,
    seed: value.seed,
    agentId: value.agent_id,
    name: value.name,
    group: value.group,
    age: value.age,
    budget: value.budget,
    postIntention: value.post_intention,
    highIntent: value.high_intent,
    behavior,
    probability,
    credibility,
  };
}

function readScore(value: unknown): number | null | undefined {
  if (value === null) return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return undefined;
}

function readReproductionChoice(value: unknown): ReproductionCard["choice"] | null {
  if (!isRecord(value)) return null;
  if (value.mode !== "live" || value.design !== "clarity" || value.exposure !== "social") return null;
  if (value.policy_id !== "cn-digital-2026-v1" || value.activation_rate !== 0.5 || value.temperature !== 0.7) return null;
  if (typeof value.agent_count !== "number" || !Number.isInteger(value.agent_count)) return null;
  if (typeof value.rounds !== "number" || !Number.isInteger(value.rounds)) return null;
  if (!Array.isArray(value.seeds)) return null;
  const seeds: number[] = [];
  for (const seed of value.seeds) {
    if (typeof seed !== "number" || !Number.isInteger(seed)) return null;
    seeds.push(seed);
  }
  return {
    mode: "live",
    design: "clarity",
    exposure: "social",
    policy_id: "cn-digital-2026-v1",
    activation_rate: 0.5,
    temperature: 0.7,
    agent_count: value.agent_count,
    rounds: value.rounds,
    seeds,
  };
}

function readOccupancy(value: unknown): ReproductionCard["occupancy"] | undefined {
  if (value === null) return null;
  if (!isRecord(value) || value.device !== "NPU") return undefined;
  if (typeof value.full_name !== "string" || typeof value.engine !== "string") return undefined;
  if (typeof value.peak_percent !== "number" || !Number.isFinite(value.peak_percent)) return undefined;
  if (typeof value.burst_inferences !== "number" || !Number.isInteger(value.burst_inferences)) return undefined;
  return {
    device: "NPU",
    full_name: value.full_name,
    peak_percent: value.peak_percent,
    engine: value.engine,
    burst_inferences: value.burst_inferences,
  };
}

function agentColumns(ids: string[]): TableModel["columns"] {
  return [
    { key: "condition", header: "condition" },
    { key: "seed", header: "seed" },
    { key: "n", header: "n" },
    ...ids.flatMap((id) => [
      { key: `${id}.item_brier`, header: `${id}.item_brier` },
      { key: `${id}.intention_mae`, header: `${id}.intention_mae` },
    ]),
  ];
}

function algorithmIds(agents: unknown[]): string[] | null {
  if (agents.length === 0) return [];
  const first = agents[0];
  if (!isRecord(first) || !isRecord(first.item_brier)) return null;
  const ids = Object.keys(first.item_brier);
  if (ids.length === 0 || ids.some((id) => !id)) return null;
  return ids;
}

export function reachSummary(graph: GraphModel): ReachSummary {
  const byGroup = new Map<string, { n: number; reached: number }>();
  let reached = 0;
  for (const node of graph.nodes) {
    if (node.reached) reached += 1;
    const counts = byGroup.get(node.group) ?? { n: 0, reached: 0 };
    counts.n += 1;
    if (node.reached) counts.reached += 1;
    byGroup.set(node.group, counts);
  }
  const mix = [...byGroup.entries()]
    .sort(([left], [right]) => left.localeCompare(right, "zh"))
    .map(([group, counts]) => ({ group, n: counts.n, reached: counts.reached }));
  return { reached, total: graph.nodes.length, mix };
}

function readAlgorithms(value: unknown): { id: string; fit: Record<string, string | number> }[] | null {
  if (!Array.isArray(value)) return null;
  const algorithms: { id: string; fit: Record<string, string | number> }[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    if (!isRecord(item) || typeof item.id !== "string" || !item.id || seen.has(item.id)) return null;
    const fit = readFit(item.fit);
    if (!fit) return null;
    seen.add(item.id);
    algorithms.push({ id: item.id, fit });
  }
  return algorithms;
}

function readFit(value: unknown): Record<string, string | number> | null {
  if (!isRecord(value)) return null;
  const fit: Record<string, string | number> = {};
  for (const [key, cell] of Object.entries(value)) {
    if (typeof cell === "string") fit[key] = cell;
    else if (typeof cell === "number" && Number.isFinite(cell)) fit[key] = cell;
    else return null;
  }
  return fit;
}

function fitText(fit: Record<string, string | number>): string {
  const ordered: Record<string, string | number> = {};
  for (const key of Object.keys(fit).sort()) {
    const cell = fit[key];
    if (cell !== undefined) ordered[key] = cell;
  }
  return JSON.stringify(ordered);
}

function scoreCells(value: unknown, ids: string[], allowNull: boolean): Record<string, string> | null {
  if (!isRecord(value) || Object.keys(value).length !== ids.length) return null;
  const cells: Record<string, string> = {};
  for (const id of ids) {
    if (!Object.prototype.hasOwnProperty.call(value, id)) return null;
    const cell = value[id];
    if (cell === null && allowNull) {
      cells[id] = "";
      continue;
    }
    if (typeof cell !== "number" || !Number.isFinite(cell)) return null;
    cells[id] = String(cell);
  }
  return cells;
}

function hasPolicyPosts(info: unknown): boolean {
  if (!isRecord(info)) return false;
  const ids = info.policy_post_ids;
  if (Array.isArray(ids)) return ids.length > 0;
  return Boolean(ids);
}

function readWholeNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isInteger(value)) return value;
  if (typeof value === "string" && /^\d+$/.test(value)) {
    const parsed = Number(value);
    return Number.isSafeInteger(parsed) ? parsed : null;
  }
  return null;
}

function textCell(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string") return value;
  return "";
}

function lines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function stringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
