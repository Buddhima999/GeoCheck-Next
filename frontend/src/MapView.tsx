import { CircleMarker, MapContainer, Popup, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";

export type MapMarker = {
  id: string;
  latitude: number;
  longitude: number;
  title: string;
  detail: string;
  kind: "customer" | "dp";
};

export default function MapView({
  markers,
  center,
  tileUrl,
}: {
  markers: MapMarker[];
  center: [number, number];
  tileUrl: string;
}) {
  return (
    <div className="leaflet-map-wrap">
      <MapContainer
        center={center}
        zoom={8}
        scrollWheelZoom
        style={{ height: "480px", width: "100%" }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a>'
          url={tileUrl}
        />
        {markers.map((marker) => {
          const color = marker.kind === "dp" ? "#cc7135" : "#315ce8";
          return (
            <CircleMarker
              key={marker.id}
              center={[marker.latitude, marker.longitude]}
              radius={marker.kind === "dp" ? 7 : 5}
              pathOptions={{ color, fillColor: color, fillOpacity: 0.76, weight: 1.5 }}
            >
              <Popup>
                <div className="map-popup">
                  <strong>{marker.title}</strong>
                  <span>{marker.detail}</span>
                  <small>{marker.latitude.toFixed(6)}, {marker.longitude.toFixed(6)}</small>
                </div>
              </Popup>
            </CircleMarker>
          );
        })}
      </MapContainer>
    </div>
  );
}
