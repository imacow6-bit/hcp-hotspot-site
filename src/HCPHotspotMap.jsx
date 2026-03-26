import { useState, useEffect, useRef } from "react";
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

// Three-signal color system
const SIGNAL_COLORS = {
  whitespace: "#FFD700",  // Gold — Tier 1, no competitor engagement
  loyalty:    "#4FC3F7",  // Blue — competitor-engaged
  volume:     "#78909C",  // Slate — Tier 2, not engaged
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

// Signal color expression for prescriber dots
const SIGNAL_COLOR_EXPR = [
  "case",
  // White Space: Tier 1 + not competitor-engaged
  ["all",
    ["==", ["get", "tier"], 1],
    ["!", ["get", "competitor_engaged"]],
  ],
  SIGNAL_COLORS.whitespace,
  // Loyalty Signal: competitor-engaged (any tier)
  ["get", "competitor_engaged"],
  SIGNAL_COLORS.loyalty,
  // Volume Signal: everything else (Tier 2, not engaged)
  SIGNAL_COLORS.volume,
];

// OpenFreeMap free vector tiles — no token required
const TILE_STYLE = "https://tiles.openfreemap.org/styles/dark";

// No separate view modes — both layers shown simultaneously

export default function HCPHotspotMap() {
  const mapContainer = useRef(null);
  const map = useRef(null);
  const popup = useRef(null);

  const [activeSpecialty, setActiveSpecialty] = useState("All Specialties");
  const [mapLoaded, setMapLoaded] = useState(false);
  const [hoveredZip, setHoveredZip] = useState(null);
  const [hoveredPrescriber, setHoveredPrescriber] = useState(null);
  const [prescriberData, setPrescriberData] = useState(null);
  const [stats, setStats] = useState(null);

  // ── Load prescriber data ────────────────────────────────────────────────────
  useEffect(() => {
    fetch("/prescriber_scores.json")
      .then((r) => r.json())
      .then((data) => {
        setPrescriberData(data);
        // Compute stats
        const total = data.length;
        const t1 = data.filter((d) => d.tier === 1).length;
        const ws = data.filter((d) => d.tier === 1 && !d.competitor_engaged).length;
        const ce = data.filter((d) => d.competitor_engaged).length;
        setStats({ total, t1, ws, ce });
      })
      .catch(() => setPrescriberData([]));
  }, []);

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
      // ── ZIP density source + layers ───────────────────────────────────────
      map.current.addSource("hcp-zips", {
        type: "geojson",
        data: "/hcp_zips.geojson",
        cluster: false,
      });

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

      // ── Prescriber source + layer ─────────────────────────────────────────
      map.current.addSource("prescribers", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      map.current.addLayer({
        id: "prescriber-dots",
        type: "circle",
        source: "prescribers",
        layout: { visibility: "visible" },
        // Only show prescribers at zoom 6+ so density layer is readable at country view
        minzoom: 6,
        paint: {
          "circle-color": SIGNAL_COLOR_EXPR,
          "circle-radius": [
            "interpolate", ["linear"], ["zoom"],
            6,  ["interpolate", ["linear"], ["get", "tot_clms"], 100, 0.5, 50000, 2],
            8,  ["interpolate", ["linear"], ["get", "tot_clms"], 100, 1.5, 50000, 5],
            10, ["interpolate", ["linear"], ["get", "tot_clms"], 100, 3,   50000, 10],
            14, ["interpolate", ["linear"], ["get", "tot_clms"], 100, 5,   50000, 16],
          ],
          "circle-opacity": [
            "interpolate", ["linear"], ["zoom"],
            6, 0.15,
            8, ["case",
              // White space gets full opacity
              ["all",
                ["==", ["get", "tier"], 1],
                ["!", ["get", "competitor_engaged"]],
              ],
              0.92,
              // Loyalty signal
              ["get", "competitor_engaged"],
              0.7,
              // Volume signal (background)
              0.45,
            ],
          ],
          "circle-stroke-width": [
            "interpolate", ["linear"], ["zoom"],
            6, 0,
            9, ["case",
              ["all",
                ["==", ["get", "tier"], 1],
                ["!", ["get", "competitor_engaged"]],
              ],
              1.5,
              0.5,
            ],
          ],
          "circle-stroke-color": [
            "case",
            ["all",
              ["==", ["get", "tier"], 1],
              ["!", ["get", "competitor_engaged"]],
            ],
            "rgba(255, 215, 0, 0.5)",
            "rgba(255, 255, 255, 0.1)",
          ],
        },
      });

      setMapLoaded(true);
    });

    // ── Hover interactions ─────────────────────────────────────────────────────
    // ZIP density hover
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

    // Prescriber hover
    map.current.on("mousemove", "prescriber-dots", (e) => {
      map.current.getCanvas().style.cursor = "pointer";
      const f = e.features[0];
      if (!f) return;
      const p = f.properties;
      setHoveredPrescriber({
        ...p,
        companies: p.companies ? JSON.parse(p.companies) : [],
        lngLat: e.lngLat,
      });
    });

    map.current.on("mouseleave", "prescriber-dots", () => {
      map.current.getCanvas().style.cursor = "";
      setHoveredPrescriber(null);
    });

    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // ── Load prescriber GeoJSON when data is ready ──────────────────────────────
  useEffect(() => {
    if (!mapLoaded || !map.current || !prescriberData) return;

    const features = prescriberData
      .filter((p) => p.lat != null && p.lng != null)
      .map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lng, p.lat] },
        properties: {
          npi: p.npi,
          name: p.name,
          state: p.state,
          specialty: p.specialty,
          tier: p.tier,
          tot_clms: p.tot_clms,
          competitor_engaged: p.competitor_engaged,
          companies: JSON.stringify(p.companies || []),
        },
      }));

    map.current.getSource("prescribers")?.setData({
      type: "FeatureCollection",
      features,
    });
  }, [mapLoaded, prescriberData]);

  // ── Specialty filter ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!mapLoaded || !map.current) return;

    // Density mode filtering
    if (activeSpecialty === "All Specialties") {
      map.current.setPaintProperty("hcp-dots", "circle-color", DOMINANT_COLOR_EXPR);
      map.current.setPaintProperty("hcp-dots", "circle-radius", [
        "interpolate", ["linear"], ["zoom"],
        4,  ["interpolate", ["linear"], ["get", "total"], 0, 2, 50, 6],
        10, ["interpolate", ["linear"], ["get", "total"], 0, 4, 50, 14],
        14, ["interpolate", ["linear"], ["get", "total"], 0, 6, 50, 20],
      ]);
      map.current.setPaintProperty("hcp-dots", "circle-opacity", 0.82);
      // Prescriber mode: show all
      map.current.setFilter("prescriber-dots", null);
    } else {
      const color = SPECIALTY_COLORS[activeSpecialty];
      map.current.setPaintProperty("hcp-dots", "circle-color", color);
      map.current.setPaintProperty("hcp-dots", "circle-radius", [
        "interpolate", ["linear"], ["zoom"],
        4,  ["interpolate", ["linear"], ["get", activeSpecialty], 0, 1.5, 30, 6],
        10, ["interpolate", ["linear"], ["get", activeSpecialty], 0, 3,   30, 14],
        14, ["interpolate", ["linear"], ["get", activeSpecialty], 0, 5,   30, 20],
      ]);
      map.current.setPaintProperty("hcp-dots", "circle-opacity", [
        "case",
        [">", ["get", activeSpecialty], 0], 0.85,
        0.08,
      ]);
      // Prescriber mode: filter to selected specialty
      map.current.setFilter("prescriber-dots", [
        "==", ["get", "specialty"], activeSpecialty,
      ]);
    }
  }, [activeSpecialty, mapLoaded]);

  // Signal label helper
  const getSignalLabel = (p) => {
    if (p.tier === 1 && !p.competitor_engaged) return "White Space";
    if (p.competitor_engaged) return "Loyalty Signal";
    return "Volume Signal";
  };

  const getSignalColor = (p) => {
    if (p.tier === 1 && !p.competitor_engaged) return SIGNAL_COLORS.whitespace;
    if (p.competitor_engaged) return SIGNAL_COLORS.loyalty;
    return SIGNAL_COLORS.volume;
  };

  return (
    <>
      {/* Top Bar */}
      <div className="topbar">
        <div>
          <h1>HCP Clinical Hotspot Map</h1>
          <div className="topbar-subtitle">
            {stats
              ? `${stats.total.toLocaleString()} scored prescribers · ${stats.ws.toLocaleString()} White Space targets · ${stats.ce.toLocaleString()} competitor-engaged`
              : "Loading prescriber data..."}
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

      {/* Legend — specialty colors + signal colors */}
      {activeSpecialty === "All Specialties" && (
        <div className="specialty-legend">
          {Object.entries(SPECIALTY_COLORS).map(([spec, color]) => (
            <span key={spec} className="legend-item">
              <span className="legend-dot" style={{ background: color }} />
              {spec}
            </span>
          ))}
          <span className="legend-separator" />
          <span className="legend-item">
            <span className="legend-dot legend-dot-glow" style={{ background: SIGNAL_COLORS.whitespace }} />
            White Space
          </span>
          <span className="legend-item">
            <span className="legend-dot" style={{ background: SIGNAL_COLORS.loyalty }} />
            Loyalty
          </span>
          <span className="legend-item">
            <span className="legend-dot" style={{ background: SIGNAL_COLORS.volume }} />
            Volume
          </span>
        </div>
      )}

      {/* Map */}
      <div className="map-wrapper">
        <div ref={mapContainer} className="maplibre-container" />

        {/* ZIP Tooltip */}
        {hoveredZip && !hoveredPrescriber && (
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

        {/* Prescriber Tooltip */}
        {hoveredPrescriber && (
          <div className="zip-tooltip prescriber-tooltip">
            <div className="tooltip-signal-badge" style={{ color: getSignalColor(hoveredPrescriber) }}>
              ● {getSignalLabel(hoveredPrescriber)}
            </div>
            <div className="tooltip-city">{hoveredPrescriber.name}</div>
            <div className="tooltip-meta">
              {hoveredPrescriber.specialty} · Tier {hoveredPrescriber.tier} · {hoveredPrescriber.state}
            </div>
            <div className="tooltip-row">
              <span>Total Claims</span>
              <span>{Number(hoveredPrescriber.tot_clms).toLocaleString()}</span>
            </div>
            {hoveredPrescriber.companies?.length > 0 && (
              <>
                <div className="tooltip-divider" />
                <div className="tooltip-section-label">Engaged by:</div>
                {hoveredPrescriber.companies.map((c) => (
                  <div key={c} className="tooltip-company">{c}</div>
                ))}
              </>
            )}
          </div>
        )}

        <div className="map-hint">
          Scroll to zoom · Drag to pan · Hover for detail · Gold = White Space target
        </div>
      </div>
    </>
  );
}
