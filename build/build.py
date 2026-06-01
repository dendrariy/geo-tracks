#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка данных для geo-tracks.

Читает все .gpx из ../tracks, делает:
  - парсинг трека (имя, точки, ссылка-источник),
  - отрезание автора из имени файла,
  - нормализацию имени в формат  location_name__length__index,
  - расчёт длины (км) по гаверсинусу,
  - упрощение геометрии (Douglas–Peucker),
  - автоматический цвет (по автору),
  - (опционально) набор высоты через Open-Meteo (--elevation),
  - применение overrides.csv (ручные исправления имён).

Результат пишется в ../site/data:
  - index.json     — лёгкий индекс для поиска/фильтра/списка
  - geometry.json  — геометрия треков {id: [[lat,lon],...]}
  - meta.json      — сводка (кол-во, авторы, экстент)
"""

import os, re, csv, json, math, glob, hashlib, argparse, sys, time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRACKS_DIR = os.path.join(ROOT, "tracks")
OUT_DIR = os.path.join(ROOT, "site", "data")
OVERRIDES = os.path.join(HERE, "overrides.csv")

# Последний токен имени файла -> (суффикс_для_отрезания, отображаемый_автор)
AUTHORS = {
    "telogrejka93":    ("telogrejka93", "telogrejka93"),
    "Travel":          ("Georgia_Travel", "Georgia Travel"),
    "AdjaraHiking":    ("AdjaraHiking", "AdjaraHiking"),
    "Novikov":         ("Ratmir_Novikov", "Ratmir Novikov"),
    "lavrenti":        ("lavrenti", "lavrenti"),
    "DVALITY":         ("DVALITY", "DVALITY"),
    "Tramp":           ("Brandy_the_Tramp", "Brandy the Tramp"),
    "Evgenia":         ("Evgenia", "Evgenia"),
    "Tamu-se":         ("Tamu-se", "Tamu-se"),
    "Liubov":          ("Ilia_Liubov", "Ilia Liubov"),
    "GeorgianTour.com":("GeorgianTour.com", "GeorgianTour.com"),
    "alxmamaev":       ("alxmamaev", "alxmamaev"),
    "Trekking":        ("Caucasus_Trekking", "Caucasus Trekking"),
    "GEO":             ("Dmitriy_GEO", "Dmitriy GEO"),
    "Alexandr":        ("Alexandr", "Alexandr"),
    "amiddio":         ("amiddio", "amiddio"),
    "gusTavs":         ("shavi_gusTavs", "shavi gusTavs"),
    "Makharadze":      ("Grigol_Makharadze", "Grigol Makharadze"),
    "challenge":       ("Travel_to_challenge", "Travel to challenge"),
    "mr.crowley":      ("mr.crowley", "mr.crowley"),
    "rokozarenko":     ("rokozarenko", "rokozarenko"),
    "DMO":             ("Samtskhe-Javakheti_DMO", "Samtskhe-Javakheti DMO"),
    "olfedos":         ("olfedos", "olfedos"),
    "Kelenjeridze":    ("Zviad_Kelenjeridze", "Zviad Kelenjeridze"),
    "Shalva91":        ("Shalva91", "Shalva91"),
    "Bakhmaro":        ("Ingo_Schlutius,_PIONEERS_of_Bakhmaro", "PIONEERS of Bakhmaro"),
}

NS = {"g": "http://www.topografix.com/GPX/1/1"}

# ---------- геометрия ----------

def haversine(a, b):
    R = 6371000.0
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2*R*math.asin(min(1.0, math.sqrt(h)))

def track_length_m(pts):
    return sum(haversine(pts[i], pts[i+1]) for i in range(len(pts)-1)) if len(pts) > 1 else 0.0

def rdp(points, eps):
    """Итеративный Douglas–Peucker (без рекурсии)."""
    if len(points) < 3:
        return points[:]
    keep = [False]*len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points)-1)]
    while stack:
        s, e = stack.pop()
        dmax, idx = 0.0, -1
        ax, ay = points[s]; bx, by = points[e]
        dx, dy = bx-ax, by-ay
        norm = math.hypot(dx, dy) or 1e-12
        for i in range(s+1, e):
            px, py = points[i]
            d = abs((px-ax)*dy - (py-ay)*dx)/norm
            if d > dmax:
                dmax, idx = d, i
        if dmax > eps and idx != -1:
            keep[idx] = True
            stack.append((s, idx)); stack.append((idx, e))
    return [p for p, k in zip(points, keep) if k]

# ---------- имена ----------

def strip_author(name):
    last = name.rsplit("_", 1)[-1]
    info = AUTHORS.get(last)
    if not info:
        return name, "—"
    suffix, display = info
    if name.endswith("_" + suffix):
        return name[: -(len(suffix)+1)], display
    return name, display

PAREN_RE = re.compile(r"\(([^()]+)\)")
META_TOKEN_RE = re.compile(
    r"(?<![\w])("
    r"\d+(?:[.,]\d+)?\s*km|"            # 20km / 20 km
    r"\d+\s*m|"                          # 670m
    r"\d+(?:[.,]\d+)?\s*h|"             # 3.5h
    r"day[\s_-]?\d+|part[\s_-]?\d+|"    # day 1 / part 2
    r"д\.?\s*\d+|часть\s*\d+"
    r")(?![\w])",
    re.IGNORECASE,
)

def clean_location(raw):
    s = raw
    paren = PAREN_RE.findall(s)
    if paren:                       # маршрут часто стоит в скобках (AdjaraHiking)
        s = paren[-1]
    s = s.replace("_", " ").replace("-", " ")
    s = META_TOKEN_RE.sub(" ", s)
    s = re.sub(r"[(){}\[\]]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,.-")
    s = re.sub(r"^(?:\d{1,2}\s+){1,3}", "", s)   # убрать ведущие номера маршрута: "1 2 ..."
    s = s.strip(" ,.-")
    if not s:
        s = re.sub(r"[_-]+", " ", raw).strip()
    # аккуратный Title Case, не ломая токены вида "GEO"
    words = []
    for w in s.split():
        words.append(w if (w.isupper() and len(w) > 1) else (w[:1].upper() + w[1:]))
    return " ".join(words) or "Без названия"

def fmt_len(km):
    if km >= 10:
        return f"{round(km)}km"
    return (f"{km:.1f}".rstrip("0").rstrip(".")) + "km"

# ---------- цвет (по автору) ----------

PALETTE = [
    "#c2410c","#1d4ed8","#15803d","#b91c1c","#7c3aed","#0e7490","#a16207",
    "#be185d","#4d7c0f","#0f766e","#9333ea","#ca8a04","#dc2626","#2563eb",
    "#059669","#db2777","#65a30d","#0891b2","#7e22ce","#ea580c","#4338ca",
    "#16a34a","#e11d48","#0284c7","#854d0e","#6d28d9",
]

def color_for(author, cache={}):
    if author not in cache:
        h = int(hashlib.md5(author.encode("utf-8")).hexdigest(), 16)
        cache[author] = PALETTE[h % len(PALETTE)]
    return cache[author]

# ---------- источник ----------

def source_domain(url):
    if not url:
        return "—"
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else "—"

# ---------- overrides ----------

def load_overrides():
    ov = {}
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("file"):
                    ov[row["file"].strip()] = (row.get("location") or "").strip()
    return ov

# ---------- elevation (опционально) ----------

def fetch_gain(points, session):
    """Набор высоты через Open-Meteo (бесплатно, без ключа, до 100 точек/запрос)."""
    if len(points) < 2:
        return None
    step = max(1, len(points)//60)            # ~60 проб на трек
    sample = points[::step]
    if sample[-1] != points[-1]:
        sample.append(points[-1])
    elevs = []
    for i in range(0, len(sample), 100):
        chunk = sample[i:i+100]
        lat = ",".join(f"{p[0]:.5f}" for p in chunk)
        lon = ",".join(f"{p[1]:.5f}" for p in chunk)
        url = f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}"
        for attempt in range(4):
            try:
                r = session.get(url, timeout=30)
                if r.status_code == 200:
                    elevs += r.json().get("elevation", [])
                    break
                time.sleep(1.5*(attempt+1))
            except Exception:
                time.sleep(1.5*(attempt+1))
        else:
            return None
    gain = sum(max(0.0, elevs[i+1]-elevs[i]) for i in range(len(elevs)-1)) if len(elevs) > 1 else 0.0
    return round(gain)

# ---------- основной проход ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eps", type=float, default=7e-5, help="допуск упрощения, градусы (~7м)")
    ap.add_argument("--elevation", action="store_true", help="дотянуть набор высоты (медленно)")
    args = ap.parse_args()

    overrides = load_overrides()
    session = None
    if args.elevation:
        import requests
        session = requests.Session()

    files = sorted(glob.glob(os.path.join(TRACKS_DIR, "*.gpx")))
    if not files:
        print(f"Нет .gpx в {TRACKS_DIR}", file=sys.stderr); sys.exit(1)

    parsed = []
    for path in files:
        fname = os.path.basename(path)
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as e:
            print(f"  пропуск {fname}: {e}", file=sys.stderr); continue

        pts = [(float(p.get("lat")), float(p.get("lon")))
               for p in root.iterfind(".//g:trkpt", NS)]
        if len(pts) < 2:
            print(f"  пропуск {fname}: <2 точек", file=sys.stderr); continue

        link = root.find(".//g:metadata/g:link", NS)
        url = link.get("href") if link is not None else ""

        base = fname[:-4]
        stripped, author = strip_author(base)
        location = overrides.get(fname) or clean_location(stripped)
        length_m = track_length_m(pts)
        simp = rdp(pts, args.eps)
        simp = [[round(la, 5), round(lo, 5)] for la, lo in simp]
        lats = [p[0] for p in simp]; lons = [p[1] for p in simp]

        parsed.append({
            "file": fname, "location": location, "author": author,
            "url": url, "source": source_domain(url),
            "lengthKm": round(length_m/1000, 2),
            "pts": simp,
            "bbox": [min(lats), min(lons), max(lats), max(lons)],
            "center": [round(sum(lats)/len(lats), 5), round(sum(lons)/len(lons), 5)],
        })

    parsed.sort(key=lambda t: (t["location"].lower(), t["lengthKm"]))

    index, geometry = [], {}
    for i, t in enumerate(parsed, 1):
        tid = f"{i:04d}"
        name = f'{t["location"]}__{fmt_len(t["lengthKm"])}__{tid}'
        rec = {
            "id": tid, "name": name, "location": t["location"],
            "lengthKm": t["lengthKm"], "author": t["author"],
            "color": color_for(t["author"]), "source": t["source"],
            "url": t["url"], "bbox": t["bbox"], "center": t["center"],
        }
        if args.elevation:
            print(f"  высота {i}/{len(parsed)}: {name}", file=sys.stderr)
            g = fetch_gain([tuple(p) for p in t["pts"]], session)
            if g is not None:
                rec["gainM"] = g
        index.append(rec)
        geometry[tid] = t["pts"]

    authors = sorted({t["author"] for t in parsed})
    all_lat = [v for t in parsed for v in (t["bbox"][0], t["bbox"][2])]
    all_lon = [v for t in parsed for v in (t["bbox"][1], t["bbox"][3])]

    def pct(vals, p):
        s = sorted(vals); k = (len(s)-1)*p
        lo = int(math.floor(k)); hi = int(math.ceil(k))
        return s[lo] if lo == hi else s[lo]+(s[hi]-s[lo])*(k-lo)

    clat = [t["center"][0] for t in parsed]
    clon = [t["center"][1] for t in parsed]
    pad = 0.08
    view = [pct(clat, .02)-pad, pct(clon, .02)-pad, pct(clat, .98)+pad, pct(clon, .98)+pad]
    slider_max = max(5, math.ceil(pct([t["lengthKm"] for t in index], .95)))

    meta = {
        "count": len(index),
        "authors": [{"name": a, "color": color_for(a),
                     "count": sum(1 for t in index if t["author"] == a)} for a in authors],
        "extent": [min(all_lat), min(all_lon), max(all_lat), max(all_lon)],
        "view": [round(v, 4) for v in view],
        "maxLengthKm": max((t["lengthKm"] for t in index), default=0),
        "sliderMax": slider_max,
        "hasElevation": args.elevation,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    for fn, obj in (("index.json", index), ("geometry.json", geometry), ("meta.json", meta)):
        with open(os.path.join(OUT_DIR, fn), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Готово: {len(index)} треков, авторов {len(authors)}.")
    for fn in ("index.json", "geometry.json", "meta.json"):
        sz = os.path.getsize(os.path.join(OUT_DIR, fn))/1024
        print(f"  data/{fn}: {sz:.0f} KB")

if __name__ == "__main__":
    main()
