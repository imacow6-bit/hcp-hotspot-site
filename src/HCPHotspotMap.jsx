import { useState, useMemo, useRef, useCallback, useEffect } from "react";
import * as d3 from "d3";
import * as topojson from "topojson-client";
import { METROS } from "./hcp_hotspot_data.js";
import {
  haversineDistance,
  findMetrosInRadius,
  weightedCentroid,
  getDocCount,
  getDensity,
} from "./haversine.js";

const SPECIALTIES = [
  "All Specialties",
  "Oncology",
  "Cardiology",
  "Orthopedics",
  "Neurology",
  "Endocrinology",
  "Pulmonology",
];

const WIDTH = 960;
const HEIGHT = 600;

export default function HCPHotspotMap() {
  const [activeSpecialty, setActiveSpecialty] = useState("All Specialties");
  const [hoveredCity, setHoveredCity] = useState(null);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [selectedCity, setSelectedCity] = useState(null);
  const [radius, setRadius] = useState(100);
  const [usStates, setUsStates] = useState(null);
  const [transform, setTransform] = useState(d3.zoomIdentity);

  const svgRef = useRef(null);
  const gRef = useRef(null);
  const zoomRef = useRef(null);

  // Load US TopoJSON
  useEffect(() => {
    fetch("/us-states-10m.json")
      .then((r) => r.json())
      .then((topo) => {
        const states = topojson.feature(topo, topo.objects.states);
        setUsStates(states);
      });
  }, []);

  // Projection
  const projection = useMemo(
    () => d3.geoAlbersUsa().scale(1100).translate([WIDTH / 2, HEIGHT / 2]),
    []
  );

  const pathGenerator = useMemo(
    () => d3.geoPath().projection(projection),
    [projection]
  );

  // Project metro coords
  const projected = useMemo(() => {
    return METROS.map((m) => {
      const coords = projection([m.lng, m.lat]);
      if (!coords) return null;
      return { ...m, x: coords[0], y: coords[1] };
    }).filter(Boolean);
  }, [projection]);

  // Max count for scaling
  const maxCount = useMemo(
    () => Math.max(...projected.map((m) => getDocCount(m, activeSpecialty))),
    [projected, activeSpecialty]
  );

  // Max density for color scale
  const densityExtent = useMemo(() => {
    const densities = projected.map((m) => getDensity(m, activeSpecialty));
    return [Math.min(...densities), Math.max(...densities)];
  }, [projected, activeSpecialty]);

  const colorScale = useMemo(
    () =>
      d3
        .scaleLinear()
        .domain([densityExtent[0], densityExtent[1]])
        .range(["#0288d1", "#00e5ff"])
        .clamp(true),
    [densityExtent]
  );

  // Haversine analysis for selected city
  const analysis = useMemo(() => {
    if (!selectedCity) return null;
    const origin = METROS.find((m) => m.city === selectedCity);
    if (!origin) return null;
    const nearby = findMetrosInRadius(origin, METROS, radius);
    const allInRadius = [origin, ...nearby.map((n) => n.metro)];
    const centroid = weightedCentroid(allInRadius, activeSpecialty);
    const totalDocs = allInRadius.reduce(
      (sum, m) => sum + getDocCount(m, activeSpecialty),
      0
    );
    const avgDensity =
      allInRadius.reduce(
        (sum, m) => sum + getDensity(m, activeSpecialty),
        0
      ) / allInRadius.length;
    return { origin, nearby, centroid, totalDocs, avgDensity, allInRadius };
  }, [selectedCity, radius, activeSpecialty]);

  // Set of city names in radius (for dimming)
  const citiesInRadius = useMemo(() => {
    if (!analysis) return null;
    return new Set(analysis.allInRadius.map((m) => m.city));
  }, [analysis]);

  // Zoom setup
  useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);
    const zoom = d3
      .zoom()
      .scaleExtent([1, 12])
      .on("zoom", (event) => {
        setTransform(event.transform);
      });
    zoomRef.current = zoom;
    svg.call(zoom);

    // Double-click to reset
    svg.on("dblclick.zoom", () => {
      svg.transition().duration(600).call(zoom.transform, d3.zoomIdentity);
      setSelectedCity(null);
    });
  }, []);

  // Zoom to city on selection
  useEffect(() => {
    if (!selectedCity || !svgRef.current || !zoomRef.current) return;
    const metro = projected.find((m) => m.city === selectedCity);
    if (!metro) return;
    const svg = d3.select(svgRef.current);
    const scale = 4;
    const tx = WIDTH / 2 - metro.x * scale;
    const ty = HEIGHT / 2 - metro.y * scale;
    svg
      .transition()
      .duration(800)
      .call(
        zoomRef.current.transform,
        d3.zoomIdentity.translate(tx, ty).scale(scale)
      );
  }, [selectedCity, projected]);

  // Handlers
  const handleCityClick = useCallback((cityName) => {
    setSelectedCity((prev) => (prev === cityName ? null : cityName));
  }, []);

  const handleMouseMove = useCallback((e) => {
    setMousePos({ x: e.clientX, y: e.clientY });
  }, []);

  const resetZoom = useCallback(() => {
    if (!svgRef.current || !zoomRef.current) return;
    d3.select(svgRef.current)
      .transition()
      .duration(600)
      .call(zoomRef.current.transform, d3.zoomIdentity);
    setSelectedCity(null);
  }, []);

  // Project the Haversine radius to SVG pixels
  const getRadiusPixels = useCallback(
    (origin, radiusMiles) => {
      // Approximate: 1 degree latitude ≈ 69 miles
      const degOffset = radiusMiles / 69;
      const p1 = projection([origin.lng, origin.lat]);
      const p2 = projection([origin.lng, origin.lat + degOffset]);
      if (!p1 || !p2) return 0;
      return Math.abs(p2[1] - p1[1]);
    },
    [projection]
  );

  // Project centroid to SVG
  const centroidPos = useMemo(() => {
    if (!analysis?.centroid) return null;
    const pos = projection([analysis.centroid.lng, analysis.centroid.lat]);
    return pos ? { x: pos[0], y: pos[1] } : null;
  }, [analysis, projection]);

  // Top cities for labels (visible at lower zoom)
  const topCities = useMemo(() => {
    const sorted = [...projected].sort(
      (a, b) => getDocCount(b, activeSpecialty) - getDocCount(a, activeSpecialty)
    );
    return new Set(sorted.slice(0, 15).map((m) => m.city));
  }, [projected, activeSpecialty]);

  return (
    <>
      {/* Top Bar */}
      <div className="topbar">
        <div>
          <h1>HCP Clinical Hotspot Map</h1>
          <div className="topbar-subtitle">
            Real CMS NPPES data · 50 metros · 127,667 matched physicians
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
            onClick={() => setActiveSpecialty(s)}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Main Layout */}
      <div className="main-layout">
        {/* Map */}
        <div className="map-container" onMouseMove={handleMouseMove}>
          <svg
            ref={svgRef}
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            preserveAspectRatio="xMidYMid meet"
          >
            <g ref={gRef} transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
              {/* State boundaries */}
              {usStates &&
                usStates.features.map((feature, i) => (
                  <path
                    key={i}
                    d={pathGenerator(feature)}
                    className="state-path"
                  />
                ))}

              {/* Radius ring */}
              {analysis && (
                <>
                  <circle
                    cx={projected.find((m) => m.city === selectedCity)?.x}
                    cy={projected.find((m) => m.city === selectedCity)?.y}
                    r={getRadiusPixels(analysis.origin, radius)}
                    className="radius-ring"
                  />
                  {/* Connection lines to nearby metros */}
                  {analysis.nearby.map((entry) => {
                    const from = projected.find(
                      (m) => m.city === selectedCity
                    );
                    const to = projected.find(
                      (m) => m.city === entry.metro.city
                    );
                    if (!from || !to) return null;
                    return (
                      <line
                        key={entry.metro.city}
                        x1={from.x}
                        y1={from.y}
                        x2={to.x}
                        y2={to.y}
                        className="connection-line"
                      />
                    );
                  })}
                </>
              )}

              {/* Metro dots */}
              {projected.map((m) => {
                const count = getDocCount(m, activeSpecialty);
                const density = getDensity(m, activeSpecialty);
                const r = 3 + (count / maxCount) * 18;
                const isDimmed =
                  citiesInRadius && !citiesInRadius.has(m.city);
                const isSelected = m.city === selectedCity;

                return (
                  <g key={m.city}>
                    <circle
                      cx={m.x}
                      cy={m.y}
                      r={r}
                      fill={colorScale(density)}
                      opacity={isDimmed ? 0.12 : 0.75}
                      className={`metro-dot ${isDimmed ? "dimmed" : ""}`}
                      stroke={isSelected ? "#fff" : "none"}
                      strokeWidth={isSelected ? 2 / transform.k : 0}
                      onClick={() => handleCityClick(m.city)}
                      onMouseEnter={() => setHoveredCity(m)}
                      onMouseLeave={() => setHoveredCity(null)}
                    />
                    {/* City label */}
                    <text
                      x={m.x}
                      y={m.y - r - 3}
                      textAnchor="middle"
                      className={`city-label ${
                        topCities.has(m.city) || (citiesInRadius && citiesInRadius.has(m.city))
                          ? "visible"
                          : ""
                      }`}
                      style={{ fontSize: `${9 / transform.k}px` }}
                    >
                      {m.city}
                    </text>
                  </g>
                );
              })}

              {/* Meeting point marker */}
              {centroidPos && (
                <g className="meeting-point">
                  <circle
                    cx={centroidPos.x}
                    cy={centroidPos.y}
                    r={12 / transform.k}
                    className="meeting-point-outer"
                  />
                  <circle
                    cx={centroidPos.x}
                    cy={centroidPos.y}
                    r={5 / transform.k}
                    className="meeting-point-inner"
                  />
                </g>
              )}
            </g>
          </svg>

          {/* Zoom hint */}
          <div className="map-hint">
            Scroll to zoom · Click city for analysis · Double-click to reset
          </div>

          {transform.k > 1.1 && (
            <button className="reset-zoom-btn" onClick={resetZoom}>
              ↩ Reset View
            </button>
          )}
        </div>

        {/* Analysis Panel */}
        <div className={`analysis-panel ${analysis ? "open" : ""}`}>
          {analysis && (
            <>
              <div className="panel-header">
                <div>
                  <div className="panel-title">
                    📍 {analysis.origin.city}, {analysis.origin.state}
                  </div>
                  <div className="panel-subtitle">
                    Event Placement Analysis · {activeSpecialty}
                  </div>
                </div>
                <button className="panel-close" onClick={resetZoom}>
                  ✕
                </button>
              </div>

              {/* Radius control */}
              <div className="panel-section">
                <div className="panel-section-title">Search Radius</div>
                <div className="radius-control">
                  <input
                    type="range"
                    min="25"
                    max="200"
                    step="5"
                    value={radius}
                    onChange={(e) => setRadius(Number(e.target.value))}
                    className="radius-slider"
                  />
                  <span className="radius-value">{radius} mi</span>
                </div>
              </div>

              {/* Key stats */}
              <div className="panel-section">
                <div className="panel-section-title">Key Metrics</div>
                <div className="stat-grid">
                  <div className="stat-card">
                    <div className="stat-card-value">
                      {analysis.totalDocs.toLocaleString()}
                    </div>
                    <div className="stat-card-label">Total HCPs</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-card-value">
                      {analysis.allInRadius.length}
                    </div>
                    <div className="stat-card-label">Metros in Range</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-card-value">
                      {Math.round(analysis.avgDensity)}
                    </div>
                    <div className="stat-card-label">Avg Density /100k</div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-card-value">{radius} mi</div>
                    <div className="stat-card-label">Radius</div>
                  </div>
                </div>
              </div>

              {/* Optimal location */}
              {analysis.centroid && (
                <div className="panel-section">
                  <div className="panel-section-title">
                    Optimal Event Location
                  </div>
                  <div className="optimal-location">
                    <div className="optimal-location-title">
                      ⊕ Weighted Centroid
                    </div>
                    <div className="optimal-location-coords">
                      {analysis.centroid.lat.toFixed(4)}°N,{" "}
                      {Math.abs(analysis.centroid.lng).toFixed(4)}°W
                    </div>
                    <div
                      style={{
                        fontSize: "11px",
                        color: "#9aa5b4",
                        marginTop: "8px",
                        lineHeight: 1.5,
                      }}
                    >
                      Computed via Haversine-weighted centroid using{" "}
                      {activeSpecialty.toLowerCase()} physician density across{" "}
                      {analysis.allInRadius.length} metro areas.
                    </div>
                  </div>
                </div>
              )}

              {/* Nearby metros list */}
              <div className="panel-section">
                <div className="panel-section-title">
                  Metros in Range ({analysis.nearby.length})
                </div>
                <ul className="metro-list">
                  {analysis.nearby.map((entry) => (
                    <li key={entry.metro.city} className="metro-list-item">
                      <div>
                        <span className="metro-list-city">
                          {entry.metro.city}
                        </span>
                        <span className="metro-list-distance">
                          {Math.round(entry.distance)} mi
                        </span>
                      </div>
                      <span className="metro-list-docs">
                        {getDocCount(
                          entry.metro,
                          activeSpecialty
                        ).toLocaleString()}
                      </span>
                    </li>
                  ))}
                  {analysis.nearby.length === 0 && (
                    <li
                      className="metro-list-item"
                      style={{ color: "var(--text-dim)", fontSize: "12px" }}
                    >
                      No other metros within {radius} mi. Try increasing the
                      radius.
                    </li>
                  )}
                </ul>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Tooltip */}
      {hoveredCity && (
        <div
          className="tooltip"
          style={{
            left: mousePos.x + 16,
            top: mousePos.y - 10,
          }}
        >
          <div className="tooltip-city">
            {hoveredCity.city}, {hoveredCity.state}
          </div>
          <div className="tooltip-meta">
            Pop. {hoveredCity.pop.toLocaleString()}
          </div>
          {Object.entries(hoveredCity.docs).map(([spec, count]) => (
            <div
              key={spec}
              className="tooltip-row"
              style={{
                fontWeight: activeSpecialty === spec ? 700 : 400,
                color: activeSpecialty === spec ? "#00e5ff" : undefined,
              }}
            >
              <span>{spec}</span>
              <span>{count.toLocaleString()}</span>
            </div>
          ))}
          <div className="tooltip-density">
            <span>Density ({activeSpecialty.split(" ")[0]})</span>
            <span>
              {getDensity(hoveredCity, activeSpecialty).toFixed(1)} / 100k
            </span>
          </div>
        </div>
      )}
    </>
  );
}