from flask import Flask, jsonify, send_from_directory, Response, request
from flask_cors import CORS
import json, os, math, urllib.request, hashlib, tempfile

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

# ─── LAYER FILE MAP ───────────────────────────────────────────────────────────
# For large files (e.g. Buildings >100MB), set BUILDINGS_URL env variable
# to a GitHub Release asset URL or Supabase public URL.
# Local files in data/ take priority; remote URL is fallback.
LAYER_FILES = {
    'buildings':   'Buildings.geojson',
    'roads':       'Road.geojson',
    'boundary':    'Warri Region.geojson',
    'forest':      'Forest.geojson',
    'wetland':    'Wetland.geojson',
    'waterbodies': 'Waterbodies.geojson',
   
}

# Remote URL overrides (set via environment variables on Render)
REMOTE_URLS = {
    'buildings': os.environ.get('BUILDINGS_URL', ''),
    # Add more as needed:
    # 'roads': os.environ.get('ROADS_URL', ''),
}

# Simple in-memory cache for remote files (keyed by URL)
_remote_cache = {}

def load_geojson(layer_name):
    """Load GeoJSON: local file first, remote URL fallback, else empty."""
    filename = LAYER_FILES.get(layer_name)
    filepath = os.path.join(DATA_DIR, filename) if filename else None

    # 1. Try local file
    if filepath and os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)

    # 2. Try remote URL
    remote_url = REMOTE_URLS.get(layer_name, '')
    if remote_url:
        if remote_url in _remote_cache:
            print(f'[cache hit] {layer_name}')
            return _remote_cache[remote_url]
        try:
            print(f'[remote fetch] {layer_name} from {remote_url}')
            with urllib.request.urlopen(remote_url, timeout=60) as r:
                data = json.loads(r.read().decode('utf-8'))
            _remote_cache[remote_url] = data
            return data
        except Exception as e:
            print(f'[remote error] {layer_name}: {e}')

    # 3. Empty fallback
    return {'type': 'FeatureCollection', 'features': []}

def simplify_coords(coords):
    """Round coordinates to 6 decimal places to reduce payload size."""
    if not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 6), round(coords[1], 6)]
    return [simplify_coords(c) for c in coords]

def _flatten_coords(c):
    if not c: return
    if isinstance(c[0], (int, float)):
        yield c
    else:
        for item in c:
            yield from _flatten_coords(item)

# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'index.html')

@app.route('/api/layers')
def list_layers():
    available = {}
    for key, filename in LAYER_FILES.items():
        filepath = os.path.join(DATA_DIR, filename)
        local_exists = os.path.exists(filepath)
        remote_url = REMOTE_URLS.get(key, '')
        available[key] = {
            'available': local_exists or bool(remote_url),
            'source': 'local' if local_exists else ('remote' if remote_url else 'missing'),
            'filename': filename,
            'size_kb': round(os.path.getsize(filepath) / 1024, 1) if local_exists else None,
            'remote_url': remote_url if remote_url else None,
        }
    return jsonify(available)

@app.route('/api/layer/<layer_name>')
def get_layer(layer_name):
    if layer_name not in LAYER_FILES:
        return jsonify({'error': 'Layer not found'}), 404
    data = load_geojson(layer_name)
    if layer_name == 'buildings':
        # Reduce coordinate precision for buildings to cut payload
        for feat in data.get('features', []):
            geom = feat.get('geometry', {})
            if geom and geom.get('coordinates'):
                geom['coordinates'] = simplify_coords(geom['coordinates'])
    resp = Response(
        json.dumps(data, separators=(',', ':')),
        status=200, mimetype='application/json'
    )
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp

@app.route('/api/layer/<layer_name>/bbox')
def get_layer_bbox(layer_name):
    """Return only features within a viewport bounding box."""
    try:
        minx = float(request.args.get('minx'))
        miny = float(request.args.get('miny'))
        maxx = float(request.args.get('maxx'))
        maxy = float(request.args.get('maxy'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid bbox parameters'}), 400

    if layer_name not in LAYER_FILES:
        return jsonify({'error': 'Layer not found'}), 404

    data = load_geojson(layer_name)

    def feat_in_bbox(feat):
        geom = feat.get('geometry', {})
        if not geom: return False
        try:
            flat = list(_flatten_coords(geom.get('coordinates', [])))
            if not flat: return False
            fx = [c[0] for c in flat]; fy = [c[1] for c in flat]
            return (max(fx) >= minx and min(fx) <= maxx and
                    max(fy) >= miny and min(fy) <= maxy)
        except:
            return True

    filtered = [f for f in data.get('features', []) if feat_in_bbox(f)]

    # Simplify coords in viewport response too
    for feat in filtered:
        geom = feat.get('geometry', {})
        if geom and geom.get('coordinates'):
            geom['coordinates'] = simplify_coords(geom['coordinates'])

    result = {'type': 'FeatureCollection', 'features': filtered}
    resp = Response(
        json.dumps(result, separators=(',', ':')),
        status=200, mimetype='application/json'
    )
    return resp

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
