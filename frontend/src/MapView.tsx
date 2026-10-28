import { Fragment, useEffect, useRef, useState } from "react";
import {
  Circle, CircleMarker, MapContainer, Polyline, Popup, TileLayer, useMap,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";

export type MapMarker = {
  id: string;
  latitude: number;
  longitude: number;
  title: string;
  detail: string;
  kind: "customer" | "dp" | "address";
  radiusMeters?: number;
  color?: string;
};

export type MapLine = [[number, number], [number, number]];
type MapTypeId = "roadmap" | "satellite";
type GoogleMapsApi = any;
let mapsPromise: Promise<GoogleMapsApi> | null = null;
let mapsKey = "";

function loadGoogleMaps(key: string): Promise<GoogleMapsApi> {
  const browser = window as any;
  if (browser.google?.maps) return Promise.resolve(browser.google.maps);
  if (mapsPromise && mapsKey === key) return mapsPromise;
  mapsKey = key;
  mapsPromise = new Promise((resolve, reject) => {
    const callback = "__geoCheckMapsLoaded";
    browser[callback] = () => resolve(browser.google.maps);
    const script = document.createElement("script");
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&loading=async&callback=${callback}`;
    script.async = true;
    script.onerror = () => {
      mapsPromise = null;
      mapsKey = "";
      reject(new Error("Google Maps did not load. Check your restricted browser key, billing, and enabled Maps JavaScript API."));
    };
    document.head.appendChild(script);
  });
  return mapsPromise;
}

export default function MapView({
  markers,
  center,
  tileUrl,
  zoom = 14,
  path,
  googleMapsKey,
  fitToMarkers = false,
}: {
  markers: MapMarker[];
  center: [number, number];
  tileUrl: string;
  zoom?: number;
  path?: MapLine;
  googleMapsKey?: string;
  fitToMarkers?: boolean;
}) {
  const element = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const overlays = useRef<any[]>([]);
  const [mapType, setMapType] = useState<MapTypeId>("roadmap");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!googleMapsKey?.trim() || !element.current) return;
    let cancelled = false;
    loadGoogleMaps(googleMapsKey.trim()).then((maps) => {
      if (cancelled || !element.current) return;
      if (!mapRef.current) {
        mapRef.current = new maps.Map(element.current, {
          center: { lat: center[0], lng: center[1] },
          zoom,
          mapTypeId: mapType === "satellite" ? "satellite" : "roadmap",
          mapTypeControl: false,
          streetViewControl: false,
          fullscreenControl: true,
          gestureHandling: "greedy",
        });
      }
      setReady(true);
      setError("");
    }).catch((cause: unknown) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Google Maps could not load.");
    });
    return () => { cancelled = true; };
  }, [googleMapsKey]);

  useEffect(() => {
    if (!mapRef.current) return;
    mapRef.current.setCenter({ lat: center[0], lng: center[1] });
    mapRef.current.setZoom(zoom);
    mapRef.current.setMapTypeId(mapType === "satellite" ? "satellite" : "roadmap");
  }, [center, mapType, ready, zoom]);

  useEffect(() => {
    if (!ready || !mapRef.current || !googleMapsKey?.trim()) return;
    const maps = (window as any).google.maps;
    overlays.current.forEach((overlay) => overlay.setMap(null));
    overlays.current = [];
    for (const marker of markers) {
      const color = marker.color || (marker.kind === "dp" ? "#e28144" : marker.kind === "address" ? "#26a881" : "#355fce");
      const pin = new maps.Marker({
        map: mapRef.current,
        position: { lat: marker.latitude, lng: marker.longitude },
        title: marker.title,
        icon: { path: maps.SymbolPath.CIRCLE, scale: marker.kind === "dp" ? 7 : 6, fillColor: color, fillOpacity: 1, strokeColor: "#fff", strokeWeight: 2 },
      });
      const info = new maps.InfoWindow({
        content: `<div class="map-popup"><strong>${escapeHtml(marker.title)}</strong><span>${escapeHtml(marker.detail)}</span><small>${marker.latitude.toFixed(6)}, ${marker.longitude.toFixed(6)}</small></div>`,
      });
      pin.addListener("click", () => info.open({ map: mapRef.current, anchor: pin }));
      overlays.current.push(pin);
      if (marker.radiusMeters) overlays.current.push(new maps.Circle({
        map: mapRef.current,
        center: { lat: marker.latitude, lng: marker.longitude },
        radius: marker.radiusMeters,
        strokeColor: "#355fce",
        strokeOpacity: 0.78,
        strokeWeight: 1.4,
        fillColor: "#355fce",
        fillOpacity: 0.08,
      }));
    }
    if (path) overlays.current.push(new maps.Polyline({
      map: mapRef.current,
      path: path.map(([lat, lng]) => ({ lat, lng })),
      strokeColor: "#355fce",
      strokeOpacity: 0.76,
      strokeWeight: 3,
      geodesic: true,
    }));
    if (fitToMarkers && markers.length > 1) {
      const bounds = new maps.LatLngBounds();
      markers.forEach((marker) => bounds.extend({ lat: marker.latitude, lng: marker.longitude }));
      mapRef.current.fitBounds(bounds, 40);
    }
    return () => {
      overlays.current.forEach((overlay) => overlay.setMap(null));
      overlays.current = [];
    };
  }, [fitToMarkers, googleMapsKey, markers, path, ready]);

  const controls = <div className="map-toolbar">
    <div className="map-style-switch" role="group" aria-label="Map style">
      <button className={mapType === "roadmap" ? "active" : ""} onClick={() => setMapType("roadmap")}>Road</button>
      <button className={mapType === "satellite" ? "active" : ""} onClick={() => setMapType("satellite")} disabled={!googleMapsKey}>Satellite</button>
    </div>
    <span className="map-provider-label">{googleMapsKey && !error ? "Google Maps" : "OpenStreetMap"}</span>
  </div>;

  if (googleMapsKey?.trim() && !error) {
    return <section className="map-component">{controls}<div ref={element} className="google-map-canvas" aria-label="Map showing customer and connected FDP locations" /></section>;
  }

  return <section className="map-component">
    {controls}
    {error && <div className="map-message" role="status">{error} Showing OpenStreetMap instead.</div>}
    <div className="leaflet-map-wrap">
      <MapContainer center={center} zoom={zoom} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
        <FitMapBounds markers={markers} enabled={fitToMarkers} />
        <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>' url={tileUrl} />
        {path && <Polyline positions={path} pathOptions={{ color: "#355fce", opacity: 0.76, weight: 3 }} />}
        {markers.map((marker) => {
          const color = marker.color || (marker.kind === "dp" ? "#e28144" : marker.kind === "address" ? "#26a881" : "#355fce");
          return <Fragment key={marker.id}>
            {marker.radiusMeters && <Circle center={[marker.latitude, marker.longitude]} radius={marker.radiusMeters} pathOptions={{ color, fillColor: color, fillOpacity: 0.08, weight: 1.4 }} />}
            <CircleMarker center={[marker.latitude, marker.longitude]} radius={marker.kind === "dp" ? 8 : 6} pathOptions={{ color, fillColor: color, fillOpacity: 0.94, weight: 2 }}>
              <Popup><div className="map-popup"><strong>{marker.title}</strong><span>{marker.detail}</span><small>{marker.latitude.toFixed(6)}, {marker.longitude.toFixed(6)}</small></div></Popup>
            </CircleMarker>
          </Fragment>;
        })}
      </MapContainer>
    </div>
  </section>;
}

function FitMapBounds({ markers, enabled }: { markers: MapMarker[]; enabled: boolean }) {
  const map = useMap();
  useEffect(() => {
    if (!enabled || markers.length < 2) return;
    map.fitBounds(markers.map((marker) => [marker.latitude, marker.longitude] as [number, number]), {
      padding: [32, 32],
      maxZoom: 17,
    });
  }, [enabled, map, markers]);
  return null;
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character] || character);
}
