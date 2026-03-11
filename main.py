#!/usr/bin/env python3
"""
Agmarknet Farmer Insights Tool
================================
Step 1 — Fetches available filters from:
           https://api.agmarknet.gov.in/v1/daily-price-arrival/filters

Step 2 — Calls the report API (only from_date + to_date mandatory).
           Handles pagination automatically — fetches ALL pages.

Step 3 — Generates variety-level insights as JSON.

Usage
-----
  python agmarknet_insights.py --from_date 2026-03-01 --to_date 2026-03-10
  python agmarknet_insights.py --from_date 2026-03-01 --to_date 2026-03-10 --commodity "Tomato" --state "Kerala"
  python agmarknet_insights.py --from_date 2026-03-01 --to_date 2026-03-10 --output insights.json
  python agmarknet_insights.py --list_filters
"""

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from typing import Annotated  
from fastapi import Query

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request


# ──────────────────────────────────────────────────────────────────────────────
# 1.  Constants
# ──────────────────────────────────────────────────────────────────────────────

FILTERS_URL  = "https://api.agmarknet.gov.in/v1/daily-price-arrival/filters"
REPORT_URL   = "https://api.agmarknet.gov.in/v1/daily-price-arrival/report"
ARRAY_PARAMS = {"state", "district", "market", "grade", "variety"}

HEADERS = {
    "User-Agent"     : (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept"         : "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Connection"     : "keep-alive",
    "Referer"        : "https://agmarknet.gov.in/",
    "Origin"         : "https://agmarknet.gov.in",
    "Cache-Control"  : "no-cache",
    "Pragma"         : "no-cache",
}

DEFAULT_PAGE_SIZE = 100  # fetch large pages to minimise round-trips



app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ──────────────────────────────────────────────────────────────────────────────
# 2.  HTTP helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ssl_ctx(verify: bool = True) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def _urllib_get(url: str, verify: bool = True) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx(verify)) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _requests_get(url: str, verify: bool = False) -> dict:
    import requests as rq
    r = rq.get(url, headers=HEADERS, timeout=30, verify=verify)
    r.raise_for_status()
    return r.json()


def fetch_json(url: str, label: str = "API", retries: int = 3) -> dict:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        for pass_num, fn in enumerate([
            lambda: _urllib_get(url, verify=True),
            lambda: _urllib_get(url, verify=False),
        ], start=1):
            try:
                return fn()
            except Exception as e:
                last_err = e
                print(f"  [{label}] attempt {attempt}/pass {pass_num} — {type(e).__name__}: {e}",
                      file=sys.stderr)
        try:
            import requests  # noqa
            try:
                return _requests_get(url, verify=False)
            except Exception as e:
                last_err = e
                print(f"  [{label}] attempt {attempt}/pass 3 (requests) — {e}", file=sys.stderr)
        except ImportError:
            pass

        if attempt < retries:
            wait = 2 ** attempt
            print(f"  ⏳ Waiting {wait}s before retry {attempt + 1}/{retries} …", file=sys.stderr)
            time.sleep(wait)

    print(
        f"\n[ERROR] Could not reach {label} after {retries} attempts.\n"
        f"  Last error : {last_err}\n"
        f"  URL        : {url}\n\n"
        "Tips:\n  • Check internet / VPN.\n  • pip install requests\n",
        file=sys.stderr,
    )
    sys.exit(1)



def extract_records_and_pagination(api_response: Any) -> tuple[list[dict], dict]:
    """
    Returns (records_list, pagination_info).
    Handles the actual Agmarknet API structure:
      { "data": { "records": [ { "data": [...], "pagination": [...] } ] } }
    """
    if not isinstance(api_response, dict):
        return [], {}

    data = api_response.get("data", {})

    # The API sometimes returns data as a list instead of a dict
    if isinstance(data, list):
        # Flat list of records — no pagination info
        raw_records = [r for r in data if isinstance(r, dict)]
        normalised = [_normalise(r) for r in raw_records]
        return normalised, {}

    if not isinstance(data, dict):
        return [], {}

    records_wrapper = data.get("records", [])
    if not records_wrapper or not isinstance(records_wrapper, list):
        return [], {}

    # records_wrapper is a list; each element may be a dict with keys 'data' and 'pagination'
    # OR it could be a flat list of records directly
    first = records_wrapper[0]

    if not isinstance(first, dict):
        return [], {}

    # If the first element has a 'data' key, it's the wrapped structure
    if "data" in first:
        raw_records = first.get("data", [])
        if not isinstance(raw_records, list):
            raw_records = []

        pagination = {}
        pag_list = first.get("pagination", [])
        if pag_list and isinstance(pag_list, list) and isinstance(pag_list[0], dict):
            pagination = pag_list[0]
    else:
        # records_wrapper is a flat list of record dicts
        raw_records = [r for r in records_wrapper if isinstance(r, dict)]
        pagination = {}

    # Normalise each record to a flat, consistent dict
    normalised = [_normalise(r) for r in raw_records if isinstance(r, dict)]
    return normalised, pagination


def _parse_price(val) -> float | None:
    """Parse price strings like '3,600.00' or plain floats."""
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _normalise(r: dict) -> dict:
    """Map raw API field names → standard names used throughout this script."""
    return {
        "date"        : r.get("arrival_date"),
        "state"       : r.get("state_name"),
        "district"    : r.get("district_name"),
        "market"      : r.get("market_name"),
        "commodity"   : r.get("cmdt_name"),
        "commodity_group": r.get("cmdt_grp_name"),
        "variety"     : r.get("variety_name"),
        "grade"       : r.get("grade_name"),
        "min_price"   : _parse_price(r.get("min_price")),
        "max_price"   : _parse_price(r.get("max_price")),
        "modal_price" : _parse_price(r.get("model_price")),   # API uses model_price
        "arrivals"    : _parse_price(r.get("arrival_qty")),
        "price_unit"  : r.get("unit_name_price"),
        "arrival_unit": r.get("unit_name_arrival"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 4.  Paginated fetch — get ALL pages automatically
# ──────────────────────────────────────────────────────────────────────────────

def fetch_all_records(base_url: str, params: dict) -> tuple[list[dict], dict]:
    """
    Fetch page 1, read total_pages from pagination, then fetch remaining pages.
    Returns (all_records, pagination_summary).
    """
    # Page 1
    params_p1 = {**params, "page": 1, "limit": DEFAULT_PAGE_SIZE}
    url_p1    = build_url(REPORT_URL, params_p1)
    print(f"📡  Fetching page 1: {url_p1}", file=sys.stderr)
    resp1           = fetch_json(url_p1, label="report p1")
    records, pag    = extract_records_and_pagination(resp1)

    total_pages  = int(pag.get("total_pages",  1))
    total_count  = int(pag.get("total_count",  len(records)))
    current_page = int(pag.get("current_page", 1))

    print(f"   📄 Page 1/{total_pages}  — {len(records)} records  (total: {total_count})", file=sys.stderr)

    # Remaining pages
    for page in range(2, total_pages + 1):
        params_pn = {**params, "page": page, "limit": DEFAULT_PAGE_SIZE}
        url_pn    = build_url(REPORT_URL, params_pn)
        print(f"   📄 Fetching page {page}/{total_pages} …", file=sys.stderr)
        resp_n        = fetch_json(url_pn, label=f"report p{page}")
        recs_n, _     = extract_records_and_pagination(resp_n)
        records.extend(recs_n)
        time.sleep(0.3)   # be polite to the server

    pagination_summary = {
        "total_count"   : total_count,
        "total_pages"   : total_pages,
        "pages_fetched" : total_pages,
        "records_fetched": len(records),
        "items_per_page": DEFAULT_PAGE_SIZE,
    }
    print(f"✅  {len(records)} total records fetched across {total_pages} page(s).", file=sys.stderr)
    return records, pagination_summary


# ──────────────────────────────────────────────────────────────────────────────
# 5.  Filter fetching & resolution
# ──────────────────────────────────────────────────────────────────────────────




def _find(items: list, name_key: str, id_key: str, query: str):
    q = query.strip().lower()
    for item in items:
        if str(item.get(id_key, "")).lower() == q:    return item
        if str(item.get(name_key, "")).lower() == q:  return item
    for item in items:
        if q in str(item.get(name_key, "")).lower():  return item
    return None


def resolve_filters(filters: dict, **kwargs) -> dict:
    resolved: dict[str, Any] = {}

    commodity = kwargs.get("commodity")
    if commodity:
        item = _find(filters["commodities"], "cmdt_name", "cmdt_id", commodity)
        if item:
            resolved["commodity"] = item["cmdt_id"]
            resolved["group"]     = item.get("cmdt_group_id")
            print(f"   📦 Commodity : {item['cmdt_name']}  (id={item['cmdt_id']}, group={item.get('cmdt_group_id')})", file=sys.stderr)
        else:
            print(f"   [WARN] Commodity '{commodity}' not found.", file=sys.stderr)

    state = kwargs.get("state")
    if state:
        item = _find(filters["states"], "state_name", "state_id", state)
        if item:
            resolved["state"] = json.dumps([item["state_id"]])
            print(f"   🗺  State     : {item['state_name']}  (id={item['state_id']})", file=sys.stderr)
        else:
            print(f"   [WARN] State '{state}' not found.", file=sys.stderr)

    market = kwargs.get("market")
    if market:
        item = _find(filters["markets"], "mkt_name", "id", market)
        if item:
            resolved["market"] = json.dumps([item["id"]])
            print(f"   🏪 Market    : {item['mkt_name']}  (id={item['id']})", file=sys.stderr)

    district = kwargs.get("district")
    if district:
        item = _find(filters["districts"], "district_name", "district_id", district)
        if item:
            resolved["district"] = json.dumps([item["district_id"]])
            print(f"   🏘  District  : {item.get('district_name')}  (id={item['district_id']})", file=sys.stderr)

    grade = kwargs.get("grade")
    if grade:
        item = _find(filters["grades"], "grade_name", "grade_id", grade)
        if item:
            resolved["grade"] = json.dumps([item["grade_id"]])
            print(f"   🏷  Grade     : {item.get('grade_name')}  (id={item['grade_id']})", file=sys.stderr)

    variety = kwargs.get("variety")
    if variety:
        item = _find(filters["varieties"], "variety_name", "variety_id", variety)
        if item:
            resolved["variety"] = json.dumps([item["variety_id"]])
            print(f"   🌱 Variety   : {item.get('variety_name')}  (id={item['variety_id']})", file=sys.stderr)

    data_type = kwargs.get("data_type")
    if data_type:
        resolved["data_type"] = data_type

    return resolved


# ──────────────────────────────────────────────────────────────────────────────
# 6.  URL builder
# ──────────────────────────────────────────────────────────────────────────────

def build_url(base: str, params: dict) -> str:
    parts = []
    for key, value in params.items():
        if value is None:
            continue
        if key in ARRAY_PARAMS:
            if str(value) in ("[]", "", "null"):
                continue
        parts.append(f"{key}={urllib.parse.quote(str(value), safe='')}")
    return base + ("?" + "&".join(parts) if parts else "")


# ──────────────────────────────────────────────────────────────────────────────
# 7.  Statistical helpers
# ──────────────────────────────────────────────────────────────────────────────

def _median(values: list) -> float | None:
    if not values: return None
    s = sorted(values); n = len(s); mid = n // 2
    return round((s[mid-1] + s[mid]) / 2 if n % 2 == 0 else s[mid], 2)


def _volatility(values: list) -> float | None:
    if len(values) < 2: return None
    mean = sum(values) / len(values)
    if mean == 0: return None
    return round((sum((v-mean)**2 for v in values)/len(values))**0.5 / mean * 100, 2)


def _col(key: str, src: list) -> list:
    return [v for v in (r.get(key) for r in src) if v is not None]


def _recommend(modal: list, arrivals: list, spread) -> tuple:
    if not modal:
        return "Insufficient data", "No price data available."
    avg   = sum(modal) / len(modal)
    vol   = _volatility(modal)
    total = sum(arrivals) if arrivals else 0
    if vol is not None and vol > 20:
        return "WAIT or SPREAD ACROSS MARKETS", f"High price volatility ({vol}%). Sell in smaller batches to reduce risk."
    if spread is not None and spread > 500:
        return "CHOOSE MARKET CAREFULLY", f"Price spread ₹{spread}/quintal detected. Transport to highest-priced market for better returns."
    if total > 5000:
        return "SELL PROMPTLY", f"High arrivals ({total} MT) signal oversupply. Prices may fall — sell soon."
    if avg < 500:
        return "CONSIDER STORAGE", f"Avg modal ₹{round(avg,2)}/quintal is low. Storage may yield better returns."
    return "GOOD TIME TO SELL", f"Stable prices — avg modal ₹{round(avg,2)}/quintal. No extreme oversupply."


# ──────────────────────────────────────────────────────────────────────────────
# 8.  Insight generation
# ──────────────────────────────────────────────────────────────────────────────

def generate_insights(records: list, pagination: dict) -> dict:
    if not records:
        return {"error": "No records to analyse."}

    # Group by market (primary insight axis) and by variety
    by_variety: dict = defaultdict(list)
    by_market:  dict = defaultdict(list)
    by_date:    dict = defaultdict(list)

    for r in records:
        variety = r.get("variety") or "Unknown"
        market  = r.get("market")  or "Unknown"
        dt      = r.get("date")    or "Unknown"
        by_variety[variety].append(r)
        by_market[market].append(r)
        by_date[dt].append(r)

    all_modal   = _col("modal_price", records)
    all_min     = _col("min_price",   records)
    all_max     = _col("max_price",   records)
    all_arrival = _col("arrivals",    records)

    # ── overall summary ──────────────────────────────────────────────
    overall = {
        "total_records"      : len(records),
        "distinct_varieties" : len(by_variety),
        "distinct_markets"   : len(by_market),
        "distinct_dates"     : len(by_date),
        "distinct_states"    : len({r.get("state") for r in records} - {None}),
        "commodity"          : records[0].get("commodity") if records else None,
        "commodity_group"    : records[0].get("commodity_group") if records else None,
        "date_range"         : {
            "from": min((r.get("date","") for r in records), default=""),
            "to"  : max((r.get("date","") for r in records), default=""),
        },
        "price_summary_inr_per_quintal": {
            "min_recorded": round(min(all_min),  2) if all_min  else None,
            "max_recorded": round(max(all_max),  2) if all_max  else None,
            "avg_modal"   : round(sum(all_modal)/len(all_modal), 2) if all_modal else None,
            "median_modal": _median(all_modal),
        },
        "arrival_summary": {
            "total_metric_tonnes"  : round(sum(all_arrival), 2) if all_arrival else None,
            "average_per_record"   : round(sum(all_arrival)/len(all_arrival), 2) if all_arrival else None,
        },
    }

    # ── per-variety insights ─────────────────────────────────────────
    variety_insights: dict = {}
    for variety, recs in by_variety.items():
        modal  = _col("modal_price", recs)
        mins   = _col("min_price",   recs)
        maxs   = _col("max_price",   recs)
        arrvls = _col("arrivals",    recs)
        spread = round(max(maxs) - min(mins), 2) if maxs and mins else None
        best   = max(recs, key=lambda r: r.get("modal_price") or 0,            default={})
        worst  = min(recs, key=lambda r: r.get("modal_price") or float("inf"), default={})
        rec, reason = _recommend(modal, arrvls, spread)

        variety_insights[variety] = {
            "records_count"   : len(recs),
            "markets_available": sorted({r.get("market") for r in recs} - {None}),
            "states_available" : sorted({r.get("state")  for r in recs} - {None}),
            "price_inr_per_quintal": {
                "min_price_recorded"  : round(min(mins), 2) if mins else None,
                "max_price_recorded"  : round(max(maxs), 2) if maxs else None,
                "avg_modal_price"     : round(sum(modal)/len(modal), 2) if modal else None,
                "median_modal_price"  : _median(modal),
                "price_spread"        : spread,
                "price_volatility_pct": _volatility(modal),
            },
            "arrivals_metric_tonnes": {
                "total"            : round(sum(arrvls), 2) if arrvls else None,
                "avg_per_record"   : round(sum(arrvls)/len(arrvls), 2) if arrvls else None,
                "max_single_record": round(max(arrvls), 2) if arrvls else None,
            },
            "best_market_to_sell" : {"market": best.get("market"), "state": best.get("state"), "modal_price": best.get("modal_price"), "date": best.get("date")},
            "lowest_price_market" : {"market": worst.get("market"), "state": worst.get("state"), "modal_price": worst.get("modal_price"), "date": worst.get("date")},
            "farmer_recommendation": rec,
            "recommendation_reason": reason,
        }

    # ── per-market summary ───────────────────────────────────────────
    market_summary = {}
    for market, recs in by_market.items():
        modal  = _col("modal_price", recs)
        arrvls = _col("arrivals",    recs)
        market_summary[market] = {
            "records_count"    : len(recs),
            "state"            : recs[0].get("state") if recs else None,
            "district"         : recs[0].get("district") if recs else None,
            "avg_modal_price"  : round(sum(modal)/len(modal), 2) if modal else None,
            "max_modal_price"  : round(max(modal), 2) if modal else None,
            "min_modal_price"  : round(min(modal), 2) if modal else None,
            "total_arrivals_mt": round(sum(arrvls), 2) if arrvls else None,
            "dates_active"     : sorted({r.get("date") for r in recs} - {None}),
        }

    # ── daily price trend ────────────────────────────────────────────
    daily_trend = {}
    for dt, recs in sorted(by_date.items()):
        modal = _col("modal_price", recs)
        arrvls = _col("arrivals",   recs)
        daily_trend[dt] = {
            "avg_modal_price"  : round(sum(modal)/len(modal), 2) if modal else None,
            "total_arrivals_mt": round(sum(arrvls), 2) if arrvls else None,
            "records"          : len(recs),
        }

    # ── top markets by arrival ───────────────────────────────────────
    top_markets_by_arrival = sorted(
        [{"market": m, "state": d["state"], "total_arrivals_mt": d["total_arrivals_mt"] or 0,
          "avg_modal_price": d["avg_modal_price"]}
         for m, d in market_summary.items()],
        key=lambda x: x["total_arrivals_mt"], reverse=True
    )[:10]

    top_markets_by_price = sorted(
        [{"market": m, "state": d["state"], "avg_modal_price": d["avg_modal_price"] or 0,
          "total_arrivals_mt": d["total_arrivals_mt"]}
         for m, d in market_summary.items()],
        key=lambda x: x["avg_modal_price"], reverse=True
    )[:10]

    # ── ranked varieties by price ────────────────────────────────────
    top_varieties = sorted(
        [{"variety": v, "avg_modal_price_inr": (d["price_inr_per_quintal"]["avg_modal_price"] or 0)}
         for v, d in variety_insights.items()],
        key=lambda x: x["avg_modal_price_inr"], reverse=True
    )

    return {
        "generated_at"             : datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pagination_info"          : pagination,
        "overall_summary"          : overall,
        "daily_price_trend"        : daily_trend,
        "top_markets_by_arrival"   : top_markets_by_arrival,
        "top_markets_by_price"     : top_markets_by_price,
        "variety_rankings"         : top_varieties,
        "variety_insights"         : variety_insights,
        "market_summary"           : market_summary,
        "all_records"              : records,
    }


def fetch_filters() -> dict:
    # print("📋  Fetching available filters …", file=sys.stderr)
    raw  = fetch_json(FILTERS_URL, label="filters")
    data = raw.get("data", {})
    filters = {
        "commodities": data.get("cmdt_data",     []),
        "states"     : data.get("state_data",    []),
        "markets"    : data.get("market_data",   []),
        "districts"  : data.get("district_data", []),
        "grades"     : data.get("grade_data",    []),
        "varieties"  : data.get("variety_data",  []),
        "groups"     : data.get("group_data",    []),
        "data_types" : data.get("data_type",     []),
    }
    counts = {k: len(v) for k, v in filters.items() if v}
    print(f"   ✅ Loaded: {counts}")
    return filters

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    filters = fetch_filters()
    return templates.TemplateResponse("UI.html", {"request": request, "filters" : filters})


@app.get("/get-agmarknet-data", response_class=HTMLResponse)
def get_agmarknet_data(
    request: Request, 
    from_date: date, 
    to_date: date, 
    commodity: str = "", 
    state: str = "", 
    district: str = "", 
    market:str = "", 
    grade:str = "", 
    variety:str = "", 
    data_type:str = "", 
    max_pages:int = 1
):
    # Step 1: filters
    filters = fetch_filters()

    # Step 2: resolve filters
    print("\n🔎  Resolving filters …", file=sys.stderr)
    filter_params = resolve_filters(
        filters, 
        commodity=commodity, 
        state=state, 
        district=district, 
        market=market, 
        grade=grade, 
        variety=variety, 
        data_type=data_type
    )

    # Step 3: build base params (only dates mandatory)
    params: dict[str, Any] = {"from_date": from_date, "to_date": to_date}
    params.update(filter_params)

    print(f"\n🌾  Agmarknet Insights Tool", file=sys.stderr)

    # Step 4: fetch all pages
    global DEFAULT_PAGE_SIZE
    all_records, pagination = fetch_all_records(REPORT_URL, params)

    if not all_records:
        return templates.TemplateResponse("UI.html", {
            "request": request, 
            "filters": filters, 
            "error": "No records found for the selected filters and dates."
        })

    # Step 5: generate insights
    insights   = generate_insights(all_records, pagination)
    output_str = json.dumps(insights, indent=2, ensure_ascii=False)

    return templates.TemplateResponse("UI.html", {"request": request, "results" : output_str, "filters": filters})


