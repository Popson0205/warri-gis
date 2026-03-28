from flask import Flask, jsonify, send_from_directory, Response, request
from flask_cors import CORS
import json, os, gzip, math, urllib.request

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE_DIR, 'data')
TILES_DIR = os.path.join(BASE_DIR, 'tiles')   # pre-tiled buildings on disk
TILE_DEG  = 0.02                               # ~2km per tile

LAYER_FILES = {
    'Buildings':   'Buildings.geojson',
    'Roads':       'Road.geojson',
    'Boundary':    'Warri Region.geojson',
    'Forest':      'Forest.geojson',
    'Wetlands':    'Wetland.geojson',
    'Waterbodies': 'Waterbodies.geojson',
    'Woodland':    'Woodland.geojson',
}

REMOTE_URLS = {
    'buildings': os.environ.get('BUILDINGS_URL', ''),
}

# ── Small layers loaded fully into RAM (all except buildings) ─────────────────
_layer_cache = {}

# ── Helpers ───────────────────────────────────────────────────────────────────
def _flatten_coords(c):
    if not c: return
    if isinstance(c[0], (int, float)): yield c
    else:
        for i in c: yield from _flatten_coords(i)

def _feat_bbox(feat):
    try:
        coords = list(_flatten_coords(feat['geometry']['coordinates']))
        xs = [c[0] for c in coords]; ys = [c[1] for c in coords]
        return min(xs), min(ys), max(xs), max(ys)
    except: return None

def _simplify(coords):
    if not coords: return coords
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 6), round(coords[1], 6)]
    return [_simplify(c) for c in coords]

def _load_raw(layer_name):
    filename = LAYER_FILES.get(layer_name)
    filepath = os.path.join(DATA_DIR, filename) if filename else None
    if filepath and os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    remote_url = REMOTE_URLS.get(layer_name, '')
    if remote_url:
        print(f'[startup] Fetching {layer_name} from {remote_url}', flush=True)
        with urllib.request.urlopen(remote_url, timeout=180) as r:
            return json.loads(r.read().decode('utf-8'))
    return {'type': 'FeatureCollection', 'features': []}

def _tile_key(x, y): return f'{x}_{y}'
def _tile_path(x, y): return os.path.join(TILES_DIR, f'{x}_{y}.json.gz')

# ── PRE-TILE buildings to disk ────────────────────────────────────────────────
def pretile_buildings():
    """
    Split buildings into small ~2km tile files on disk.
    Each tile is a gzipped GeoJSON FeatureCollection.
    Only tiles that are requested get loaded — never the whole dataset.
    """
    os.makedirs(TILES_DIR, exist_ok=True)
    # Skip if already tiled
    existing = [f for f in os.listdir(TILES_DIR) if f.endswith('.json.gz')]
    if existing:
        print(f'[startup] Buildings already tiled ({len(existing)} tiles found)', flush=True)
        return

    print('[startup] Pre-tiling buildings — this runs once…', flush=True)
    data = _load_raw('buildings')
    features = data.get('features', [])
    print(f'[startup] Tiling {len(features)} building features…', flush=True)

    grid = {}
    for feat in features:
        bb = _feat_bbox(feat)
        if not bb: continue
        minx, miny, maxx, maxy = bb
        # Simplify coords to reduce tile size
        geom = feat.get('geometry', {})
        if geom and geom.get('coordinates'):
            geom['coordinates'] = _simplify(geom['coordinates'])
        cx0 = math.floor(minx / TILE_DEG)
        cy0 = math.floor(miny / TILE_DEG)
        cx1 = math.floor(maxx / TILE_DEG)
        cy1 = math.floor(maxy / TILE_DEG)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                k = _tile_key(cx, cy)
                if k not in grid: grid[k] = {'cx': cx, 'cy': cy, 'feats': []}
                grid[k]['feats'].append(feat)

    for k, tile in grid.items():
        fc = {'type': 'FeatureCollection', 'features': tile['feats']}
        raw = json.dumps(fc, separators=(',', ':')).encode('utf-8')
        with gzip.open(_tile_path(tile['cx'], tile['cy']), 'wb', compresslevel=6) as f:
            f.write(raw)

    print(f'[startup] ✓ Buildings tiled into {len(grid)} tiles', flush=True)

def load_small_layers():
    """Load all non-buildings layers into RAM (they're small)."""
    for key in LAYER_FILES:
        if key == 'buildings': continue
        try:
            data = _load_raw(key)
            features = data.get('features', [])
            for feat in features:
                geom = feat.get('geometry', {})
                if geom and geom.get('coordinates'):
                    geom['coordinates'] = _simplify(geom['coordinates'])
            _layer_cache[key] = features
            print(f'[startup] ✓ {key}: {len(features)} features', flush=True)
        except Exception as e:
            print(f'[startup] ✗ {key}: {e}', flush=True)
            _layer_cache[key] = []

def gzip_resp(data_str):
    compressed = gzip.compress(data_str.encode('utf-8'), compresslevel=6)
    r = Response(compressed, status=200, mimetype='application/json')
    r.headers['Content-Encoding'] = 'gzip'
    r.headers['Cache-Control'] = 'public, max-age=3600'
    r.headers['Vary'] = 'Accept-Encoding'
    return r

# ── Run startup ───────────────────────────────────────────────────────────────
load_small_layers()
pretile_buildings()

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/api/layers')
def list_layers():
    result = {}
    for key in LAYER_FILES:
        if key == 'buildings':
            tiles = len([f for f in os.listdir(TILES_DIR) if f.endswith('.gz')]) if os.path.exists(TILES_DIR) else 0
            result[key] = {'available': tiles > 0 or bool(REMOTE_URLS.get(key)), 'tiles': tiles}
        else:
            result[key] = {'available': len(_layer_cache.get(key, [])) > 0, 'count': len(_layer_cache.get(key, []))}
    return jsonify(result)

@app.route('/api/layer/<layer_name>')
def get_layer(layer_name):
    if layer_name not in LAYER_FILES:
        return jsonify({'error': 'Layer not found'}), 404
    features = _layer_cache.get(layer_name, [])
    return gzip_resp(json.dumps({'type': 'FeatureCollection', 'features': features}, separators=(',', ':')))

@app.route('/api/layer/buildings/bbox')
def buildings_bbox():
    try:
        minx = float(request.args.get('minx'))
        miny = float(request.args.get('miny'))
        maxx = float(request.args.get('maxx'))
        maxy = float(request.args.get('maxy'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid bbox'}), 400

    cx0 = math.floor(minx / TILE_DEG)
    cy0 = math.floor(miny / TILE_DEG)
    cx1 = math.floor(maxx / TILE_DEG)
    cy1 = math.floor(maxy / TILE_DEG)

    seen = set(); features = []
    for cx in range(cx0, cx1 + 1):
        for cy in range(cy0, cy1 + 1):
            tp = _tile_path(cx, cy)
            if not os.path.exists(tp): continue
            try:
                with gzip.open(tp, 'rb') as f:
                    tile = json.loads(f.read())
                for feat in tile.get('features', []):
                    fid = id(feat)
                    if fid not in seen:
                        seen.add(fid)
                        features.append(feat)
            except Exception as e:
                print(f'[tile error] {cx},{cy}: {e}')

    result = json.dumps({'type': 'FeatureCollection', 'features': features}, separators=(',', ':'))
    return gzip_resp(result)

@app.route('/api/layer/<layer_name>/bbox')
def get_layer_bbox(layer_name):
    if layer_name == 'buildings':
        return buildings_bbox()
    if layer_name not in LAYER_FILES:
        return jsonify({'error': 'Layer not found'}), 404
    try:
        minx = float(request.args.get('minx'))
        miny = float(request.args.get('miny'))
        maxx = float(request.args.get('maxx'))
        maxy = float(request.args.get('maxy'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid bbox'}), 400

    def in_bbox(feat):
        bb = _feat_bbox(feat)
        if not bb: return True
        fx0, fy0, fx1, fy1 = bb
        return fx1 >= minx and fx0 <= maxx and fy1 >= miny and fy0 <= maxy

    features = [f for f in _layer_cache.get(layer_name, []) if in_bbox(f)]
    return gzip_resp(json.dumps({'type': 'FeatureCollection', 'features': features}, separators=(',', ':')))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
