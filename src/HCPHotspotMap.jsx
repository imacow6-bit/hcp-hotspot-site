import { useState, useEffect, useRef, useCallback } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

const SPECIALTIES = [
  "All Specialties",
  "Oncology",
  "Cardiology",
  "Orthopedics",
  "Neurology",
  "Endocrinology",
  "Pulmonology",
];

const SPECIALTY_COLORS = {
  Oncology:      "#ff5252",
  Cardiology:    "#ff80ab",
  Orthopedics:   "#69f0ae",
  Neurology:     "#b388ff",
  Endocrinology: "#ffd740",
  Pulmonology:   "#40c4ff",
};

// Color for "All Specialties" mode — dominant specialty wins
const DOMINANT_COLOR_EXPR = [
  "match",
  ["get", "dominant"],
  "Oncology",      "#ff5252",
  "Cardiology",    "#ff80ab",
  "Orthopedics",   "#69f0ae",
  "Neurology",     "#b388ff",
  "Endocrinology", "#ffd740",
  "Pulmonology",   "#40c4ff",
  "#00e5ff",
];

// OpenFreeMap free vector tiles — no token required
const TILE_STYLE = "https://tiles.openfreemap.org/styles/dark";

export default function HCPHotspotMap() {
  const mapContainer = useRef(null);
  const map = useRef(null);
  const popup = useRef(null);

  const [activeSpecialty, setActiveSpecialty] = useState("All Specialties");
  const [mapLoaded, setMapLoaded] = useState(false);
  const [hoveredZip, setHoveredZip] = useState(null);

  // ── Initialize map ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (map.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: TILE_STYLE,
      center: [-96, 38],
      zoom: 4,
      minZoom: 3,
      maxZoom: 18,
    });

    map.current.addControl(new maplibregl.NavigationControl(), "top-left");

    map.current.on("load", () => {
      // Add GeoJSON source
      map.current.addSource("hcp-zips", {
        type: "geojson",
        data: "/hcp_zips.geojson",
        cluster: false,
      });

      // ── Circle layer ──────────────────────────────────────────────────────
      map.current.addLayer({
        id: "hcp-dots",
        type: "circle",
        source: "hcp-zips",
        paint: {
          "circle-color": DOMINANT_COLOR_EXPR,
          "circle-radius": [
            "interpolate", ["linear"], ["zoom"],
            4,  ["interpolate", ["linear"], ["get", "total"], 0, 2, 50, 6],
            10, ["interpolate", ["linear"], ["get", "total"], 0, 4, 50, 14],
            14, ["interpolate", ["linear"], ["get", "total"], 0, 6, 50, 20],
          ],
          "circle-opacity": 0.82,
          "circle-stroke-width": 0.5,
          "circle-stroke-color": "rgba(255,255,255,0.15)",
        },
      });

      // ── Highlight layer (hovered zip) ─────────────────────────────────────
      map.current.addLayer({
        id: "hcp-dots-hover",
        type: "circle",
        source: "hcp-zips",
        filter: ["==", ["get", "zip"], ""],
        paint: {
          "circle-color": "#ffffff",
          "circle-radius": [
            "interpolate", ["linear"], ["zoom"],
            4, 8, 10, 16, 14, 22,
          ],
          "circle-opacity": 0.25,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });

      setMapLoaded(true);
    });

    // ── Hover interaction ─────────────────────────────────────────────────────
    map.current.on("mousemove", "hcp-dots", (e) => {
      map.current.getCanvas().style.cursor = "pointer";
      const f = e.features[0];
      if (!f) return;
      map.current.setFilter("hcp-dots-hover", ["==", ["get", "zip"], f.properties.zip]);
      setHoveredZip({ ...f.properties, lngLat: e.lngLat });
    });

    map.current.on("mouseleave", "hcp-dots", () => {
      map.current.getCanvas().style.cursor = "";
      map.current.setFilter("hcp-dots-hover", ["==", ["get", "zip"], ""]);
      setHoveredZip(null);
    });

    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // ── Specialty filter → repaint dots ────────────────────────────────────────
  useEffect(() => {
    if (!mapLoaded || !map.current) return;

    if (activeSpecialty === "All Specialties") {
      // Color by dominant specialty, size by total
      map.current.setPaintProperty("hcp-dots", "circle-color", DOMINANT_COLOR_EXPR);
      map.current.setPaintProperty("hcp-dots", "circle-radius", [
        "interpolate", ["linear"], ["zoom"],
        4,  ["interpolate", ["linear"], ["get", "total"], 0, 2, 50, 6],
        10, ["interpolate", ["linear"], ["get", "total"], 0, 4, 50, 14],
        14, ["interpolate", ["linear"], ["get", "total"], 0, 6, 50, 20],
      ]);
      map.current.setPaintProperty("hcp-dots", "circle-opacity", 0.82);
    } else {
      // Solid color for selected specialty, size by that specialty's count
      const color = SPECIALTY_COLORS[activeSpecialty];
      map.current.setPaintProperty("hcp-dots", "circle-color", color);
      map.current.setPaintProperty("hcp-dots", "circle-radius", [
        "interpolate", ["linear"], ["zoom"],
        4,  ["interpolate", ["linear"], ["get", activeSpecialty], 0, 1.5, 30, 6],
        10, ["interpolate", ["linear"], ["get", activeSpecialty], 0, 3,   30, 14],
        14, ["interpolate", ["linear"], ["get", activeSpecialty], 0, 5,   30, 20],
      ]);
      // Dim ZIPs with zero of this specialty
      map.current.setPaintProperty("hcp-dots", "circle-opacity", [
        "case",
        [">", ["get", activeSpecialty], 0], 0.85,
        0.08,
      ]);
    }
  }, [activeSpecialty, mapLoaded]);

  return (
    <>
      {/* Top Bar */}
      <div className="topbar">
        <div>
          <h1>HCP Clinical Hotspot Map</h1>
          <div className="topbar-subtitle">
            Real CMS NPPES data · 9,535 ZIP codes · 127,667 matched physicians
          </div>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="filter-bar">
        <span className="filter-bar-label">Specialty</span>
        {SPECIALTIES.map((s) => (
          <button
            key={s}
            className={`filter-btn ${activeSpecialty === s ? "active" : ""}`}
            style={
              activeSpecialty === s && s !== "All Specialties"
                ? { borderColor: SPECIALTY_COLORS[s], color: SPECIALTY_COLORS[s] }
                : {}
            }
            onClick={() => setActiveSpecialty(s)}
          >
            {s !== "All Specialties" && (
              <span
                className="filter-btn-dot"
                style={{ background: SPECIALTY_COLORS[s] }}
              />
            )}
            {s}
          </button>
        ))}
      </div>

      {/* Legend */}
      {activeSpecialty === "All Specialties" && (
        <div className="specialty-legend">
          {Object.entries(SPECIALTY_COLORS).map(([spec, color]) => (
            <span key={spec} className="legend-item">
              <span className="legend-dot" style={{ background: color }} />
              {spec}
            </span>
          ))}
        </div>
      )}

      {/* Map */}
      <div className="map-wrapper">
        <div ref={mapContainer} className="maplibre-container" />

        {/* Tooltip */}
        {hoveredZip && (
          <div className="zip-tooltip">
            <div className="tooltip-city">ZIP {hoveredZip.zip} — {hoveredZip.state}</div>
            <div className="tooltip-meta">{hoveredZip.total} total physicians</div>
            {Object.entries(SPECIALTY_COLORS).map(([spec]) => (
              hoveredZip[spec] > 0 && (
                <div
                  key={spec}
                  className="tooltip-row"
                  style={{
                    fontWeight: activeSpecialty === spec ? 700 : 400,
                    color: activeSpecialty === spec ? SPECIALTY_COLORS[spec] : undefined,
                  }}
                >
                  <span
                    className="tooltip-spec-dot"
                    style={{ background: SPECIALTY_COLORS[spec] }}
                  />
                  <span>{spec}</span>
                  <span>{hoveredZip[spec]}</span>
                </div>
              )
            ))}
          </div>
        )}

        <div className="map-hint">Scroll to zoom · Drag to pan · Hover ZIP for detail</div>
      </div>
    </>
  );
}
