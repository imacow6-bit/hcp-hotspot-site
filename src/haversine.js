/**
 * Haversine & geographic utility functions for HCP event placement.
 * All distances in miles. All coordinates in decimal degrees.
 */

const EARTH_RADIUS_MI = 3958.8;

/** Convert degrees to radians */
function toRad(deg) {
  return (deg * Math.PI) / 180;
}

/**
 * Haversine distance between two lat/lng points.
 * Returns distance in miles.
 */
export function haversineDistance(lat1, lng1, lat2, lng2) {
  const dLat = toRad(lat2 - lat1);
  const dLng = toRad(lng2 - lng1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return EARTH_RADIUS_MI * c;
}

/**
 * Find all metros within a given radius (miles) of an origin metro.
 * Returns array of { metro, distance } sorted by distance ascending.
 */
export function findMetrosInRadius(origin, allMetros, radiusMiles) {
  return allMetros
    .map((m) => ({
      metro: m,
      distance: haversineDistance(origin.lat, origin.lng, m.lat, m.lng),
    }))
    .filter((entry) => entry.distance <= radiusMiles && entry.distance > 0)
    .sort((a, b) => a.distance - b.distance);
}

/**
 * Compute the weighted geographic centroid of a set of metros.
 * Weights are the doctor count for the given specialty.
 * Returns { lat, lng } of the optimal meeting point.
 */
export function weightedCentroid(metros, specialty) {
  let totalWeight = 0;
  let sumLat = 0;
  let sumLng = 0;

  for (const m of metros) {
    const w =
      specialty === "All Specialties"
        ? Object.values(m.docs).reduce((a, b) => a + b, 0)
        : m.docs[specialty] || 0;
    sumLat += m.lat * w;
    sumLng += m.lng * w;
    totalWeight += w;
  }

  if (totalWeight === 0) return null;
  return { lat: sumLat / totalWeight, lng: sumLng / totalWeight };
}

/**
 * Get total doctor count for a metro, optionally filtered by specialty.
 */
export function getDocCount(metro, specialty) {
  if (specialty === "All Specialties") {
    return Object.values(metro.docs).reduce((a, b) => a + b, 0);
  }
  return metro.docs[specialty] || 0;
}

/**
 * Get density (doctors per 100k population) for a metro + specialty.
 */
export function getDensity(metro, specialty) {
  return (getDocCount(metro, specialty) / metro.pop) * 100000;
}
