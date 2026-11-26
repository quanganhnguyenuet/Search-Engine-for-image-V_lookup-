"use client";

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import {
  type KeyboardEvent,
  type PointerEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import {
  type GraphEdge,
  type GraphNode,
  NODE_LABEL,
  NODE_TYPES,
  type NodeType,
  PROP_LABEL,
  REL_SHORT,
  REL_TEXT,
  type Subgraph,
} from "@/lib/graph";
import { cn } from "@/lib/utils";

type SimNode = GraphNode & SimulationNodeDatum;
type SimLink = SimulationLinkDatum<SimNode> & { edge: GraphEdge };
type View = { x: number; y: number; w: number; h: number };

const MOVE_THRESHOLD = 4; // px; below it a press is a click, not a drag
const LABEL_MAX = 24;
const EDGE_LABELS_ALL_BELOW = 17;

function radius(node: GraphNode): number {
  if (node.role === "origin") return 22;
  if (node.role === "seed") return 17;
  if (node.role === "sibling") return 12;
  return 14;
}

function shorten(text: string): string {
  return text.length > LABEL_MAX ? `${text.slice(0, LABEL_MAX - 1)}…` : text;
}

/** Shape per type, so types stay apart without colour: circle, square, diamond, hexagon. */
function shapeOf(type: NodeType): "circle" | "square" | "diamond" | "hexagon" {
  if (type === "entity") return "circle";
  if (type === "brand") return "square";
  if (type === "category") return "diamond";
  return "hexagon";
}

function polygon(shape: "diamond" | "hexagon", r: number): string {
  if (shape === "diamond") {
    const d = r * 1.25;
    return `0,${-d} ${d},0 0,${d} ${-d},0`;
  }
  return Array.from({ length: 6 }, (_, i) => {
    const angle = (Math.PI / 3) * i;
    return `${(r * 1.1 * Math.cos(angle)).toFixed(1)},${(r * 1.1 * Math.sin(angle)).toFixed(1)}`;
  }).join(" ");
}

export function NodeGlyph({ type, size = 16 }: { type: NodeType; size?: number }) {
  const r = size / 2 - 1.5;
  const fill = `hsl(var(--node-${type}))`;
  const shape = shapeOf(type);
  return (
    <svg width={size} height={size} viewBox={`${-size / 2} ${-size / 2} ${size} ${size}`} aria-hidden="true">
      {shape === "circle" && <circle r={r} fill={fill} />}
      {shape === "square" && <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={2} fill={fill} />}
      {(shape === "diamond" || shape === "hexagon") && <polygon points={polygon(shape, r * 0.8)} fill={fill} />}
    </svg>
  );
}

export type RelationGraphProps = {
  graph: Subgraph;
  /** Entity id -> image URL, drawn inside that entity's node. */
  thumbs?: Record<string, string>;
  /** Entity id -> number of result images, shown in the node panel. */
  imageCounts?: Record<string, number>;
  /** "side": node panel beside the graph on wide screens; "stacked": always below it. */
  layout?: "side" | "stacked";
  className?: string;
};

export function RelationGraph({
  graph,
  thumbs = {},
  imageCounts = {},
  layout: arrangement = "side",
  className,
}: RelationGraphProps) {
  const uid = useId().replace(/:/g, "");
  const svgRef = useRef<SVGSVGElement>(null);
  const simRef = useRef<Simulation<SimNode, SimLink> | null>(null);
  const gesture = useRef<{
    kind: "node" | "pan";
    node?: SimNode;
    startX: number;
    startY: number;
    moved: boolean;
  } | null>(null);
  const reducedMotion = useRef(false);
  const [, redraw] = useReducer((n: number) => n + 1, 0);
  const [layout, setLayout] = useState<{ nodes: SimNode[]; links: SimLink[] } | null>(null);
  const [view, setView] = useState<View>({ x: -300, y: -200, w: 600, h: 400 });
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);

  useEffect(() => {
    reducedMotion.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }, []);

  const build = useCallback(() => {
    const ids = new Set(graph.nodes.map((n) => n.id));
    const nodes: SimNode[] = graph.nodes.map((n) => ({ ...n }));
    const links: SimLink[] = graph.edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, edge: e }));
    const sim = forceSimulation<SimNode>(nodes)
      .force(
        "link",
        forceLink<SimNode, SimLink>(links)
          .id((d) => d.id)
          .distance(96)
          .strength(0.65),
      )
      .force("charge", forceManyBody<SimNode>().strength(-340))
      .force("center", forceCenter(0, 0))
      .force("x", forceX<SimNode>(0).strength(0.05))
      .force("y", forceY<SimNode>(0).strength(0.05))
      .force("collide", forceCollide<SimNode>((d) => radius(d) + 22))
      .stop();
    sim.tick(320);
    sim.on("tick", () => redraw());
    simRef.current?.stop();
    simRef.current = sim;
    // Fit the view to the settled layout.
    const xs = nodes.map((n) => n.x ?? 0);
    const ys = nodes.map((n) => n.y ?? 0);
    const pad = 80;
    const minX = Math.min(...xs) - pad;
    const maxX = Math.max(...xs) + pad;
    const minY = Math.min(...ys) - pad;
    const maxY = Math.max(...ys) + pad;
    // A small graph keeps a minimum frame, so its nodes and labels are not blown up.
    const w = Math.max(maxX - minX, 560);
    const h = Math.max(maxY - minY, 360);
    setView({ x: (minX + maxX) / 2 - w / 2, y: (minY + maxY) / 2 - h / 2, w, h });
    setLayout({ nodes, links });
  }, [graph]);

  useEffect(() => {
    build();
    setSelected(null);
    return () => {
      simRef.current?.stop();
    };
  }, [build]);

  const byId = useMemo(() => new Map((layout?.nodes ?? []).map((n) => [n.id, n])), [layout]);
  const focusId = hovered ?? selected;
  const neighbours = useMemo(() => {
    const set = new Set<string>();
    if (!focusId || !layout) return set;
    set.add(focusId);
    for (const link of layout.links) {
      const { source, target } = link as { source: SimNode; target: SimNode };
      if (source.id === focusId) set.add(target.id);
      if (target.id === focusId) set.add(source.id);
    }
    return set;
  }, [focusId, layout]);

  // ---- pointer handling: drag a node, drag the background to pan -----------------------
  const toSvg = (event: PointerEvent): { x: number; y: number } => {
    const svg = svgRef.current!;
    const matrix = svg.getScreenCTM()?.inverse();
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix);
    return { x: point.x, y: point.y };
  };

  const onPointerDown = (event: PointerEvent<Element>, node?: SimNode) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = {
      kind: node ? "node" : "pan",
      node,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
    };
  };

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const g = gesture.current;
    if (!g) return;
    if (!g.moved && Math.hypot(event.clientX - g.startX, event.clientY - g.startY) < MOVE_THRESHOLD) return;
    g.moved = true;
    if (g.kind === "node" && g.node) {
      const { x, y } = toSvg(event);
      g.node.fx = x;
      g.node.fy = y;
      if (reducedMotion.current) {
        g.node.x = x;
        g.node.y = y;
        redraw();
      } else {
        simRef.current?.alpha(0.25).restart();
      }
    } else {
      // "meet" fits the whole viewBox, so the tighter axis sets the user-units-per-pixel scale.
      const box = svgRef.current!.getBoundingClientRect();
      const scale = Math.max(view.w / box.width, view.h / box.height);
      const dx = (event.clientX - g.startX) * scale;
      const dy = (event.clientY - g.startY) * scale;
      g.startX = event.clientX;
      g.startY = event.clientY;
      setView((v) => ({ ...v, x: v.x - dx, y: v.y - dy }));
    }
  };

  const endGesture = (event: PointerEvent<SVGSVGElement>) => {
    const g = gesture.current;
    gesture.current = null;
    if (!g) return;
    if (g.node) {
      g.node.fx = null;
      g.node.fy = null;
      if (!g.moved) setSelected((current) => (current === g.node!.id ? null : g.node!.id));
    } else if (!g.moved && event.target === event.currentTarget) {
      setSelected(null);
    }
  };

  const zoom = (factor: number) =>
    setView((v) => {
      const w = Math.min(Math.max(v.w * factor, 120), 4000);
      const h = (v.h * w) / v.w;
      return { x: v.x + (v.w - w) / 2, y: v.y + (v.h - h) / 2, w, h };
    });

  const onNodeKey = (event: KeyboardEvent, id: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setSelected((current) => (current === id ? null : id));
    }
  };

  if (!layout) return null;

  const showAllEdgeLabels = layout.links.length < EDGE_LABELS_ALL_BELOW;
  const present = NODE_TYPES.filter((type) => layout.nodes.some((n) => n.type === type));
  const selectedNode = selected ? byId.get(selected) : undefined;
  const description = `Đồ thị quan hệ gồm ${layout.nodes.length} nút và ${layout.links.length} cạnh. Bảng quan hệ dạng văn bản nằm bên dưới.`;

  return (
    <div className={cn("grid gap-3", arrangement === "side" && "lg:grid-cols-[minmax(0,1fr)_17rem]", className)}>
      <div className="flex min-w-0 flex-col gap-2">
        <div className="relative overflow-hidden rounded-lg border bg-surface">
          <svg
            ref={svgRef}
            viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
            role="group"
            aria-label={description}
            className="h-[22rem] w-full touch-pan-y select-none sm:h-[28rem]"
            onPointerDown={(event) => event.target === event.currentTarget && onPointerDown(event)}
            onPointerMove={onPointerMove}
            onPointerUp={endGesture}
            onPointerCancel={endGesture}
            onWheel={(event) => {
              if (event.ctrlKey || event.metaKey) zoom(event.deltaY > 0 ? 1.12 : 0.89);
            }}
          >
            <defs>
              <marker id={`${uid}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill="hsl(var(--muted-foreground))" />
              </marker>
              {layout.nodes
                .filter((n) => thumbs[n.id])
                .map((n) => (
                  <clipPath key={n.id} id={`${uid}-clip-${n.id}`}>
                    <circle r={radius(n) - 2} />
                  </clipPath>
                ))}
            </defs>

            <g>
              {layout.links.map((link) => {
                const { source, target, edge } = link as { source: SimNode; target: SimNode; edge: GraphEdge };
                const active = !focusId || (neighbours.has(source.id) && neighbours.has(target.id));
                const label = showAllEdgeLabels || (focusId && (source.id === focusId || target.id === focusId));
                const dx = (target.x ?? 0) - (source.x ?? 0);
                const dy = (target.y ?? 0) - (source.y ?? 0);
                const length = Math.hypot(dx, dy) || 1;
                const tr = radius(target) + 3;
                const ex = (target.x ?? 0) - (dx / length) * tr;
                const ey = (target.y ?? 0) - (dy / length) * tr;
                return (
                  <g key={edge.id} opacity={active ? 1 : 0.15}>
                    <line
                      x1={source.x}
                      y1={source.y}
                      x2={ex}
                      y2={ey}
                      stroke="hsl(var(--muted-foreground))"
                      strokeOpacity={0.7}
                      strokeWidth={1.4}
                      strokeDasharray={edge.reviewed === false ? "4 3" : undefined}
                      markerEnd={`url(#${uid}-arrow)`}
                    />
                    {label && (
                      <text
                        x={((source.x ?? 0) + (target.x ?? 0)) / 2}
                        y={((source.y ?? 0) + (target.y ?? 0)) / 2}
                        textAnchor="middle"
                        dy={-3}
                        fontSize={9}
                        className="fill-muted-foreground font-mono"
                        stroke="hsl(var(--surface))"
                        strokeWidth={3}
                        paintOrder="stroke"
                      >
                        {REL_SHORT[edge.rel] ?? edge.rel}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>

            <g>
              {layout.nodes.map((node) => {
                const r = radius(node);
                const shape = shapeOf(node.type);
                const fill = `hsl(var(--node-${node.type}))`;
                const isSelected = selected === node.id;
                const dim = focusId !== null && !neighbours.has(node.id);
                const thumb = thumbs[node.id];
                return (
                  <g
                    key={node.id}
                    className="graph-node"
                    transform={`translate(${node.x ?? 0} ${node.y ?? 0})`}
                    opacity={dim ? 0.2 : 1}
                    tabIndex={0}
                    role="button"
                    aria-pressed={isSelected}
                    aria-label={`${NODE_LABEL[node.type]}: ${node.label}${node.role === "origin" ? " (thực thể gốc của câu)" : ""}`}
                    onKeyDown={(event) => onNodeKey(event, node.id)}
                    onPointerDown={(event) => {
                      event.stopPropagation();
                      onPointerDown(event, node);
                    }}
                    onPointerEnter={() => setHovered(node.id)}
                    onPointerLeave={() => setHovered(null)}
                    onFocus={() => setHovered(node.id)}
                    onBlur={() => setHovered(null)}
                  >
                    <title>{`${NODE_LABEL[node.type]}: ${node.label}`}</title>
                    {shape === "circle" ? (
                      <circle className="node-ring" r={r + 5} fill="none" stroke="hsl(var(--foreground))" strokeWidth={2} />
                    ) : (
                      <rect className="node-ring" x={-r - 5} y={-r - 5} width={2 * r + 10} height={2 * r + 10} rx={6} fill="none" stroke="hsl(var(--foreground))" strokeWidth={2} />
                    )}
                    {node.role === "origin" && (
                      <circle r={r + 4} fill="none" stroke="hsl(var(--foreground))" strokeWidth={1.5} />
                    )}
                    {shape === "circle" && (
                      <circle
                        r={r}
                        fill={fill}
                        fillOpacity={node.role === "sibling" ? 0.55 : 1}
                        stroke={isSelected ? "hsl(var(--foreground))" : "hsl(var(--surface))"}
                        strokeWidth={isSelected ? 3 : 2}
                        strokeDasharray={node.role === "sibling" ? "3 2" : undefined}
                      />
                    )}
                    {shape === "square" && (
                      <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={5} fill={fill} stroke={isSelected ? "hsl(var(--foreground))" : "hsl(var(--surface))"} strokeWidth={isSelected ? 3 : 2} />
                    )}
                    {(shape === "diamond" || shape === "hexagon") && (
                      <polygon points={polygon(shape, r)} fill={fill} stroke={isSelected ? "hsl(var(--foreground))" : "hsl(var(--surface))"} strokeWidth={isSelected ? 3 : 2} strokeLinejoin="round" />
                    )}
                    {thumb && shape === "circle" && (
                      <>
                        <rect x={-r + 2} y={-r + 2} width={2 * r - 4} height={2 * r - 4} fill="white" clipPath={`url(#${uid}-clip-${node.id})`} />
                        <image
                          href={thumb}
                          x={-r + 2}
                          y={-r + 2}
                          width={2 * r - 4}
                          height={2 * r - 4}
                          preserveAspectRatio="xMidYMid slice"
                          clipPath={`url(#${uid}-clip-${node.id})`}
                        />
                      </>
                    )}
                    <text
                      y={r + 14}
                      textAnchor="middle"
                      fontSize={node.role === "sibling" ? 10 : 11}
                      fontWeight={node.role === "origin" || node.role === "seed" ? 600 : 400}
                      className="fill-foreground"
                      stroke="hsl(var(--surface))"
                      strokeWidth={3.5}
                      paintOrder="stroke"
                    >
                      {shorten(node.label)}
                    </text>
                    {node.role === "origin" && (
                      <text y={-r - 9} textAnchor="middle" fontSize={9} className="fill-muted-foreground font-mono" stroke="hsl(var(--surface))" strokeWidth={3} paintOrder="stroke">
                        GỐC
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          </svg>

          <div className="absolute right-2 top-2 flex flex-col gap-1">
            <Button variant="outline" className="h-9 w-9 px-0" onClick={() => zoom(0.8)} aria-label="Phóng to đồ thị">
              +
            </Button>
            <Button variant="outline" className="h-9 w-9 px-0" onClick={() => zoom(1.25)} aria-label="Thu nhỏ đồ thị">
              −
            </Button>
            <Button variant="outline" className="h-9 w-9 px-0 text-xs" onClick={build} aria-label="Sắp xếp lại đồ thị">
              ⟲
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {present.map((type) => (
            <span key={type} className="inline-flex items-center gap-1">
              <NodeGlyph type={type} />
              {NODE_LABEL[type]}
            </span>
          ))}
          <span>Nét đứt: cạnh chưa duyệt hoặc sản phẩm ngữ cảnh</span>
          <span>Kéo nút để di chuyển · kéo nền để dịch · Ctrl + cuộn để zoom</span>
        </div>
        {graph.truncated && (
          <p className="text-xs text-warn">Đồ thị chỉ vẽ phần đầu của danh sách sản phẩm để dễ đọc.</p>
        )}

        <details className="rounded-md border px-3 py-2 text-sm">
          <summary className="cursor-pointer font-medium">Bảng quan hệ dạng văn bản · {layout.links.length}</summary>
          <ul className="mt-2 max-h-60 space-y-1 overflow-auto text-xs">
            {layout.links.map((link) => {
              const { source, target, edge } = link as { source: SimNode; target: SimNode; edge: GraphEdge };
              return (
                <li key={edge.id}>
                  <strong>{source.label}</strong> {REL_TEXT[edge.rel] ?? edge.rel} <strong>{target.label}</strong>
                </li>
              );
            })}
          </ul>
        </details>
      </div>

      <NodePanel
        node={selectedNode}
        links={layout.links as unknown as { source: SimNode; target: SimNode; edge: GraphEdge }[]}
        imageCounts={imageCounts}
        onSelect={setSelected}
      />
    </div>
  );
}

function NodePanel({
  node,
  links,
  imageCounts,
  onSelect,
}: {
  node: SimNode | undefined;
  links: { source: SimNode; target: SimNode; edge: GraphEdge }[];
  imageCounts: Record<string, number>;
  onSelect: (id: string) => void;
}) {
  if (!node) {
    return (
      <aside className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground" aria-live="polite">
        Chọn một nút để xem thuộc tính và các quan hệ của nó.
      </aside>
    );
  }
  const related = links.filter((l) => l.source.id === node.id || l.target.id === node.id);
  const shown = related.length;
  return (
    <aside className="flex min-w-0 flex-col gap-3 rounded-lg border bg-surface p-4 text-sm" aria-live="polite">
      <div>
        <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <NodeGlyph type={node.type} />
          {NODE_LABEL[node.type]}
          {node.role === "origin" && " · gốc của câu"}
          {node.role === "sibling" && " · ngữ cảnh"}
        </p>
        <h3 className="mt-0.5 break-words font-semibold leading-snug">{node.label}</h3>
        {imageCounts[node.id] ? (
          <p className="mt-1 text-xs text-muted-foreground">{imageCounts[node.id]} ảnh trong kết quả</p>
        ) : null}
      </div>

      {Object.keys(node.props).length > 0 && (
        <dl className="space-y-1 text-xs">
          {Object.entries(node.props).map(([key, value]) => (
            <div key={key} className="flex justify-between gap-3">
              <dt className="text-muted-foreground">{PROP_LABEL[key] ?? key}</dt>
              <dd className="text-right font-medium">{value}</dd>
            </div>
          ))}
        </dl>
      )}

      <p className="text-xs text-muted-foreground">
        {node.type === "entity"
          ? `${node.degree} quan hệ trong graph`
          : `Gắn với ${node.degree} sản phẩm trong graph${shown < node.degree ? `, đồ thị chỉ vẽ ${shown}` : ""}`}
      </p>

      <ul className="max-h-64 space-y-2 overflow-auto text-xs">
        {related.map(({ source, target, edge }) => {
          const outgoing = source.id === node.id;
          const other = outgoing ? target : source;
          return (
            <li key={edge.id} className="space-y-0.5 border-t pt-2 first:border-t-0 first:pt-0">
              <p>
                <span className="text-muted-foreground">
                  {outgoing ? REL_TEXT[edge.rel] ?? edge.rel : `${REL_SHORT[edge.rel] ?? edge.rel} từ`}
                </span>{" "}
                <button
                  type="button"
                  onClick={() => onSelect(other.id)}
                  className="rounded font-medium underline decoration-dotted underline-offset-2 hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                >
                  {other.label}
                </button>
              </p>
              <p className="font-mono text-[11px] text-muted-foreground">
                {edge.rel}
                {edge.origin && ` · ${edge.origin}`}
                {edge.confidence !== null && ` · ${edge.confidence.toFixed(2)}`}
                {edge.reviewed !== null && ` · ${edge.reviewed ? "đã duyệt" : "chưa duyệt"}`}
              </p>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
