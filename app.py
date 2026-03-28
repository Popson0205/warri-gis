from flask import Flask, jsonify, send_from_directory, Response, request
from flask_cors import CORS
import json, os, gzip, urllib.request, threading

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')

LAYER_FILES = {
    'Buildings':   None,  # loaded from remote only
    'Roads':       'Road.geojson',
    'Boundary':    'Warri Region.geojson',
    'Forest':      'Forest.geojson',
    'Woodland':    'Vegetation.geojson',
    'Wetlands':    'Wetlands.geojson',
    'Waterbodies': 'Water Bodies.geojson',
}

REMOTE_URLS = {
    'Buildings': os.environ.get('BUILDINGS_URL', 'https://github.com/Popson0205/warri-gis/releases/download/v1.0/Building.geojson'),
}

# ── Cache ─────────────────────────────────────────────────────────────────────
_layer_cache    = {}        # small layers in RAM
_buildings_data = None      # full buildings GeoJSON in RAM once downloaded
_buildings_lock = threading.Lock()
_buildings_ready = False

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

def _load_file(key):
    filename = LAYER_FILES.get(key)
    if not filename: return {'type': 'FeatureCollection', 'features': []}
    filepath = os.path.join(DATA_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'type': 'FeatureCollection', 'features': []}

def gzip_resp(data_str):
    compressed = gzip.compress(data_str.encode('utf-8'), compresslevel=6)
    r = Response(compressed, status=200, mimetype='application/json')
    r.headers['Content-Encoding'] = 'gzip'
    r.headers['Cache-Control']    = 'public, max-age=3600'
    r.headers['Vary']             = 'Accept-Encoding'
    return r

# ── Background: download buildings into RAM ───────────────────────────────────
def _fetch_buildings():
    global _buildings_data, _buildings_ready
    with _buildings_lock:
        if _buildings_ready:
            return
        url = REMOTE_URLS.get('Buildings', '')
        if not url:
            print('[buildings] No URL configured', flush=True)
            return
        try:
            print(f'[buildings] Downloading from {url}', flush=True)
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=300) as r:
                raw = json.loads(r.read().decode('utf-8'))
            features = raw.get('features', [])
            # Simplify coordinates to reduce memory
            for feat in features:
                geom = feat.get('geometry', {})
                if geom and geom.get('coordinates'):
                    geom['coordinates'] = _simplify(geom['coordinates'])
            _buildings_data  = features
            _buildings_ready = True
            print(f'[buildings] ✓ {len(features)} buildings loaded into RAM', flush=True)
        except Exception as e:
            print(f'[buildings] ✗ Download failed: {e}', flush=True)

def load_small_layers():
    for key in LAYER_FILES:
        if key == 'Buildings': continue
        try:
            data     = _load_file(key)
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

# ── Startup ───────────────────────────────────────────────────────────────────
load_small_layers()
threading.Thread(target=_fetch_buildings, daemon=True).start()

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/api/ready')
def ready():
    return jsonify({'buildings_ready': _buildings_ready})

@app.route('/api/layers')
def list_layers():
    result = {}
    for key in LAYER_FILES:
        if key == 'Buildings':
            result[key] = {'available': _buildings_ready, 'count': len(_buildings_data) if _buildings_data else 0}
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
    if not _buildings_ready:
        return jsonify({'error': 'Buildings still loading, please wait…', 'retry': True}), 503
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

    features = [f for f in (_buildings_data or []) if in_bbox(f)]
    return gzip_resp(json.dumps({'type': 'FeatureCollection', 'features': features}, separators=(',', ':')))

@app.route('/api/layer/<layer_name>/bbox')
def get_layer_bbox(layer_name):
    if layer_name == 'Buildings':
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
