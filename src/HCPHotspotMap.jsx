import { useState, useEffect, useRef, useMemo } from "react";
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
  whitespace: "#FFD700",  // Gold — Tier 1, no competitor engagement (shown as stars)
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

// Signal color for circle layer (loyalty + volume only — White Space shown as stars)
const SIGNAL_COLOR_EXPR = [
  "case",
  ["get", "competitor_engaged"],
  SIGNAL_COLORS.loyalty,
  SIGNAL_COLORS.volume,
];

// OpenFreeMap free vector tiles — no token required
const TILE_STYLE = "https://tiles.openfreemap.org/styles/dark";

// ── Water exclusion: simplified Lake Michigan polygon ─────────────────────────
const LAKE_MICHIGAN = [
  [-87.9, 41.6], [-86.9, 41.6], [-86.6, 42.0], [-86.4, 42.8],
  [-86.3, 43.5], [-86.4, 44.0], [-86.6, 44.8], [-87.4, 45.4],
  [-87.8, 46.0], [-86.6, 46.1], [-85.3, 45.8], [-85.2, 45.2],
  [-84.9, 44.8], [-85.1, 44.3], [-85.4, 44.0], [-85.8, 43.5],
  [-86.0, 43.0], [-86.3, 42.4], [-87.2, 41.8], [-87.9, 41.6],
];

function pointInPolygon(lat, lng, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (((yi > lat) !== (yj > lat)) && lng < (xj - xi) * (lat - yi) / (yj - yi) + xi)
      inside = !inside;
  }
  return inside;
}

function isInWater(lat, lng) {
  return pointInPolygon(lat, lng, LAKE_MICHIGAN);
}

// ── Gold star canvas image for Tier 1 White Space markers ────────────────────
function makeStarImage(size = 22, fillColor = "#FFD700") {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  const cx = size / 2, cy = size / 2;
  const r1 = size / 2 - 1.5;
  const r2 = size / 4.5;
  ctx.fillStyle = fillColor;
  ctx.strokeStyle = "rgba(0,0,0,0.55)";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i < 5; i++) {
    const a1 = (i * 4 * Math.PI) / 5 - Math.PI / 2;
    const a2 = a1 + (2 * Math.PI) / 10;
    if (i === 0)
      ctx.moveTo(cx + r1 * Math.cos(a1), cy + r1 * Math.sin(a1));
    else
      ctx.lineTo(cx + r1 * Math.cos(a1), cy + r1 * Math.sin(a1));
    ctx.lineTo(cx + r2 * Math.cos(a2), cy + r2 * Math.sin(a2));
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

export default function HCPHotspotMap() {
  const mapContainer = useRef(null);
  const map = useRef(null);
  const eventMarkerRef = useRef(null);

  const [activeSpecialty, setActiveSpecialty] = useState("All Specialties");
  const [showTier1Only, setShowTier1Only] = useState(false);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [hoveredZip, setHoveredZip] = useState(null);
  const [hoveredPrescriber, setHoveredPrescriber] = useState(null);
  const [prescriberData, setPrescriberData] = useState(null);
  const [stats, setStats] = useState(null);

  // ── Load prescriber data ─────────────────────────────────────────────────────
  useEffect(() => {
    fetch("/prescriber_scores.json")
      .then((r) => r.json())
      .then((data) => {
        setPrescriberData(data);
        const total = data.length;
        const t1 = data.filter((d) => d.tier === 1).length;
        const ws = data.filter((d) => d.tier === 1 && !d.competitor_engaged).length;
        const ce = data.filter((d) => d.competitor_engaged).length;
        setStats({ total, t1, ws, ce });
      })
      .catch(() => setPrescriberData([]));
  }, []);

  // ── Compute optimal event location (weighted centroid of Tier 1 White Space) ──
  const eventLocation = useMemo(() => {
    if (!prescriberData || !showTier1Only) return null;
    const pts = prescriberData.filter(
      (p) =>
        p.lat != null &&
        p.lng != null &&
        p.tier === 1 &&
        !p.competitor_engaged &&
        !isInWater(p.lat, p.lng) &&
        (activeSpecialty === "All Specialties" || p.specialty === activeSpecialty)
    );
    if (pts.length === 0) return null;
    let wLat = 0, wLng = 0, totalW = 0;
    for (const p of pts) {
      const w = p.tot_clms || 1;
      wLat += p.lat * w;
      wLng += p.lng * w;
      totalW += w;
    }
    return { lat: wLat / totalW, lng: wLng / totalW, count: pts.length };
  }, [prescriberData, showTier1Only, activeSpecialty]);

  // ── Initialize map ───────────────────────────────────────────────────────────
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

      // ── Prescriber source ─────────────────────────────────────────────────
      map.current.addSource("prescribers", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });

      // Circle layer: loyalty signal + volume signal only
      // White Space (tier1 + !competitor_engaged) is shown as gold stars instead
      map.current.addLayer({
        id: "prescriber-dots",
        type: "circle",
        source: "prescribers",
        layout: { visibility: "visible" },
        minzoom: 6,
        filter: ["any", ["get", "competitor_engaged"], ["!=", ["get", "tier"], 1]],
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
            8, ["case", ["get", "competitor_engaged"], 0.7, 0.45],
          ],
          "circle-stroke-width": [
            "interpolate", ["linear"], ["zoom"],
            6, 0, 9, 0.5,
          ],
          "circle-stroke-color": "rgba(255,255,255,0.1)",
        },
      });

      // ── Gold star layer: Tier 1 White Space targets ───────────────────────
      map.current.addImage("tier1-star", makeStarImage(22, "#FFD700"), { sdf: false });

      map.current.addLayer({
        id: "tier1-stars",
        type: "symbol",
        source: "prescribers",
        minzoom: 6,
        filter: ["all", ["==", ["get", "tier"], 1], ["!", ["get", "competitor_engaged"]]],
        layout: {
          "icon-image": "tier1-star",
          "icon-size": [
            "interpolate", ["linear"], ["zoom"],
            6, 0.35,
            9, 0.65,
            12, 0.95,
            14, 1.2,
          ],
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
        paint: {
          "icon-opacity": [
            "interpolate", ["linear"], ["zoom"],
            6, 0.15,
            8, 0.9,
          ],
        },
      });

      setMapLoaded(true);
    });

    // ── Hover: ZIP density ──────────────────────────────────────────────────
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

    // ── Hover: prescriber circles (loyalty + volume) ────────────────────────
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

    // ── Hover: Tier 1 stars (White Space) ──────────────────────────────────
    map.current.on("mousemove", "tier1-stars", (e) => {
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

    map.current.on("mouseleave", "tier1-stars", () => {
      map.current.getCanvas().style.cursor = "";
      setHoveredPrescriber(null);
    });

    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  // ── Load prescriber GeoJSON (filter water points) ───────────────────────────
  useEffect(() => {
    if (!mapLoaded || !map.current || !prescriberData) return;

    const features = prescriberData
      .filter((p) => p.lat != null && p.lng != null && !isInWater(p.lat, p.lng))
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

  // ── Specialty + Tier 1 combined filter ──────────────────────────────────────
  useEffect(() => {
    if (!mapLoaded || !map.current) return;

    // Layer base filters
    const wsBase = ["all", ["==", ["get", "tier"], 1], ["!", ["get", "competitor_engaged"]]];
    const circleBase = ["any", ["get", "competitor_engaged"], ["!=", ["get", "tier"], 1]];
    // When Tier 1 Only: circles show only tier-1 loyalty (competitor_engaged tier-1)
    const circleWhenTier1 = ["all", ["get", "competitor_engaged"], ["==", ["get", "tier"], 1]];

    const circleFilter = showTier1Only ? circleWhenTier1 : circleBase;

    if (activeSpecialty === "All Specialties") {
      map.current.setFilter("prescriber-dots", circleFilter);
      map.current.setFilter("tier1-stars", wsBase);
    } else {
      const specExpr = ["==", ["get", "specialty"], activeSpecialty];
      map.current.setFilter("prescriber-dots", ["all", circleFilter, specExpr]);
      map.current.setFilter("tier1-stars", ["all", wsBase, specExpr]);
    }

    // ── ZIP density paint ───────────────────────────────────────────────────
    if (activeSpecialty === "All Specialties") {
      map.current.setPaintProperty("hcp-dots", "circle-color", DOMINANT_COLOR_EXPR);
      map.current.setPaintProperty("hcp-dots", "circle-radius", [
        "interpolate", ["linear"], ["zoom"],
        4,  ["interpolate", ["linear"], ["get", "total"], 0, 2, 50, 6],
        10, ["interpolate", ["linear"], ["get", "total"], 0, 4, 50, 14],
        14, ["interpolate", ["linear"], ["get", "total"], 0, 6, 50, 20],
      ]);
      map.current.setPaintProperty("hcp-dots", "circle-opacity", 0.82);
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
    }
  }, [activeSpecialty, showTier1Only, mapLoaded]);

  // ── Event location marker ────────────────────────────────────────────────────
  useEffect(() => {
    if (!map.current || !mapLoaded) return;

    if (eventMarkerRef.current) {
      eventMarkerRef.current.remove();
      eventMarkerRef.current = null;
    }
    if (!eventLocation) return;

    const el = document.createElement("div");
    el.className = "event-location-marker";
    el.textContent = "📍";

    eventMarkerRef.current = new maplibregl.Marker({ element: el, anchor: "bottom" })
      .setLngLat([eventLocation.lng, eventLocation.lat])
      .addTo(map.current);
  }, [eventLocation, mapLoaded]);

  // Signal label/color helpers
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
        <span className="filter-divider" />
        <button
          className={`filter-btn tier1-btn ${showTier1Only ? "active" : ""}`}
          onClick={() => setShowTier1Only((v) => !v)}
        >
          ★ Tier 1 Only
        </button>
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
          <span className="legend-separator" />
          <span className="legend-item">
            <span style={{ color: SIGNAL_COLORS.whitespace, fontSize: "13px", lineHeight: 1 }}>★</span>
            &nbsp;White Space
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
            <div
              className="tooltip-signal-badge"
              style={{ color: getSignalColor(hoveredPrescriber) }}
            >
              {hoveredPrescriber.tier === 1 && !hoveredPrescriber.competitor_engaged ? "★" : "●"}{" "}
              {getSignalLabel(hoveredPrescriber)}
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

        {/* Optimal Event Location callout */}
        {eventLocation && (
          <div className="event-location-callout">
            <div className="callout-title">📍 Optimal Event Location</div>
            <div className="callout-meta">
              {eventLocation.count.toLocaleString()} Tier 1 White Space targets
              {activeSpecialty !== "All Specialties" ? ` · ${activeSpecialty}` : ""}
            </div>
            <div className="callout-coords">
              {eventLocation.lat.toFixed(3)}°N, {Math.abs(eventLocation.lng).toFixed(3)}°W
            </div>
          </div>
        )}

        <div className="map-hint">
          Scroll to zoom · Drag to pan · Hover for detail · ★ = White Space target
        </div>
      </div>
    </>
  );
}
