import json, os, gzip, math, urllib.request

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE_DIR, 'data')
TILES_DIR = os.path.join(BASE_DIR, 'tiles')
TILE_DEG  = 0.02

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

def main():
    os.makedirs(TILES_DIR, exist_ok=True)
    existing = [f for f in os.listdir(TILES_DIR) if f.endswith('.json.gz')]
    if existing:
        print(f'[tile_builder] Already tiled ({len(existing)} tiles). Skipping.')
        return

    local_path = os.path.join(DATA_DIR, 'Buildings.geojson')
    remote_url = os.environ.get('BUILDINGS_URL', '')

    if os.path.exists(local_path):
        print('[tile_builder] Loading Buildings.geojson from local data/ ...')
        with open(local_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    elif remote_url:
        print(f'[tile_builder] Downloading Buildings.geojson from {remote_url} ...')
        req = urllib.request.Request(remote_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode('utf-8'))
    else:
        print('[tile_builder] No buildings source found. Skipping.')
        return

    features = data.get('features', [])
    print(f'[tile_builder] Tiling {len(features)} features...')

    grid = {}
    for i, feat in enumerate(features):
        if i % 10000 == 0:
            print(f'[tile_builder]   {i}/{len(features)}...', flush=True)
        geom = feat.get('geometry', {})
        if geom and geom.get('coordinates'):
            geom['coordinates'] = _simplify(geom['coordinates'])
        bb = _feat_bbox(feat)
        if not bb: continue
        minx,miny,maxx,maxy = bb
        for cx in range(math.floor(minx/TILE_DEG), math.floor(maxx/TILE_DEG)+1):
            for cy in range(math.floor(miny/TILE_DEG), math.floor(maxy/TILE_DEG)+1):
                k = f'{cx}_{cy}'
                if k not in grid: grid[k] = {'cx':cx,'cy':cy,'feats':[]}
                grid[k]['feats'].append(feat)

    for k, tile in grid.items():
        out = json.dumps({'type':'FeatureCollection','features':tile['feats']},separators=(',',':')).encode()
        with gzip.open(os.path.join(TILES_DIR,f"{tile['cx']}_{tile['cy']}.json.gz"),'wb',compresslevel=6) as f:
            f.write(out)

    print(f'[tile_builder] Done: {len(grid)} tiles written to tiles/')

if __name__ == '__main__':
    main()
