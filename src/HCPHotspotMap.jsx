import { useState, useMemo, useRef, useCallback } from "react";
import * as d3 from "d3";

import { METROS } from "./hcp_hotspot_data.js";

const SPECIALTIES = [
  "All Specialties",
  "Oncology",
  "Cardiology",
  "Orthopedics",
  "Neurology",
  "Endocrinology",
  "Pulmonology",
];

const projection = d3.geoAlbersUsa().scale(1100).translate([480, 300]);

export default function HCPHotspotMap() {
  const [activeSpecialty, setActiveSpecialty] = useState("All Specialties");
  const [hoveredCity, setHoveredCity] = useState(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const mapRef = useRef(null);

  const getDocCount = useCallback((metro, specialty) => {
    if (specialty === "All Specialties")
      return Object.values(metro.docs).reduce((a, b) => a + b, 0);
    return metro.docs[specialty] || 0;
  }, []);

  const getDensity = useCallback((metro, specialty) => {
    return (getDocCount(metro, specialty) / metro.pop) * 100000;
  }, [getDocCount]);

  const projected = useMemo(() => {
    return METROS.map((m) => {
      const coords = projection([m.lng, m.lat]);
      if (!coords) return null;
      return { ...m, x: coords[0], y: coords[1] };
    }).filter(Boolean);
  }, []);

  const maxCount = useMemo(
    () => Math.max(...projected.map((m) => getDocCount(m, activeSpecialty))),
    [projected, activeSpecialty, getDocCount]
  );

  const sorted = useMemo(() => {
    return [...projected].sort(
      (a, b) => getDensity(b, activeSpecialty) - getDensity(a, activeSpecialty)
    );
  }, [projected, activeSpecialty, getDensity]);

  return (
    <div style={{ background: "#0a0e17", minHeight: "100vh", color: "white" }}>
      <h2 style={{ padding: 20 }}>HCP Clinical Hotspot Map</h2>

      <div style={{ padding: 20 }}>
        {SPECIALTIES.map((s) => (
          <button
            key={s}
            onClick={() => setActiveSpecialty(s)}
            style={{ marginRight: 8 }}
          >
            {s}
          </button>
        ))}
      </div>

      <svg
        ref={mapRef}
        viewBox="0 0 960 600"
        style={{ width: "100%", height: "80vh" }}
      >
        {projected.map((m) => {
          const count = getDocCount(m, activeSpecialty);
          const r = 3 + (count / maxCount) * 20;

          return (
            <circle
              key={m.city}
              cx={m.x}
              cy={m.y}
              r={r}
              fill="#00e5ff"
              opacity={0.7}
              onMouseEnter={() => setHoveredCity(m.city)}
              onMouseLeave={() => setHoveredCity(null)}
            />
          );
        })}
      </svg>

      {hoveredCity && (
        <div style={{ padding: 20 }}>
          Hovering: {hoveredCity}
        </div>
      )}
    </div>
  );
}