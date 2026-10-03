import type { AgentV3TimelineStop } from "./agent-v3-types";
import { hasMapLocation } from "./agent-v3-view.ts";

/* GCJ-02 conversion adapted from https://github.com/wandergis/coordtransform
 * The MIT License (MIT)
 * Copyright (c) 2015 记忆的残骸
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
export function gcj02ToWgs84(longitude: number, latitude: number) {
  if (!(longitude > 73.66 && longitude < 135.05 && latitude > 3.86 && latitude < 53.55)) return { longitude, latitude };
  const x = longitude - 105, y = latitude - 35, pi = Math.PI;
  const common = (20 * Math.sin(6 * x * pi) + 20 * Math.sin(2 * x * pi)) * 2 / 3;
  let dLat = -100 + 2 * x + 3 * y + .2 * y * y + .1 * x * y + .2 * Math.sqrt(Math.abs(x)) + common;
  dLat += (20 * Math.sin(y * pi) + 40 * Math.sin(y / 3 * pi)) * 2 / 3;
  dLat += (160 * Math.sin(y / 12 * pi) + 320 * Math.sin(y * pi / 30)) * 2 / 3;
  let dLng = 300 + x + 2 * y + .1 * x * x + .1 * x * y + .1 * Math.sqrt(Math.abs(x)) + common;
  dLng += (20 * Math.sin(x * pi) + 40 * Math.sin(x / 3 * pi)) * 2 / 3;
  dLng += (150 * Math.sin(x / 12 * pi) + 300 * Math.sin(x / 30 * pi)) * 2 / 3;
  const radians = latitude * pi / 180;
  const magic = 1 - .00669342162296594323 * Math.sin(radians) ** 2;
  const root = Math.sqrt(magic);
  dLat = dLat * 180 / ((6378245 * (1 - .00669342162296594323)) / (magic * root) * pi);
  dLng = dLng * 180 / (6378245 / root * Math.cos(radians) * pi);
  return { longitude: longitude - dLng, latitude: latitude - dLat };
}

export type MapView = { x: number; y: number; zoom: number };
export const TILE_SIZE = 256;
export const MAX_MERCATOR_LATITUDE = 85.0511287798066;
export function mercatorPoint(longitude: number, latitude: number) {
  const lat = Math.max(-MAX_MERCATOR_LATITUDE, Math.min(MAX_MERCATOR_LATITUDE, latitude)) * Math.PI / 180;
  return { x: (longitude + 180) / 360, y: Math.max(0, Math.min(1, (1 - Math.log(Math.tan(lat) + 1 / Math.cos(lat)) / Math.PI) / 2)) };
}
export function mapStops(stops: AgentV3TimelineStop[]) {
  return stops.flatMap((stop, index) => {
    if (!hasMapLocation(stop)) return [];
    const wgs = gcj02ToWgs84(stop.location!.longitude, stop.location!.latitude);
    return [{ stop, number: index + 1, ...mercatorPoint(wgs.longitude, wgs.latitude) }];
  });
}
export function fitMapView(points: Array<{ x: number; y: number }>, width: number, height: number): MapView {
  if (!points.length) return { x: .5, y: .5, zoom: 2 };
  const minX = Math.min(...points.map((point) => point.x)), maxX = Math.max(...points.map((point) => point.x));
  const minY = Math.min(...points.map((point) => point.y)), maxY = Math.max(...points.map((point) => point.y));
  const availableWidth = Math.max(100, width - 120), availableHeight = Math.max(100, height - 160);
  const zoom = Math.floor(Math.log2(Math.min(availableWidth / (Math.max(maxX - minX, .000015) * TILE_SIZE), availableHeight / (Math.max(maxY - minY, .000015) * TILE_SIZE))));
  return { x: (minX + maxX) / 2, y: (minY + maxY) / 2, zoom: Math.max(2, Math.min(16, zoom)) };
}
export function screenPoint(point: { x: number; y: number }, view: MapView, width: number, height: number) {
  const scale = TILE_SIZE * 2 ** view.zoom;
  return { x: width / 2 + (point.x - view.x) * scale, y: height / 2 + (point.y - view.y) * scale };
}
export function viewportTiles(view: MapView, width: number, height: number) {
  const count = 2 ** view.zoom, scale = TILE_SIZE * count;
  const left = view.x * scale - width / 2, top = view.y * scale - height / 2;
  const firstX = Math.max(0, Math.floor(left / TILE_SIZE)), lastX = Math.min(count - 1, Math.ceil((left + width) / TILE_SIZE) - 1);
  const firstY = Math.max(0, Math.floor(top / TILE_SIZE)), lastY = Math.min(count - 1, Math.ceil((top + height) / TILE_SIZE) - 1);
  const tiles: Array<{ x: number; y: number; zoom: number; url: string }> = [];
  for (let y = firstY; y <= lastY; y++) for (let x = firstX; x <= lastX; x++) tiles.push({ x, y, zoom: view.zoom, url: `https://tile.openstreetmap.org/${view.zoom}/${x}/${y}.png` });
  return tiles;
}

// Connections encode itinerary order only. An unlocated stop interrupts the line.
export function consecutiveMapPairs<T extends { number: number }>(points: T[]) {
  return points.slice(1).flatMap((point, index) => point.number === points[index].number + 1 ? [{ from: points[index], to: point }] : []);
}
