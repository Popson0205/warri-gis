from flask import Flask, jsonify, send_from_directory, Response, request
from flask_cors import CORS
import json, os, gzip, math

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE_DIR, 'data')
TILES_DIR = os.path.join(BASE_DIR, 'tiles')
TILE_DEG  = 0.02

LAYER_FILES = {
    'buildings':   'Buildings.geojson',
    'roads':       'Road.geojson',
    'boundary':    'Warri Region.geojson',
    'forest':      'Forest.geojson',
    'vegetation':  'Vegetation.geojson',
    'wetlands':    'Wetlands.geojson',
    'waterbodies': 'Water Bodies.geojson',
    'barerock':    'Barerock.geojson',
}

def _flatten_coords(c):
    if not c: return
    if isinstance(c[0], (int, float)): yield c
    else:
        for i in c: yield from _flatten_coords(i)

def _feat_bbox(feat):
    try:
        coords = list(_flatten_coords(feat['geometry']['coordinates']))
        xs=[c[0] for c in coords]; ys=[c[1] for c in coords]
        return min(xs),min(ys),max(xs),max(ys)
    except: return None

def _simplify(coords):
    if not coords: return coords
    if isinstance(coords[0],(int,float)):
        return [round(coords[0],6),round(coords[1],6)]
    return [_simplify(c) for c in coords]

def _gzip(s):
    c = gzip.compress(s.encode('utf-8'), compresslevel=6)
    r = Response(c, status=200, mimetype='application/json')
    r.headers['Content-Encoding'] = 'gzip'
    r.headers['Cache-Control'] = 'public, max-age=3600'
    return r

# Load small layers into RAM at startup (buildings handled via tiles)
_cache = {}
for key, fname in LAYER_FILES.items():
    if key == 'buildings': continue
    try:
        fp = os.path.join(DATA_DIR, fname)
        if os.path.exists(fp):
            with open(fp,'r',encoding='utf-8') as f:
                data = json.load(f)
            feats = data.get('features',[])
            for feat in feats:
                geom = feat.get('geometry',{})
                if geom and geom.get('coordinates'):
                    geom['coordinates'] = _simplify(geom['coordinates'])
            _cache[key] = feats
            print(f'[startup] ✓ {key}: {len(feats)} features', flush=True)
        else:
            _cache[key] = []
            print(f'[startup] — {key}: file not found', flush=True)
    except Exception as e:
        _cache[key] = []
        print(f'[startup] ✗ {key}: {e}', flush=True)

tiles_ready = os.path.exists(TILES_DIR) and any(f.endswith('.gz') for f in os.listdir(TILES_DIR))
print(f'[startup] Buildings tiles: {"ready ✓" if tiles_ready else "NOT FOUND — run tile_builder.py"}', flush=True)

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/api/layers')
def list_layers():
    result = {}
    for key in LAYER_FILES:
        if key == 'buildings':
            n = len([f for f in os.listdir(TILES_DIR) if f.endswith('.gz')]) if os.path.exists(TILES_DIR) else 0
            result[key] = {'available': n > 0, 'tiles': n}
        else:
            result[key] = {'available': len(_cache.get(key,[])) > 0, 'count': len(_cache.get(key,[]))}
    return jsonify(result)

@app.route('/api/layer/<layer_name>')
def get_layer(layer_name):
    if layer_name not in LAYER_FILES:
        return jsonify({'error':'not found'}), 404
    feats = _cache.get(layer_name, [])
    return _gzip(json.dumps({'type':'FeatureCollection','features':feats},separators=(',',':')))

@app.route('/api/layer/<layer_name>/bbox')
def get_bbox(layer_name):
    try:
        minx=float(request.args['minx']); miny=float(request.args['miny'])
        maxx=float(request.args['maxx']); maxy=float(request.args['maxy'])
    except: return jsonify({'error':'bad bbox'}), 400

    if layer_name == 'buildings':
        cx0=math.floor(minx/TILE_DEG); cy0=math.floor(miny/TILE_DEG)
        cx1=math.floor(maxx/TILE_DEG); cy1=math.floor(maxy/TILE_DEG)
        feats = []
        for cx in range(cx0, cx1+1):
            for cy in range(cy0, cy1+1):
                tp = os.path.join(TILES_DIR, f'{cx}_{cy}.json.gz')
                if not os.path.exists(tp): continue
                try:
                    with gzip.open(tp,'rb') as f:
                        tile = json.loads(f.read())
                    feats.extend(tile.get('features',[]))
                except Exception as e:
                    print(f'[tile] {cx},{cy}: {e}')
        return _gzip(json.dumps({'type':'FeatureCollection','features':feats},separators=(',',':')))

    def in_bb(feat):
        bb = _feat_bbox(feat)
        if not bb: return True
        x0,y0,x1,y1 = bb
        return x1>=minx and x0<=maxx and y1>=miny and y0<=maxy

    feats = [f for f in _cache.get(layer_name,[]) if in_bb(f)]
    return _gzip(json.dumps({'type':'FeatureCollection','features':feats},separators=(',',':')))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
