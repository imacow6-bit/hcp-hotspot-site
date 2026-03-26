"""
Re-geocode prescriber_scores.json using real US city coordinates.
Replaces hash-based state bounding box placement with actual city lat/lng.
"""
import json, random

random.seed(42)

# Major US cities: state -> [(city, lat, lng, relative_weight), ...]
US_CITIES = {
    "AL": [("Birmingham",33.5186,-86.8104,60),("Huntsville",34.7304,-86.5861,30),("Mobile",30.6954,-88.0399,25),("Montgomery",32.3617,-86.2791,25),("Tuscaloosa",33.2098,-87.5692,15)],
    "AK": [("Anchorage",61.2181,-149.9003,80),("Fairbanks",64.8378,-147.7164,10),("Juneau",58.3005,-134.4197,10)],
    "AZ": [("Phoenix",33.4484,-112.0740,100),("Tucson",32.2226,-110.9747,40),("Mesa",33.4152,-111.8315,20),("Scottsdale",33.4942,-111.9261,15),("Chandler",33.3062,-111.8413,10)],
    "AR": [("Little Rock",34.7465,-92.2896,50),("Fayetteville",36.0626,-94.1574,20),("Fort Smith",35.3859,-94.3985,15),("Jonesboro",35.8423,-90.7043,10)],
    "CA": [("Los Angeles",34.0522,-118.2437,100),("San Francisco",37.7749,-122.4194,60),("San Diego",32.7157,-117.1611,50),("San Jose",37.3382,-121.8863,35),("Sacramento",38.5816,-121.4944,25),("Fresno",36.7378,-119.7871,15),("Oakland",37.8044,-122.2712,15),("Long Beach",33.7701,-118.1937,12),("Bakersfield",35.3733,-119.0187,10),("Irvine",33.6846,-117.8265,10)],
    "CO": [("Denver",39.7392,-104.9903,80),("Colorado Springs",38.8339,-104.8214,25),("Aurora",39.7294,-104.8319,15),("Fort Collins",40.5853,-105.0844,10),("Boulder",40.0150,-105.2705,10)],
    "CT": [("Hartford",41.7658,-72.6734,35),("New Haven",41.3083,-72.9279,35),("Bridgeport",41.1865,-73.1952,20),("Stamford",41.0534,-73.5387,15),("Waterbury",41.5582,-73.0515,10)],
    "DE": [("Wilmington",39.7391,-75.5398,50),("Dover",39.1582,-75.5244,20),("Newark",39.6837,-75.7497,15)],
    "DC": [("Washington",38.9072,-77.0369,100)],
    "FL": [("Miami",25.7617,-80.1918,80),("Jacksonville",30.3322,-81.6557,35),("Tampa",27.9506,-82.4572,40),("Orlando",28.5383,-81.3792,35),("St. Petersburg",27.7676,-82.6403,15),("Fort Lauderdale",26.1224,-80.1373,20),("Tallahassee",30.4383,-84.2807,10),("Sarasota",27.3364,-82.5307,12)],
    "GA": [("Atlanta",33.7490,-84.3880,100),("Augusta",33.4735,-81.9748,20),("Savannah",32.0809,-81.0912,15),("Columbus",32.4610,-84.9877,10),("Macon",32.8407,-83.6324,10)],
    "HI": [("Honolulu",21.3069,-157.8583,90),("Hilo",19.7074,-155.0847,10)],
    "ID": [("Boise",43.6150,-116.2023,60),("Meridian",43.6121,-116.3915,15),("Idaho Falls",43.4917,-112.0339,10),("Pocatello",42.8713,-112.4455,8)],
    "IL": [("Chicago",41.8781,-87.6298,100),("Aurora",41.7606,-88.3201,10),("Rockford",42.2711,-89.0940,8),("Springfield",39.7817,-89.6501,8),("Peoria",40.6936,-89.5890,7),("Naperville",41.7508,-88.1535,8)],
    "IN": [("Indianapolis",39.7684,-86.1581,60),("Fort Wayne",41.0793,-85.1394,15),("Evansville",37.9716,-87.5711,10),("South Bend",41.6764,-86.2520,10),("Carmel",39.9784,-86.1180,8)],
    "IA": [("Des Moines",41.5868,-93.6250,40),("Cedar Rapids",41.9779,-91.6656,15),("Davenport",41.5236,-90.5776,10),("Iowa City",41.6611,-91.5302,12),("Sioux City",42.4963,-96.4049,8)],
    "KS": [("Wichita",37.6872,-97.3301,30),("Overland Park",38.9822,-94.6708,25),("Kansas City",39.1142,-94.6275,25),("Topeka",39.0473,-95.6752,15),("Lawrence",38.9717,-95.2353,10)],
    "KY": [("Louisville",38.2527,-85.7585,50),("Lexington",38.0406,-84.5037,30),("Bowling Green",36.9685,-86.4808,10),("Covington",39.0837,-84.5086,8)],
    "LA": [("New Orleans",29.9511,-90.0715,50),("Baton Rouge",30.4515,-91.1871,30),("Shreveport",32.5252,-93.7502,15),("Lafayette",30.2241,-92.0198,12),("Lake Charles",30.2266,-93.2174,8)],
    "ME": [("Portland",43.6591,-70.2568,40),("Bangor",44.8016,-68.7712,15),("Lewiston",44.1004,-70.2148,10),("Augusta",44.3106,-69.7795,8)],
    "MD": [("Baltimore",39.2904,-76.6122,60),("Bethesda",38.9847,-77.0947,20),("Rockville",39.0840,-77.1528,12),("Silver Spring",38.9907,-76.9820,10),("Columbia",39.2037,-76.8610,10)],
    "MA": [("Boston",42.3601,-71.0589,80),("Worcester",42.2626,-71.8023,15),("Springfield",42.1015,-72.5898,12),("Cambridge",42.3736,-71.1097,15),("Lowell",42.6334,-71.3162,8)],
    "MI": [("Detroit",42.3314,-83.0458,60),("Grand Rapids",42.9634,-85.6681,20),("Ann Arbor",42.2808,-83.7430,20),("Lansing",42.7325,-84.5555,10),("Kalamazoo",42.2917,-85.5872,8),("Flint",43.0125,-83.6875,8)],
    "MN": [("Minneapolis",44.9778,-93.2650,60),("St. Paul",44.9537,-93.0900,25),("Rochester",44.0121,-92.4802,20),("Duluth",46.7867,-92.1005,8),("Bloomington",44.8408,-93.2983,8)],
    "MS": [("Jackson",32.2988,-90.1848,40),("Gulfport",30.3674,-89.0928,12),("Hattiesburg",31.3271,-89.2903,10),("Southaven",34.9889,-90.0126,8),("Tupelo",34.2576,-88.7034,8)],
    "MO": [("Kansas City",39.0997,-94.5786,50),("St. Louis",38.6270,-90.1994,50),("Springfield",37.2090,-93.2923,15),("Columbia",38.9517,-92.3341,12),("Independence",39.0911,-94.4155,8)],
    "MT": [("Billings",45.7833,-108.5007,30),("Missoula",46.8721,-114.0001,20),("Great Falls",47.5002,-111.3008,12),("Helena",46.5958,-112.0270,10),("Bozeman",45.6770,-111.0429,12)],
    "NE": [("Omaha",41.2565,-95.9345,50),("Lincoln",40.8136,-96.7026,25),("Grand Island",40.9264,-98.3420,8),("Kearney",40.6993,-99.0832,6)],
    "NV": [("Las Vegas",36.1699,-115.1398,80),("Reno",39.5296,-119.8138,20),("Henderson",36.0395,-114.9817,10),("Sparks",39.5349,-119.7527,5)],
    "NH": [("Manchester",42.9956,-71.4548,30),("Nashua",42.7654,-71.4676,20),("Concord",43.2081,-71.5376,12),("Dover",43.1979,-70.8737,8),("Lebanon",43.6423,-72.2518,10)],
    "NJ": [("Newark",40.7357,-74.1724,30),("Jersey City",40.7178,-74.0431,20),("New Brunswick",40.4863,-74.4518,15),("Trenton",40.2171,-74.7429,10),("Camden",39.9259,-75.1196,8),("Hackensack",40.8859,-74.0435,12),("Morristown",40.7968,-74.4815,10),("Atlantic City",39.3643,-74.4229,8)],
    "NM": [("Albuquerque",35.0844,-106.6504,60),("Santa Fe",35.6870,-105.9378,15),("Las Cruces",32.3199,-106.7637,12),("Rio Rancho",35.2328,-106.6630,8)],
    "NY": [("New York",40.7128,-74.0060,100),("Buffalo",42.8864,-78.8784,15),("Rochester",43.1566,-77.6088,12),("Albany",42.6526,-73.7562,12),("Syracuse",43.0481,-76.1474,10),("White Plains",41.0340,-73.7629,8),("Yonkers",40.9312,-73.8987,8)],
    "NC": [("Charlotte",35.2271,-80.8431,50),("Raleigh",35.7796,-78.6382,35),("Durham",35.9940,-78.8986,20),("Greensboro",36.0726,-79.7920,15),("Winston-Salem",36.0999,-80.2442,12),("Wilmington",34.2257,-77.9447,10),("Asheville",35.5951,-82.5515,10)],
    "ND": [("Fargo",46.8772,-96.7898,30),("Bismarck",46.8083,-100.7837,20),("Grand Forks",47.9253,-97.0329,12),("Minot",48.2330,-101.2923,8)],
    "OH": [("Columbus",39.9612,-82.9988,50),("Cleveland",41.4993,-81.6944,40),("Cincinnati",39.1031,-84.5120,35),("Toledo",41.6528,-83.5379,12),("Akron",41.0814,-81.5190,10),("Dayton",39.7589,-84.1916,12)],
    "OK": [("Oklahoma City",35.4676,-97.5164,50),("Tulsa",36.1540,-95.9928,35),("Norman",35.2226,-97.4395,10),("Edmond",35.6528,-97.4781,8)],
    "OR": [("Portland",45.5152,-122.6784,60),("Eugene",44.0521,-123.0868,15),("Salem",44.9429,-123.0351,12),("Bend",44.0582,-121.3153,8),("Medford",42.3265,-122.8756,8)],
    "PA": [("Philadelphia",39.9526,-75.1652,60),("Pittsburgh",40.4406,-79.9959,40),("Allentown",40.6084,-75.4902,10),("Harrisburg",40.2732,-76.8867,10),("Erie",42.1292,-80.0851,8),("Lancaster",40.0379,-76.3055,8),("Reading",40.3357,-75.9269,7)],
    "PR": [("San Juan",18.4655,-66.1057,60),("Ponce",18.0111,-66.6141,15),("Mayaguez",18.2013,-67.1397,10),("Bayamon",18.3985,-66.1568,15)],
    "RI": [("Providence",41.8240,-71.4128,50),("Warwick",41.7001,-71.4162,12),("Cranston",41.7798,-71.4373,10),("Newport",41.4901,-71.3128,8)],
    "SC": [("Charleston",32.7765,-79.9311,30),("Columbia",34.0007,-81.0348,25),("Greenville",34.8526,-82.3940,20),("Myrtle Beach",33.6891,-78.8867,10),("Spartanburg",34.9496,-81.9320,8)],
    "SD": [("Sioux Falls",43.5446,-96.7311,35),("Rapid City",44.0805,-103.2310,15),("Aberdeen",45.4647,-98.4865,8),("Brookings",44.3114,-96.7984,6)],
    "TN": [("Nashville",36.1627,-86.7816,50),("Memphis",35.1495,-90.0490,40),("Knoxville",35.9606,-83.9207,20),("Chattanooga",35.0456,-85.3097,15),("Murfreesboro",35.8456,-86.3903,8)],
    "TX": [("Houston",29.7604,-95.3698,80),("Dallas",32.7767,-96.7970,60),("San Antonio",29.4241,-98.4936,50),("Austin",30.2672,-97.7431,40),("Fort Worth",32.7555,-97.3308,20),("El Paso",31.7619,-106.4850,15),("Plano",33.0198,-96.6989,10),("Lubbock",33.5779,-101.8552,8),("Corpus Christi",27.8006,-97.3964,8),("McAllen",26.2034,-98.2300,8)],
    "UT": [("Salt Lake City",40.7608,-111.8910,60),("Provo",40.2338,-111.6585,15),("West Valley City",40.6916,-112.0011,10),("Ogden",41.2230,-111.9738,10),("St. George",37.0965,-113.5684,8)],
    "VT": [("Burlington",44.4759,-73.2121,40),("Rutland",43.6106,-72.9726,12),("Montpelier",44.2601,-72.5754,10),("Brattleboro",42.8509,-72.5579,8)],
    "VA": [("Virginia Beach",36.8529,-75.9780,20),("Richmond",37.5407,-77.4360,30),("Norfolk",36.8508,-76.2859,15),("Arlington",38.8816,-77.0910,20),("Charlottesville",38.0293,-78.4767,12),("Roanoke",37.2710,-79.9414,10),("Alexandria",38.8048,-77.0469,12)],
    "WA": [("Seattle",47.6062,-122.3321,80),("Spokane",47.6588,-117.4260,15),("Tacoma",47.2529,-122.4443,12),("Bellevue",47.6101,-122.2015,12),("Vancouver",45.6387,-122.6615,8),("Olympia",47.0379,-122.9007,6)],
    "WV": [("Charleston",38.3498,-81.6326,30),("Huntington",38.4192,-82.4452,15),("Morgantown",39.6295,-79.9559,15),("Wheeling",40.0640,-80.7209,8),("Parkersburg",39.2667,-81.5615,8)],
    "WI": [("Milwaukee",43.0389,-87.9065,50),("Madison",43.0731,-89.4012,35),("Green Bay",44.5133,-88.0133,12),("Kenosha",42.5847,-87.8212,8),("Appleton",44.2619,-88.4154,8)],
    "WY": [("Cheyenne",41.1400,-104.8202,25),("Casper",42.8666,-106.3131,15),("Laramie",41.3114,-105.5911,10),("Gillette",44.2911,-105.5022,6),("Jackson",43.4799,-110.7624,6)],
}

print("=== Improve Prescriber Geocoding (Real City Coords) ===\n")

with open("public/prescriber_scores.json") as f:
    prescribers = json.load(f)

print(f"Loaded {len(prescribers):,} prescribers")

# Build weighted city lists per state
state_city_weights = {}
for state, cities in US_CITIES.items():
    total_w = sum(c[3] for c in cities)
    cumulative = []
    running = 0
    for city, lat, lng, w in cities:
        running += w / total_w
        cumulative.append((city, lat, lng, running))
    state_city_weights[state] = cumulative

def pick_city(state):
    """Pick a city in this state weighted by population."""
    cities = state_city_weights.get(state)
    if not cities:
        return None
    r = random.random()
    for city, lat, lng, cum in cities:
        if r <= cum:
            return (lat, lng)
    return (cities[-1][1], cities[-1][2])

patched = 0
skipped = 0

for p in prescribers:
    state = p.get("state", "")
    coords = pick_city(state)
    if coords:
        # Jitter: ~5-8 miles spread within metro area
        jitter_lat = random.gauss(0, 0.06)
        jitter_lng = random.gauss(0, 0.075)
        p["lat"] = round(coords[0] + jitter_lat, 4)
        p["lng"] = round(coords[1] + jitter_lng, 4)
        patched += 1
    else:
        skipped += 1

with open("public/prescriber_scores.json", "w") as f:
    json.dump(prescribers, f, separators=(",", ":"))

print(f"Patched: {patched:,}")
print(f"Skipped (unknown state): {skipped:,}")
print("Done!")
