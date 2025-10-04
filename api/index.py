from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import calendar
import os
import json
import base64
import ee


# Decode Base64 JSON from environment variable
key_b64 = os.environ['GOOGLE_APPLICATION_CREDENTIALS']

key_str = base64.b64decode(key_b64).decode('utf-8')
key_json = json.loads(base64.b64decode(key_b64))

# Create ServiceAccountCredentials directly from JSON dict
credentials = ee.ServiceAccountCredentials(
    key_json['client_email'],
    None,
    key_str
)

# Initialize Earth Engine
ee.Initialize(credentials=credentials, project="secret-proton-309304")

# Initialize Earth Engine
#ee.Initialize(project="secret-proton-309304")

app = Flask(__name__)

def get_last4months_with_data(aoi):
    """
    Returns NDVI, EVI, NDWI for the last 4 months with available data.
    Includes both month number and month name.
    """
    results = []
    today = datetime(2023,1,1)
    
    months_back = 0
    months_checked = 0
    max_months_check = 24  # prevent infinite loop

    while len(results) < 6 and months_checked < max_months_check:
        month_date = today - timedelta(days=months_back*30)  # approx month
        year = month_date.year
        month = month_date.month
        month_name = calendar.month_name[month]

        start_date = f"{year}-{month:02d}-01"
        if month < 12:
            end_date = f"{year}-{month+1:02d}-01"
        else:
            end_date = f"{year+1}-01-01"

        # MODIS monthly NDVI/EVI (MOD13A1)
        modis = ee.ImageCollection('MODIS/006/MOD13A1') \
                    .filterDate(start_date, end_date) \
                    .select(['NDVI', 'EVI'])
        
        if modis.size().getInfo() > 0:
            image_mean = modis.mean()
            sample = image_mean.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=aoi,
                scale=500,
                bestEffort=True
            )
            ndvi = sample.get('NDVI').getInfo() / 10000 if sample.get('NDVI') else None
            evi = sample.get('EVI').getInfo() / 10000 if sample.get('EVI') else None

            # MODIS SR for NDWI
            modis_sr = ee.ImageCollection('MODIS/006/MOD09A1') \
                            .filterDate(start_date, end_date) \
                            .select(['sur_refl_b02', 'sur_refl_b06'])
            if modis_sr.size().getInfo() > 0:
                image_sr_mean = modis_sr.mean()
                ndwi = image_sr_mean.expression(
                    '(NIR - SWIR) / (NIR + SWIR)',
                    {
                        'NIR': image_sr_mean.select('sur_refl_b02'),
                        'SWIR': image_sr_mean.select('sur_refl_b06')
                    }
                ).rename('NDWI')
                sample_ndwi = ndwi.reduceRegion(
                    ee.Reducer.mean(),
                    geometry=aoi,
                    scale=500,
                    bestEffort=True
                )
                ndwi_val = sample_ndwi.get('NDWI').getInfo() if sample_ndwi.get('NDWI') else None
            else:
                ndwi_val = None

            # Vegetation category
            if ndvi is not None:
                if ndvi > 0.5:
                    veg_category = "Dense Vegetation / High Bloom"
                elif ndvi > 0.2:
                    veg_category = "Moderate Vegetation / Possible Bloom"
                else:
                    veg_category = "Sparse Vegetation / No Bloom"
            else:
                veg_category = "No Data"

            results.append({
                "year": year,
                "month": month,
                "month_name": month_name,
                "NDVI": ndvi,
                "EVI": evi,
                "NDWI": ndwi_val,
                "VegetationCategory": veg_category
            })

        months_back += 1
        months_checked += 1

    return results


@app.route('/get_last4months', methods=['POST'])
def get_last4months():
    """
    Accepts JSON:
    {
        "type": "Point" or "Polygon",
        "coordinates": [[lng, lat], ...]
    }
    Returns last 4 months with available NDVI/EVI/NDWI values, including month names.
    """
    try:
        data = request.get_json()
        geom_type = data.get('type', 'Point')
        coords = data.get('coordinates', [])

        if not coords:
            return jsonify({"error": "No coordinates provided"}), 400

        if geom_type == 'Point':
            aoi = ee.Geometry.Point(coords[0])
        elif geom_type == 'Polygon':
            aoi = ee.Geometry.Polygon(coords)
        else:
            return jsonify({"error": "Invalid geometry type"}), 400

        results = get_last4months_with_data(aoi)
        if not results:
            return jsonify({"error": "No data available for the last checked months"}), 404

        return jsonify(results)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
