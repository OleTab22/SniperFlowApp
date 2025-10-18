# ML Training Guide - Using Render for Auto Data Collection

Your Render deployment will **automatically collect training data** in the background. Here's how:

## 🚀 How It Works

1. **Automatic Collection**: Your Render backend now runs `ml_collector.py` in the background
   - Collects features from `/home` every 5 minutes
   - Stores to PostgreSQL (`ml_features` table)
   - Runs 24/7 as long as your backend is deployed

2. **On-Demand Training**: When you have enough data, train locally or via Render shell

3. **Deploy Model**: Commit the trained model to git → Render auto-deploys → clients get ML

---

## 📊 Step 1: Let It Collect Data

**Your Render deployment is already collecting!** Just deploy this update:

```bash
cd SniperFlow
git add backend/ml_collector.py backend/main.py backend/ml_engine.py models/
git commit -m "Add ML data collection and inference"
git push
```

Render will:
- Auto-deploy within ~2-3 minutes
- Run the collector in background
- Store ~288 samples/day (every 5 min)

**Minimum data needed**: 200-300 samples (~1-2 days)
**Recommended**: 1000+ samples (~3-4 days) for better accuracy

---

## 📈 Step 2: Check Collection Status

Via Render dashboard → Shell:

```bash
# Count collected samples
psql $DATABASE_URL -c "SELECT COUNT(*) FROM ml_features"

# See last 10 samples
psql $DATABASE_URL -c "SELECT ts, price_now, dxy_z, real_z FROM ml_features ORDER BY ts DESC LIMIT 10"
```

Or locally (if you have DATABASE_URL env var):

```bash
python -c "import os, psycopg2; conn=psycopg2.connect(os.getenv('DATABASE_URL')); cur=conn.cursor(); cur.execute('SELECT COUNT(*) FROM ml_features'); print(f'{cur.fetchone()[0]} samples collected')"
```

---

## 🎯 Step 3: Train the Model

### Option A: Train Locally (Recommended)

```bash
cd SniperFlow

# Set your Render database URL
export DATABASE_URL="postgresql://user:pass@host/db"  # get from Render dashboard

# Train
pip install psycopg2-binary pandas numpy lightgbm onnxmltools skl2onnx scikit-learn
python backend/train_from_db.py

# This creates:
#   models/xau_nowcast_lgb.onnx
#   models/feature_order.json
```

### Option B: Train on Render (via Shell)

In Render dashboard → your service → Shell:

```bash
python backend/train_from_db.py 12  # 12 = 1 hour prediction horizon
```

---

## 🔄 Step 4: Deploy the Model

After training locally:

```bash
cd SniperFlow
git add models/xau_nowcast_lgb.onnx models/feature_order.json
git commit -m "Add trained ML model"
git push
```

Render auto-deploys → backend detects model → starts serving ML predictions!

---

## ✅ Verify ML is Active

```bash
# Check model availability
curl https://your-app.onrender.com/home | jq '.metrics.nowcast.model_id'
# Should show: "ml-onnx-001" (not "stub-000")

# Check ML probability
curl https://your-app.onrender.com/v1/nowcast | jq '.prob_up'
# Should show: 0.XXX
```

Your Android app will automatically start using ML predictions!

---

## 🔧 Advanced: Platt Calibration

After training the base model, calibrate probabilities:

```bash
# Create train_platt_from_db.py (similar to train_from_db.py but runs LogisticRegression on raw outputs)
# Or use the existing train_platt.py if you export DB to CSV first

export DATABASE_URL="..."
python train_platt_from_db.py

# Creates models/platt.json with {"w": ..., "b": ...}
git add models/platt.json
git commit -m "Add Platt calibration"
git push
```

---

## 📁 File Structure After Training

```
SniperFlow/
├── backend/
│   ├── ml_collector.py       # ✅ Auto-collects data to DB
│   ├── ml_engine.py          # ✅ ONNX inference
│   ├── train_from_db.py      # ✅ Training script
│   └── main.py               # ✅ Starts collector on startup
├── models/
│   ├── xau_nowcast_lgb.onnx  # 🎯 Your trained model
│   ├── feature_order.json    # 📋 Feature names/order
│   └── platt.json            # 🎲 Optional calibration
```

---

## 🎛️ Configuration (Optional)

Environment variables (set in Render dashboard):

```bash
ML_COLLECTION_INTERVAL=300    # seconds between samples (default 300 = 5min)
MODEL_DIR=models              # where to find model files
MODEL_FILE=xau_nowcast_lgb.onnx
FEATURE_ORDER_FILE=feature_order.json
PLATT_FILE=platt.json
```

---

## 📊 Monitoring Collection

Query recent samples:

```sql
-- Last 24h of data
SELECT ts, price_now, dxy_z, real_z, vix_z, mom
FROM ml_features
WHERE ts > now() - interval '24 hours'
ORDER BY ts DESC
LIMIT 20;

-- Data quality check
SELECT 
    DATE(ts) as date,
    COUNT(*) as samples,
    AVG(price_now) as avg_price
FROM ml_features
GROUP BY DATE(ts)
ORDER BY date DESC;
```

---

## 🔁 Continuous Improvement Workflow

1. **Week 1**: Deploy → collector runs → 1000+ samples
2. **Train**: `python backend/train_from_db.py`
3. **Deploy model**: `git add models/ && git commit && git push`
4. **Week 2**: Collector keeps running (now with 2000+ samples)
5. **Retrain**: Better model with more data
6. **Repeat**: Monthly retraining recommended

---

## 🐛 Troubleshooting

**No samples collected?**
- Check Render logs for ml_collector errors
- Verify DATABASE_URL is set
- Check `ml_features` table exists: `\dt ml_features` in psql

**Training fails?**
- Ensure you have ≥200 samples
- Check for NULL values: `SELECT * FROM ml_features WHERE price_now IS NULL`
- Verify feature columns exist

**Model not loading?**
- Check `models/` directory exists and contains `.onnx` file
- Verify `backend/requirements.txt` includes `onnxruntime`
- Check Render build logs for ONNX install errors

**Want faster collection?**
- Set `ML_COLLECTION_INTERVAL=60` for 1-minute samples (uses more DB space)

---

## 💰 Render Free Tier Limits

- **Database**: 256 MB (enough for ~50,000+ samples)
- **Compute**: Spins down after 15min idle (collector pauses but resumes on next request)
- **Tip**: Keep backend warm with your Android app's auto-refresh

Storage estimate:
- ~100 bytes/sample
- 288 samples/day (5min interval)
- ~28KB/day, ~850KB/month

You have plenty of room! 🎉

