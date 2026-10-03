"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { LocateFixed, Minus, Plus, MapPin, ArrowUpRight } from "lucide-react";
import type { AgentV3TimelineStop } from "@/lib/agent-v3-types";
import { mapNavigationUrl, projectMapStops } from "@/lib/agent-v3-view";
import { consecutiveMapPairs, fitMapView, mapStops, screenPoint, TILE_SIZE, viewportTiles, type MapView } from "@/lib/agent-v3-map";

// Marker offsets affect display only; itinerary lines retain each original map point.
function separateMarkers<T extends { x: number; y: number }>(points: T[], width: number, height: number) {
  const placed: Array<T & { anchorX: number; anchorY: number }> = [];
  const gap = 52;
  for (const point of points) {
    let x = point.x, y = point.y;
    const visible = x >= -22 && x <= width + 22 && y >= -22 && y <= height + 22;
    if (visible) {
      let found = false;
      for (let ring = 0; ring <= 8 && !found; ring++) {
        const samples = ring === 0 ? 1 : ring * 8;
        for (let angle = 0; angle < samples; angle++) {
          const radians = angle * Math.PI * 2 / samples - Math.PI / 2;
          const candidateX = point.x + Math.cos(radians) * ring * gap;
          const candidateY = point.y + Math.sin(radians) * ring * gap;
          if (candidateX < 22 || candidateX > width - 22 || candidateY < 22 || candidateY > height - 22) continue;
          if (placed.some((other) => Math.hypot(candidateX - other.x, candidateY - other.y) < gap)) continue;
          x = candidateX; y = candidateY; found = true; break;
        }
      }
    }
    placed.push({ ...point, anchorX: point.x, anchorY: point.y, x, y });
  }
  return placed;
}

export function TripMap({ stops, selectedStopId, onSelect, dayNumber, fullHeight = false }: {
  stops: AgentV3TimelineStop[]; selectedStopId: string | null;
  onSelect: (id: string) => void; dayNumber: number; fullHeight?: boolean;
}) {
  const canvas = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 600, height: 470 });
  const [visible, setVisible] = useState(false);
  const [realMap, setRealMap] = useState(true);
  const [tileFailed, setTileFailed] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [mapView, setMapView] = useState<MapView | null>(null);
  const [tileView, setTileView] = useState<MapView | null>(null);
  const [diagram, setDiagram] = useState({ zoom: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; view: MapView; diagram: typeof diagram } | null>(null);
  const loadedTile = useRef(false);
  const worldPoints = useMemo(() => mapStops(stops), [stops]);
  const schematic = useMemo(() => projectMapStops(stops), [stops]);
  const initial = useMemo(() => fitMapView(worldPoints, size.width, size.height), [worldPoints, size]);
  const view = mapView ?? initial;
  const showTiles = realMap && !tileFailed && worldPoints.length > 0;
  const selected = stops.find((stop) => stop.stop_id === selectedStopId);
  const navigation = selected ? mapNavigationUrl(selected) : null;

  useEffect(() => {
    const node = canvas.current;
    if (!node) return;
    const resize = new ResizeObserver(([entry]) => { if (entry.contentRect.width > 0 && entry.contentRect.height > 0) setSize({ width: entry.contentRect.width, height: entry.contentRect.height }); });
    resize.observe(node);
    const intersection = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    intersection.observe(node);
    return () => { resize.disconnect(); intersection.disconnect(); };
  }, []);
  // Only request tiles after a gesture settles and the map is actually visible.
  // Native images retain browser HTTP caching and send the normal browser Referer.
  useEffect(() => {
    if (!showTiles || !visible || dragging) return;
    const timer = setTimeout(() => setTileView(view), 220);
    return () => clearTimeout(timer);
  }, [showTiles, visible, dragging, view]);
  useEffect(() => {
    if (!showTiles || !visible || !tileView) return;
    const timer = setTimeout(() => { if (!loadedTile.current) setTileFailed(true); }, 8000);
    return () => clearTimeout(timer);
  }, [showTiles, visible, tileView]);
  useEffect(() => {
    const point = worldPoints.find((item) => item.stop.stop_id === selectedStopId);
    if (!point || !showTiles) return;
    const position = screenPoint(point, view, size.width, size.height);
    if (position.x < 30 || position.x > size.width - 30 || position.y < 70 || position.y > size.height - 60) setMapView({ ...view, x: point.x, y: point.y });
    // A selection moves the map only when the selected marker is out of view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStopId]);
  const points = showTiles
    ? worldPoints.map((point) => ({ ...point, ...screenPoint(point, view, size.width, size.height) }))
    : schematic.map((point) => ({ ...point, x: ((point.x - 300) * diagram.zoom + 300) * size.width / 600 + diagram.x, y: ((point.y - 235) * diagram.zoom + 235) * size.height / 470 + diagram.y }));
  const markers = separateMarkers(points, size.width, size.height).sort((a, b) => Number(a.stop.stop_id === selectedStopId) - Number(b.stop.stop_id === selectedStopId));
  const tiles = showTiles && visible && tileView ? viewportTiles(tileView, size.width, size.height) : [];
  const reset = () => { setMapView(null); setDiagram({ zoom: 1, x: 0, y: 0 }); };
  const zoom = (amount: number) => {
    if (showTiles) setMapView({ ...view, zoom: Math.max(2, Math.min(18, view.zoom + amount)) });
    else setDiagram((current) => ({ ...current, zoom: Math.max(.6, Math.min(4, current.zoom + amount * .4)) }));
  };
  return <div className={`trip-map-root relative min-w-0 overflow-hidden bg-[#edf0e7] text-ink ${fullHeight ? "h-full" : "h-[480px] rounded-xl border border-line"}`} data-testid="trip-map">
    <div ref={canvas} className="trip-map-canvas relative h-full w-full overflow-hidden">
      <div className="absolute left-4 top-4 z-10 max-w-[calc(100%_-_80px)] rounded-lg bg-white/95 px-3 py-2 shadow-sm"><button type="button" onClick={() => { if (tileFailed || !worldPoints.length) return; setRealMap((current) => !current); }} disabled={tileFailed || !worldPoints.length} className="text-xs font-medium disabled:text-muted">{tileFailed ? "底图暂不可用" : showTiles ? "示意图" : "底图"}</button><p className="flex items-center gap-1.5 text-[10px] text-muted"><span className="inline-block w-5 border-t-2 border-dashed border-[#ef6a4b]" />地点顺序</p>{points.length < stops.length && <p className="mt-1 text-[10px] text-muted">{stops.length - points.length} 个地点暂无坐标</p>}</div>
      <div className="absolute right-4 top-4 z-10 flex flex-col gap-1 rounded-lg bg-white p-1 shadow-sm">
        <button type="button" aria-label="放大地图" className="rounded p-2 hover:bg-[#edf0e7]" onClick={() => zoom(1)}><Plus size={16} /></button>
        <button type="button" aria-label="缩小地图" className="rounded p-2 hover:bg-[#edf0e7]" onClick={() => zoom(-1)}><Minus size={16} /></button>
        <button type="button" aria-label="显示全部地点" className="rounded p-2 hover:bg-[#edf0e7]" onClick={reset}><LocateFixed size={16} /></button>
      </div>
      {tiles.map((tile) => {
        const tileOrigin = screenPoint({ x: tile.x / 2 ** tile.zoom, y: tile.y / 2 ** tile.zoom }, view, size.width, size.height);
        const extent = TILE_SIZE * 2 ** (view.zoom - tile.zoom);
        return <img key={tile.url} src={tile.url} alt="" draggable={false} referrerPolicy="strict-origin-when-cross-origin" onLoad={() => { loadedTile.current = true; }} onError={() => setTileFailed(true)} className="pointer-events-none absolute max-w-none select-none" style={{ left: tileOrigin.x, top: tileOrigin.y, width: extent, height: extent }} />;
      })}
      <svg viewBox={`0 0 ${size.width} ${size.height}`} className="absolute inset-0 h-full w-full touch-none cursor-grab active:cursor-grabbing" role="group" aria-label={`第 ${dayNumber} 天地点地图，可拖动和缩放`}
        onPointerDown={(event) => {
          if ((event.target as Element).closest("[data-map-marker]")) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { x: event.clientX, y: event.clientY, view, diagram };
          setDragging(true);
        }}
        onPointerMove={(event) => {
          if (!drag.current) return;
          const dx = event.clientX - drag.current.x, dy = event.clientY - drag.current.y;
          if (showTiles) {
            const scale = TILE_SIZE * 2 ** drag.current.view.zoom;
            setMapView({ ...drag.current.view, x: Math.max(0, Math.min(1, drag.current.view.x - dx / scale)), y: Math.max(0, Math.min(1, drag.current.view.y - dy / scale)) });
          } else setDiagram({ ...drag.current.diagram, x: drag.current.diagram.x + dx, y: drag.current.diagram.y + dy });
        }}
        onPointerUp={() => { drag.current = null; setDragging(false); }} onPointerCancel={() => { drag.current = null; setDragging(false); }}>
        {!showTiles && <><defs><pattern id={`map-grid-${dayNumber}`} width="50" height="50" patternUnits="userSpaceOnUse"><path d="M 50 0 L 0 0 0 50" fill="none" stroke="#d9e0d4" strokeWidth="1" /></pattern></defs><rect width="100%" height="100%" fill={`url(#map-grid-${dayNumber})`} /></>}
        {consecutiveMapPairs(points).map(({ from, to }) => <line key={`line-${to.stop.stop_id}`} x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke="#ef6a4b" strokeWidth="2.5" strokeDasharray="5 5" />)}
        {markers.map((point) => Math.hypot(point.x - point.anchorX, point.y - point.anchorY) > 1 && <line key={`leader-${point.stop.stop_id}`} x1={point.anchorX} y1={point.anchorY} x2={point.x} y2={point.y} stroke="#718074" strokeWidth="1" opacity=".7" pointerEvents="none" />)}
        {markers.map((point) => {
          const active = point.stop.stop_id === selectedStopId;
          return <g key={point.stop.stop_id} data-map-marker="true" role="button" tabIndex={0} aria-label={`地点 ${point.number}：${point.stop.name}`} aria-pressed={active}
            onClick={() => onSelect(point.stop.stop_id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(point.stop.stop_id); } }} className="cursor-pointer outline-none focus:[&>circle]:stroke-[#ef6a4b]">
            <circle cx={point.x} cy={point.y} r="22" fill="transparent" pointerEvents="all" />
            {active && <circle cx={point.x} cy={point.y} r="26" fill="#ef6a4b" opacity=".2" />}
            <circle cx={point.x} cy={point.y} r="17" fill={active ? "#ef6a4b" : "#26332e"} stroke="white" strokeWidth="3" />
            <text x={point.x} y={point.y + 4} textAnchor="middle" fill="white" fontSize="12" fontWeight="700">{point.number}</text>

          </g>;
        })}
      </svg>
      {!points.length && <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-6 text-center"><MapPin className="mb-3 opacity-40" size={28} /><p className="text-sm">暂无地点坐标</p></div>}
      {selected && <div className="absolute bottom-12 left-4 right-4 z-10 flex max-w-[400px] items-center justify-between gap-3 rounded-xl bg-white px-4 py-3 shadow-lg"><div className="min-w-0"><p className="break-words text-sm font-semibold leading-5">{selected.name}</p>{selected.address && !selected.address.includes("示例点位") && <p className="mt-1 truncate text-xs text-muted">{selected.address}</p>}{!navigation && <p className="mt-1 text-xs text-muted">暂无坐标</p>}</div>{navigation && <a href={navigation} target="_blank" rel="noreferrer" className="flex shrink-0 items-center gap-1 text-xs font-medium">导航<ArrowUpRight size={14} /></a>}</div>}
      <div className="absolute bottom-3 right-3 z-10 rounded bg-white/95 px-2 py-1 text-[9px] text-muted">{showTiles ? <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a> : "坐标示意图"}</div>
    </div>
  </div>;
}
