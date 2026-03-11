from flask import Flask, jsonify, send_from_directory, Response, request
from flask_cors import CORS
import json, os, gzip, math, urllib.request

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

LAYER_FILES = {
    'buildings':   'Buildings.geojson',
    'roads':       'Road.geojson',
    'boundary':    'Warri Region.geojson',
    'forest':      'Forest.geojson',
    'vegetation':  'Vegetation.geojson',
    'wetlands':    'Wetlands.geojson',
    'waterbodies': 'Waterbodies.geojson',
    'barerock':    'Barerock.geojson',
}

REMOTE_URLS = {
    'buildings': os.environ.get('BUILDINGS_URL', ''),
}

# ─── IN-MEMORY STORE ──────────────────────────────────────────────────────────
# All layers loaded once at startup into RAM
_layer_cache = {}        # key -> list of features (raw)
_spatial_index = {}      # key -> grid {cell: [feat, ...]}
GRID_SIZE = 0.01         # ~1km grid cells for spatial index

def _flatten_coords(c):
    if not c: return
    if isinstance(c[0], (int, float)):
        yield c
    else:
        for item in c:
            yield from _flatten_coords(item)

def _feat_bbox(feat):
    """Return (minx, miny, maxx, maxy) for a feature."""
    try:
        coords = list(_flatten_coords(feat['geometry']['coordinates']))
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return min(xs), min(ys), max(xs), max(ys)
    except:
        return None

def _simplify_coords(coords):
    if not coords: return coords
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 6), round(coords[1], 6)]
    return [_simplify_coords(c) for c in coords]

def _build_spatial_index(features):
    """Build a simple grid-based spatial index."""
    grid = {}
    for feat in features:
        bb = _feat_bbox(feat)
        if not bb: continue
        minx, miny, maxx, maxy = bb
        # Find all grid cells this feature touches
        cx0 = math.floor(minx / GRID_SIZE)
        cy0 = math.floor(miny / GRID_SIZE)
        cx1 = math.floor(maxx / GRID_SIZE)
        cy1 = math.floor(maxy / GRID_SIZE)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                cell = (cx, cy)
                if cell not in grid:
                    grid[cell] = []
                grid[cell].append(feat)
    return grid

def _query_spatial_index(grid, minx, miny, maxx, maxy):
    """Fast bbox query using spatial index — no full scan."""
    cx0 = math.floor(minx / GRID_SIZE)
    cy0 = math.floor(miny / GRID_SIZE)
    cx1 = math.floor(maxx / GRID_SIZE)
    cy1 = math.floor(maxy / GRID_SIZE)
    seen = set()
    results = []
    for cx in range(cx0, cx1 + 1):
        for cy in range(cy0, cy1 + 1):
            for feat in grid.get((cx, cy), []):
                fid = id(feat)
                if fid not in seen:
                    seen.add(fid)
                    results.append(feat)
    return results

def _load_raw(layer_name):
    """Load raw GeoJSON from disk or remote URL."""
    filename = LAYER_FILES.get(layer_name)
    filepath = os.path.join(DATA_DIR, filename) if filename else None

    if filepath and os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    remote_url = REMOTE_URLS.get(layer_name, '')
    if remote_url:
        print(f'[startup] Fetching {layer_name} from remote URL…')
        with urllib.request.urlopen(remote_url, timeout=120) as r:
            return json.loads(r.read().decode('utf-8'))

    return {'type': 'FeatureCollection', 'features': []}

def preload_all_layers():
    """Called once at startup — load everything into RAM."""
    for key in LAYER_FILES:
        try:
            print(f'[startup] Loading {key}…', flush=True)
            data = _load_raw(key)
            features = data.get('features', [])

            # Simplify coordinates once at load time
            for feat in features:
                geom = feat.get('geometry', {})
                if geom and geom.get('coordinates'):
                    geom['coordinates'] = _simplify_coords(geom['coordinates'])

            _layer_cache[key] = features

            # Build spatial index for buildings (most expensive bbox query)
            if key == 'buildings':
                print(f'[startup] Building spatial index for {len(features)} buildings…', flush=True)
                _spatial_index[key] = _build_spatial_index(features)

            print(f'[startup] ✓ {key}: {len(features)} features', flush=True)
        except Exception as e:
            print(f'[startup] ✗ {key}: {e}', flush=True)
            _layer_cache[key] = []

def gzip_response(data_str):
    """Return a gzip-compressed Flask Response."""
    compressed = gzip.compress(data_str.encode('utf-8'), compresslevel=6)
    resp = Response(compressed, status=200, mimetype='application/json')
    resp.headers['Content-Encoding'] = 'gzip'
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    resp.headers['Vary'] = 'Accept-Encoding'
    return resp

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'index.html')

@app.route('/api/layers')
def list_layers():
    result = {}
    for key, filename in LAYER_FILES.items():
        filepath = os.path.join(DATA_DIR, filename)
        count = len(_layer_cache.get(key, []))
        result[key] = {
            'available': count > 0 or bool(REMOTE_URLS.get(key)),
            'source': 'memory',
            'count': count,
        }
    return jsonify(result)

@app.route('/api/layer/<layer_name>')
def get_layer(layer_name):
    if layer_name not in LAYER_FILES:
        return jsonify({'error': 'Layer not found'}), 404
    features = _layer_cache.get(layer_name, [])
    result = json.dumps({'type': 'FeatureCollection', 'features': features}, separators=(',', ':'))
    return gzip_response(result)

@app.route('/api/layer/<layer_name>/bbox')
def get_layer_bbox(layer_name):
    try:
        minx = float(request.args.get('minx'))
        miny = float(request.args.get('miny'))
        maxx = float(request.args.get('maxx'))
        maxy = float(request.args.get('maxy'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid bbox parameters'}), 400

    if layer_name not in LAYER_FILES:
        return jsonify({'error': 'Layer not found'}), 404

    features = _layer_cache.get(layer_name, [])

    # Use spatial index for buildings, linear scan for others
    if layer_name in _spatial_index:
        candidates = _query_spatial_index(_spatial_index[layer_name], minx, miny, maxx, maxy)
    else:
        candidates = features

    # Precise filter
    def in_bbox(feat):
        bb = _feat_bbox(feat)
        if not bb: return True
        fx0, fy0, fx1, fy1 = bb
        return fx1 >= minx and fx0 <= maxx and fy1 >= miny and fy0 <= maxy

    filtered = [f for f in candidates if in_bbox(f)]
    result = json.dumps({'type': 'FeatureCollection', 'features': filtered}, separators=(',', ':'))
    return gzip_response(result)

if __name__ == '__main__':
    preload_all_layers()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
