"""
Colombia Invisible-Invencible — modelo de priorización y asignación de recursos.

Enfoque (documentado en la pestaña "Modelo"):
  * Índice de RELEVANCIA (0-100), estilo DFS: población expuesta x privación, reescalado.
  * Clustering NO supervisado (K-means) de municipios por vulnerabilidad/vivienda/población.
  * Indicador principal: CUÁNTO SE DEBE DONAR por municipio (COP), con canasta real (SIPSA).
No se usan redes neuronales ni random forest: no hay variable objetivo etiquetada ni volumen
de datos para entrenarlos, y una veeduría requiere un modelo explicable.
"""
import json, math, re, os
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
GEOJSON = os.path.join(BASE, "data", "municipios.geojson")
EXCEL = os.path.join(BASE, "data", "donaciones.xlsx")
SIPSA = os.path.join(BASE, "data", "sipsa_costos.json")

# ---------------------------------------------------------------- parámetros
DEFAULTS = {
    "entrega": 1.8,      # markup mayorista SIPSA -> costo de entrega de ayuda
    "higiene": 3000,     # COP/persona/día
    "techo": 6000,       # COP/persona/día
    "meses": 3,
    "rural": 15,         # recargo logístico rural (%)
    "boost": 40,         # refuerzo de equidad (%)
    "shelter_frac": 25,  # % de afectados que requieren albergue
    "usd_cop": 4000,
    "tier": {"critica": 30, "alta": 15, "media": 6, "baja": 2, "sindato": 3, "ninguna": 0},
    "pesos": {"gravedad": 30, "poblacion": 20, "densidad": 10, "lejania": 15, "vivienda": 15, "estrato": 10},
    "k": 4,
}
SEVSCORE = {"critica": 1.0, "alta": 0.7, "media": 0.45, "baja": 0.2, "sindato": 0.12, "ninguna": 0.0}
EXPO = {"critica": 1.0, "alta": 0.8, "media": 0.5, "baja": 0.3, "sindato": 0.4, "ninguna": 0.0}
SEVNOM = {"critica": "Crítico", "alta": "Alto", "media": "Medio", "baja": "Bajo",
          "sindato": "Sin dato", "ninguna": "Sin afectación"}
NIVNOM = {"muyalta": "Muy alto", "alta": "Alto", "media": "Medio", "baja": "Bajo",
          "minima": "Mínimo", "ninguna": "Sin afectación"}

# Víctimas por depto — OPS/UNGRD SITREP 4 (corte 13 ago 2026, 17:00)
DEPVICT = {
    "Valle del Cauca": {"fall": 146, "her": 2348, "desap": 236, "resc": 89, "total": 2819, "expuestos": 3499908},
    "Risaralda": {"fall": 99, "her": 470, "desap": 260, "resc": 250, "total": 1079, "expuestos": 872174},
    "Quindío": {"fall": 1, "her": 314, "desap": 0, "resc": 0, "total": 315, "expuestos": 575498},
    "Chocó": {"fall": 13, "her": 139, "desap": 0, "resc": 0, "total": 152, "expuestos": 369021},
}
# DFS — puntaje de relevancia (exposición ponderada por privación) y fallecidos modelados (USGS PAGER)
DFS_REL = {"Cali": 100.0, "Palmira": 26.0, "Pereira": 25.3, "Armenia": 22.4, "Dosquebradas": 19.2,
           "Buenaventura": 15.0, "Tuluá": 13.6, "Quibdó": 11.9, "Jamundí": 11.7, "Cartago": 10.6,
           "Candelaria": 10.4, "Guadalajara De Buga": 8.0}
DFS_FAT = {"Pereira": 90, "Dosquebradas": 63, "Cali": 52, "Armenia": 51, "Cartago": 46, "Quibdó": 35,
           "Buenaventura": 31, "Santa Rosa De Cabal": 10, "Tuluá": 9, "Palmira": 8, "Calarcá": 7}

ESPECIE = ["Alimentos", "Agua", "Medicamentos", "Elementos de higiene", "Ropa y comodidades",
           "Mano de obra", "Vehículos de transporte", "Maquinaria", "Elementos de protección"]


# ---------------------------------------------------------------- carga
def load_municipios():
    with open(GEOJSON, encoding="utf-8") as fh:
        geo = json.load(fh)
    for f in geo["features"]:
        p = f["properties"]
        p["dfs_rel"] = DFS_REL.get(p["mun"])
        p["dfs_fat"] = DFS_FAT.get(p["mun"])
    return geo, [f["properties"] for f in geo["features"]]


def load_sipsa():
    try:
        return json.load(open(SIPSA, encoding="utf-8"))
    except Exception:
        return {}


def _coords(s):
    if not isinstance(s, str):
        return None, None
    m = re.findall(r"-?\d+\.\d+", s)
    return (float(m[0]), float(m[1])) if len(m) >= 2 else (None, None)


def _cap_num(s):
    """Extrae número de personas/camas de un texto de capacidad."""
    if not isinstance(s, str):
        return None
    t = s.replace(".", "").replace(",", "")
    m = re.search(r"(\d{2,6})", t)
    return int(m.group(1)) if m else None


def _tipo_centro(cat):
    c = (cat or "").lower()
    if "pmu" in c or "mando" in c:
        return "pmu"
    if "sangre" in c or "donación" in c or "donacion" in c or "hemocentro" in c or "hemo" in c:
        return "sangre"
    if "hospital" in c or "clínica" in c or "clinica" in c or "salud" in c or "urgencias" in c or "trauma" in c:
        return "hospital"
    if "albergue" in c:
        return "albergue"
    if "acopio" in c:
        return "acopio"
    return "acopio"


def load_excel():
    out = {}
    centros = []
    alb = pd.read_excel(EXCEL, sheet_name="ALBERGUES").fillna("")
    for _, r in alb.iterrows():
        lat, lng = _coords(r.get("Coordenadas GPS"))
        if lat is None:
            continue
        cat = str(r.get("Categoría", ""))
        centros.append({"tipo": _tipo_centro(cat), "categoria": cat,
                        "nombre": str(r.get("Nombre del Centro", "")),
                        "ciudad": str(r.get("Ciudad / Municipio", "")),
                        "direccion": str(r.get("Dirección Física / Barrio / Referencia", "")),
                        "lat": lat, "lng": lng, "gmaps": str(r.get("Enlace Google Maps", "")),
                        "servicios": str(r.get("Servicios / Elementos Requeridos", "")),
                        "horario": str(r.get("Horarios", "")),
                        "contacto": str(r.get("Número de Contacto", "")),
                        "cap_txt": str(r.get("Capacidad Estimada", "")),
                        "cap_num": _cap_num(r.get("Capacidad Estimada")), "nivel": ""})
    hos = pd.read_excel(EXCEL, sheet_name="HOSPITALES").fillna("")
    for _, r in hos.iterrows():
        lat, lng = _coords(r.get("Coordenadas GPS"))
        if lat is None:
            continue
        cat = str(r.get("Categoría", ""))
        centros.append({"tipo": _tipo_centro(cat), "categoria": cat,
                        "nombre": str(r.get("Nombre del Centro / Institución", "")),
                        "ciudad": str(r.get("Ciudad / Municipio", "")),
                        "direccion": str(r.get("Dirección Física / Barrio / Referencia", "")),
                        "lat": lat, "lng": lng, "gmaps": str(r.get("Enlace Google Maps", "")),
                        "servicios": str(r.get("Servicios Disponibles / Nivel de Atención", "")),
                        "horario": str(r.get("Horario de Atención", "")),
                        "contacto": str(r.get("Número de Contacto / Línea Directa", "")),
                        "cap_txt": str(r.get("Capacidad / Camas / Tipo", "")),
                        "cap_num": _cap_num(r.get("Capacidad / Camas / Tipo")),
                        "nivel": str(r.get("Servicios Disponibles / Nivel de Atención", ""))})
    out["centros"] = centros
    out["anuncios"] = pd.read_excel(EXCEL, sheet_name="Anuncios Oficiales").fillna("").to_dict("records")
    return out


def parse_monto_cop(texto, usd_cop):
    if not isinstance(texto, str):
        return None, True
    up = texto.upper()
    if "US$" in up or "USD" in up:
        m = re.search(r"US\$?\s*([\d\.]+)", texto)
        if m:
            return float(m.group(1).replace(".", "")) * usd_cop, False
    if "COP" in up or "$" in texto:
        m = re.search(r"\$?\s*([\d\.]+)\s*COP", texto) or re.search(r"\$\s*([\d\.]+)", texto)
        if m:
            return float(m.group(1).replace(".", "")), False
    return None, True


# ---------------------------------------------------------------- geodistancia
def haversine(a, b, c, d):
    R = 6371.0
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = math.radians(c - a), math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def mas_cercano(m, centros, tipo):
    if m.get("lat") is None:
        return None
    cand = [c for c in centros if c["tipo"] == tipo]
    if not cand:
        return None
    best = min(cand, key=lambda c: haversine(m["lat"], m["lng"], c["lat"], c["lng"]))
    best = dict(best)
    best["dist_km"] = round(haversine(m["lat"], m["lng"], best["lat"], best["lng"]), 1)
    return best


# ---------------------------------------------------------------- modelo
def _rural(m):
    return (m["pobres"] / m["pob2024"]) if m.get("pob2024") else 0.0


def _estrato(m, over):
    o = over.get(m["code"])
    if o not in (None, ""):
        return max(0.0, min(1.0, (6 - float(o)) / 5))
    return min(1.0, _rural(m) * 0.6 + (m.get("vivPrec") or 0) * 0.4)


def _vuln(m, over):
    return (_rural(m) + (m.get("vivPrec") or 0) + _estrato(m, over)) / 3


def relevancia(m, muns, over):
    """Índice de relevancia 0-100 (estilo DFS): población expuesta x privación, reescalado."""
    def raw(x):
        if x["sev"] == "ninguna":
            return 0.0
        expo = (x.get("pob2024") or 0) * EXPO.get(x["sev"], 0)
        depr = 0.3 + 0.7 * _vuln(x, over)
        return expo * depr
    mx = max((raw(x) for x in muns), default=1) or 1
    return round(100 * raw(m) / mx, 1)


def nivel_rel(m, muns, over):
    vals = sorted(relevancia(x, muns, over) for x in muns if x["sev"] != "ninguna")
    if m["sev"] == "ninguna":
        return "ninguna"
    if len(vals) < 5:
        return "media"
    q = lambda p: vals[min(len(vals) - 1, int(p * (len(vals) - 1)))]
    r = relevancia(m, muns, over)
    if r >= q(0.8): return "muyalta"
    if r >= q(0.6): return "alta"
    if r >= q(0.4): return "media"
    if r >= q(0.2): return "baja"
    return "minima"


def afectada(m, cfg, over):
    o = over.get(m["code"])
    pct = float(o) if o not in (None, "") else cfg["tier"].get(m["sev"], 0)
    return round(m["pob2024"] * pct / 100)


def costo_persona_dia(m, cfg, sipsa):
    canasta = sipsa.get(m["dep"], 3000) * cfg["entrega"]
    fu = 1 + (cfg["rural"] / 100) * _rural(m)
    return (canasta + cfg["higiene"] + cfg["techo"]) * fu


def donacion(m, cfg, over, sipsa):
    """Indicador principal: cuánto se debe donar por municipio (COP)."""
    eq = 1 + (cfg["boost"] / 100) * _vuln(m, over)
    return round(afectada(m, cfg, over) * costo_persona_dia(m, cfg, sipsa) * cfg["meses"] * 30 * eq)


def desglose(m, cfg, over, sipsa):
    fu = 1 + (cfg["rural"] / 100) * _rural(m)
    eq = 1 + (cfg["boost"] / 100) * _vuln(m, over)
    af, dias = afectada(m, cfg, over), cfg["meses"] * 30
    base = {"Alimentos (SIPSA)": sipsa.get(m["dep"], 3000) * cfg["entrega"] * fu,
            "Higiene": cfg["higiene"] * fu, "Techo": cfg["techo"] * fu}
    return {k: round(v * af * dias * eq) for k, v in base.items()}


# ---------------------------------------------------------------- albergues: aptitud
def _norm(s):
    import unicodedata
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def capacidad_albergue(m, centros):
    mn = _norm(m["mun"])
    total = 0
    for c in centros:
        if c["tipo"] == "albergue" and mn in _norm(c["ciudad"]) and c["cap_num"]:
            total += c["cap_num"]
    return total


def estado_albergue(m, centros, cfg, over):
    cap = capacidad_albergue(m, centros)
    need = round(afectada(m, cfg, over) * cfg["shelter_frac"] / 100)
    if cap == 0:
        return "sin", cap, need
    if need > cap:
        return "desbordado", cap, need
    if need > 0.85 * cap:
        return "completo", cap, need
    return "apto", cap, need


# ---------------------------------------------------------------- clustering
def clusters(muns, cfg, over):
    import numpy as np
    from sklearn.preprocessing import StandardScaler
    from sklearn.cluster import KMeans
    aff = [m for m in muns if m["sev"] != "ninguna"]
    X = np.array([[math.log((m.get("pob2024") or 0) + 1), math.log((m.get("dens") or 0) + 1),
                   _rural(m), m.get("vivPrec") or 0, _estrato(m, over), SEVSCORE.get(m["sev"], 0)]
                  for m in aff])
    Xs = StandardScaler().fit_transform(X)
    k = min(cfg.get("k", 4), len(aff))
    km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(Xs)
    labels = km.labels_
    # etiqueta descriptiva por centroide (en unidades originales)
    desc = {}
    for cl in range(k):
        idx = [i for i, l in enumerate(labels) if l == cl]
        pob = np.mean([X[i][0] for i in idx]); rur = np.mean([X[i][2] for i in idx])
        viv = np.mean([X[i][3] for i in idx]); dens = np.mean([X[i][1] for i in idx])
        if dens > np.log(500):
            nombre = "Urbano denso"
        elif rur > 0.6 and viv > 0.12:
            nombre = "Rural remoto vulnerable"
        elif rur > 0.5:
            nombre = "Rural disperso"
        else:
            nombre = "Intermedio"
        desc[cl] = f"{nombre}"
    res = {}
    for m, l in zip(aff, labels):
        res[m["code"]] = (int(l), desc[int(l)])
    return res


# ---------------------------------------------------------------- distribución
def distribuir(total_cop, muns, cfg, over, sipsa):
    pesos = {m["code"]: donacion(m, cfg, over, sipsa) for m in muns}
    s = sum(pesos.values()) or 1
    return {code: round(total_cop * w / s) for code, w in pesos.items()}


# ---------------------------------------------------------------- formato
def cop(n):
    try:
        return "$" + f"{round(n):,}".replace(",", ".")
    except Exception:
        return "$0"


def abrev(n):
    n = n or 0
    if n >= 1e12: return f"${n/1e12:.1f} B"
    if n >= 1e9: return f"${n/1e9:.1f} mM"
    if n >= 1e6: return f"${n/1e6:.1f} M"
    if n >= 1e3: return f"${n/1e3:.0f} K"
    return f"${round(n)}"


if __name__ == "__main__":
    geo, muns = load_municipios()
    data = load_excel()
    sipsa = load_sipsa()
    cfg = dict(DEFAULTS)
    cl = clusters(muns, cfg, {})
    print("Municipios:", len(muns), "| Centros:", len(data["centros"]),
          "| SIPSA:", {k: v for k, v in sipsa.items() if not k.startswith("_")})
    tot = sum(donacion(m, cfg, {}, sipsa) for m in muns)
    print("Total a donar (modelo):", abrev(tot))
    rank = sorted(muns, key=lambda m: donacion(m, cfg, {}, sipsa), reverse=True)
    print("\nTOP 6 cuánto donar:")
    for m in rank[:6]:
        st, cap, need = estado_albergue(m, data["centros"], cfg, {})
        print(f"  {m['mun']:16s} {abrev(donacion(m,cfg,{},sipsa)):>9} | relev {relevancia(m,muns,{}):5} "
              f"| cluster {cl.get(m['code'],('-','-'))[1]:22} | albergue {st} (cap {cap}/need {need})")
