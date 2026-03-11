# Warri Region GIS Platform
### Powered by Popson Geospatial Services

---

## Local Setup

1. Place GeoJSON files in `data/` folder
2. `pip install flask flask-cors`
3. `python app.py` → open http://localhost:5000

---

## Deploying to Render (with 200MB+ Buildings file)

### Step 1 — Handle the large Buildings.geojson via GitHub Releases

GitHub blocks files >100MB in regular commits.
Use a **GitHub Release** instead (allows up to 2GB):

1. Push everything **except** Buildings.geojson to GitHub:
   ```
   # Add this to .gitignore:
   data/Buildings.geojson
   ```
2. On GitHub → your repo → **Releases → Create a new release**
3. Tag it `v1.0`, drag `Buildings.geojson` into the asset upload box
4. Publish the release
5. Copy the direct download URL — it looks like:
   ```
   https://github.com/YOUR_USERNAME/warri-gis/releases/download/v1.0/Buildings.geojson
   ```

### Step 2 — Set environment variable on Render

In your Render service dashboard → **Environment** tab:
```
Key:   BUILDINGS_URL
Value: https://github.com/YOUR_USERNAME/warri-gis/releases/download/v1.0/Buildings.geojson
```

### Step 3 — Deploy

Render auto-deploys when you push to GitHub.
The app will:
- Serve all layers from local `data/` folder
- Fetch Buildings.geojson from the GitHub Release URL on first request
- Cache it in memory for subsequent viewport requests

---

## File Structure
```
warri_gis/
├── app.py              ← Flask backend
├── index.html          ← Frontend (Leaflet map)
├── requirements.txt
├── render.yaml         ← Render deploy config
├── .gitignore
└── data/
    ├── Road.geojson
    ├── Warri Region.geojson
    ├── Forest.geojson
    ├── Vegetation.geojson
    ├── Wetlands.geojson
    ├── Water Bodies.geojson
    ├── Barerock.geojson
    └── Buildings.geojson  ← Upload to GitHub Releases instead
```

---

## Environment Variables (Render)
| Variable | Purpose |
|---|---|
| `BUILDINGS_URL` | Public URL to Buildings.geojson (GitHub Release or Supabase) |
| `PORT` | Auto-set by Render — do not override |

"# warri-gis" 
