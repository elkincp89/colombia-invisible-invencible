"""
Colombia Invisible-Invencible — módulo de datos y modelo.
Sin dependencias pesadas: pandas + openpyxl + json + math.

Contiene:
  - Carga de municipios (GeoJSON DANE) y del Excel de donaciones/ubicaciones.
  - Parseo y clasificación de centros (acopio, hospital/sangre, albergue).
  - Índice de prioridad (gravedad, densidad, lejanía, vivienda, estrato).
  - Índice de donación en dinero por canasta familiar, higiene y techo.
  - Municipio -> centro más cercano (haversine).
"""
import json
import math
import re
import os
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
GEOJSON = os.path.join(BASE, "data", "municipios.geojson")
EXCEL = os.path.join(BASE, "data", "donaciones.xlsx")

# ----------------------------------------------------------------------------
# Parámetros por defecto (todos calibrables desde la interfaz)
# ----------------------------------------------------------------------------
DEFAULTS = {
    # Canasta diaria por persona (COP) — pilares: canasta familiar, higiene, techo
    "canasta": 8000,
    "higiene": 3000,
    "techo": 5000,
    "meses": 3,
    "rural": 15,          # recargo logístico rural (%)
    "boost": 50,          # refuerzo de equidad al potencial (%)
    "usd_cop": 4000,      # tasa referencial USD->COP para anuncios
    # % de población damnificada por nivel de severidad
    "tier": {"critica": 30, "alta": 15, "media": 6, "baja": 2, "sindato": 0, "ninguna": 0},
    # pesos del índice de prioridad (suman ~100)
    "pesos": {"gravedad": 35, "densidad": 15, "lejania": 20, "vivienda": 20, "estrato": 10},
}

SEVSCORE = {"critica": 1.0, "alta": 0.7, "media": 0.45, "baja": 0.2, "sindato": 0.12, "ninguna": 0.0}
SEVNOM = {"critica": "Crítico", "alta": "Alto", "media": "Medio", "baja": "Bajo",
          "sindato": "Sin dato", "ninguna": "Sin afectación"}
PRINOM = {"muyalta": "Muy alta", "alta": "Alta", "media": "Media", "baja": "Baja", "minima": "Mínima"}
PRICOL = {"muyalta": "#8A0F1C", "alta": "#CE1126", "media": "#E8720C",
          "baja": "#F6C21B", "minima": "#EBD9A3", "sindato": "#D8D2C4", "ninguna": "#C9D3CD"}

ESPECIE = ["Alimentos", "Agua", "Medicamentos", "Elementos de higiene",
           "Ropa y comodidades", "Mano de obra", "Vehículos de transporte",
           "Maquinaria", "Elementos de protección"]


# ----------------------------------------------------------------------------
# Carga de municipios
# ----------------------------------------------------------------------------
def load_municipios():
    with open(GEOJSON, encoding="utf-8") as fh:
        geo = json.load(fh)
    muns = [f["properties"] for f in geo["features"]]
    return geo, muns


# ----------------------------------------------------------------------------
# Excel: centros, cuentas y anuncios
# ----------------------------------------------------------------------------
def _parse_coords(s):
    if not isinstance(s, str):
        return None, None
    m = re.findall(r"-?\d+\.\d+", s)
    if len(m) >= 2:
        return float(m[0]), float(m[1])
    return None, None


def _clasificar(cat):
    c = (cat or "").lower()
    if any(k in c for k in ["hospital", "hemocentro", "asistencia médica", "asistencia medica", "hospitalario", "sangre", "referral"]):
        return "hospital"
    if "albergue" in c:
        return "albergue"
    return "acopio"  # acopio, socorro, PMU, operaciones, bienestar animal


def load_excel():
    xls = pd.ExcelFile(EXCEL)
    out = {}

    # --- Ubicaciones geoespaciales (acopios, albergues, algunos hospitales) ---
    ub = pd.read_excel(EXCEL, sheet_name="Ubicaciones Geoespaciales")
    centros = []
    for _, r in ub.iterrows():
        lat, lng = _parse_coords(r.get("Coordenadas GPS (Lat, Long)"))
        if lat is None:
            continue
        centros.append({
            "tipo": _clasificar(r.get("Categoría de Centro")),
            "categoria": str(r.get("Categoría de Centro") or ""),
            "nombre": str(r.get("Nombre del Centro / Entidad") or ""),
            "ciudad": str(r.get("Ciudad / Municipio") or ""),
            "direccion": str(r.get("Dirección Física / Referencia") or ""),
            "lat": lat, "lng": lng,
            "gmaps": str(r.get("Enlace Google Maps") or ""),
            "servicios": str(r.get("Servicios / Elementos Requeridos") or ""),
            "horario": str(r.get("Horarios y Contacto") or ""),
            "telefono": "", "web": "", "nivel": "",
        })

    # --- Red hospitalaria y sangre ---
    hosp = pd.read_excel(EXCEL, sheet_name="Red Hospitalaria y Sangre")
    for _, r in hosp.iterrows():
        lat, lng = _parse_coords(r.get("Coordenadas GPS (Lat, Long)"))
        if lat is None:
            continue
        tipo = "hospital"
        centros.append({
            "tipo": tipo,
            "categoria": str(r.get("Tipo de Centro Médico") or ""),
            "nombre": str(r.get("Nombre del Hospital / Banco") or ""),
            "ciudad": str(r.get("Departamento / Municipio") or ""),
            "direccion": str(r.get("Dirección Física") or ""),
            "lat": lat, "lng": lng,
            "gmaps": str(r.get("Enlace Google Maps") or ""),
            "servicios": str(r.get("Estado de Operación y Requerimientos") or ""),
            "horario": "",
            "telefono": str(r.get("Teléfono Urgencias / Donación") or ""),
            "web": str(r.get("Página Web Oficial") or ""),
            "nivel": str(r.get("Nivel de Atención") or ""),
        })
    # de-duplicar por (nombre, lat, lng) para no repetir hospitales que están en ambas hojas
    seen = set()
    uniq = []
    for c in centros:
        key = (c["nombre"].strip().lower(), round(c["lat"], 3), round(c["lng"], 3))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    out["centros"] = uniq

    # --- Cuentas / canales certificados para donar ---
    cu = pd.read_excel(EXCEL, sheet_name="Cuentas y Canal Digital")
    cu = cu.fillna("")
    out["cuentas"] = cu.to_dict("records")

    # --- Anuncios oficiales ---
    an = pd.read_excel(EXCEL, sheet_name="Anuncios Oficiales")
    an = an.fillna("")
    out["anuncios"] = an.to_dict("records")

    return out


def parse_monto_cop(texto, usd_cop):
    """Extrae un COP aproximado del texto del anuncio. Devuelve (cop|None, especie_bool)."""
    if not isinstance(texto, str):
        return None, True
    t = texto.replace(".", "").replace(",", "").strip()
    up = texto.upper()
    if "US$" in up or "USD" in up:
        m = re.search(r"US\$?\s*([\d\.]+)", texto)
        if m:
            val = float(m.group(1).replace(".", ""))
            return val * usd_cop, False
    if "COP" in up or "$" in texto:
        m = re.search(r"\$?\s*([\d\.]+)\s*COP", texto) or re.search(r"\$\s*([\d\.]+)", texto)
        if m:
            return float(m.group(1).replace(".", "")), False
    return None, True  # en especie / no cuantificable


# ----------------------------------------------------------------------------
# Geodistancia y centro más cercano
# ----------------------------------------------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def mas_cercano(m, centros, tipo):
    lat, lng = m.get("lat"), m.get("lng")
    if lat is None:
        return None
    cand = [c for c in centros if c["tipo"] == tipo]
    if not cand:
        return None
    best, bestd = None, 1e18
    for c in cand:
        d = haversine(lat, lng, c["lat"], c["lng"])
        if d < bestd:
            best, bestd = c, d
    best = dict(best)
    best["dist_km"] = round(bestd, 1)
    return best


# ----------------------------------------------------------------------------
# Modelo: prioridad, potencial e índice de donación
# ----------------------------------------------------------------------------
def _rural_share(m):
    return (m["pobres"] / m["pob2024"]) if m.get("pob2024") else 0.0


def _estrato_vuln(m, estrato_over):
    o = estrato_over.get(m["code"])
    if o not in (None, ""):
        return max(0.0, min(1.0, (6 - float(o)) / 5))
    return min(1.0, _rural_share(m) * 0.6 + (m.get("vivPrec") or 0) * 0.4)


def _dens_bounds(muns):
    ds = [m.get("dens") or 0 for m in muns]
    return min(ds), max(ds)


def prioridad(m, muns, pesos, estrato_over):
    dmin, dmax = _dens_bounds(muns)
    g = SEVSCORE.get(m["sev"], 0)
    ld = math.log((m.get("dens") or 0) + 1)
    d = (ld - math.log(dmin + 1)) / (math.log(dmax + 1) - math.log(dmin + 1)) if dmax > dmin else 0
    lej = _rural_share(m)
    viv = m.get("vivPrec") or 0
    est = _estrato_vuln(m, estrato_over)
    tot = sum(pesos.values()) or 1
    p = 100 * (g * pesos["gravedad"] + d * pesos["densidad"] + lej * pesos["lejania"]
               + viv * pesos["vivienda"] + est * pesos["estrato"]) / tot
    return round(p)


def nivel_pri(m, p):
    if m["sev"] == "ninguna":
        return "ninguna"
    if p >= 70: return "muyalta"
    if p >= 50: return "alta"
    if p >= 32: return "media"
    if p >= 16: return "baja"
    return "minima"


def dias(cfg):
    return cfg["meses"] * 30


def factor_ubic(m, cfg):
    share = _rural_share(m)
    return 1 + (cfg["rural"] / 100) * share


def pob_objetivo(m, cfg, override):
    o = override.get(m["code"])
    pct = float(o) if o not in (None, "") else cfg["tier"].get(m["sev"], 0)
    return round(m["pob2024"] * pct / 100)


def _equidad(m, estrato_over):
    return (_rural_share(m) + (m.get("vivPrec") or 0) + _estrato_vuln(m, estrato_over)) / 3


def indice_donacion(m, cfg, override, estrato_over):
    """Índice de donación en dinero (COP) por canasta familiar, higiene y techo.
    Devuelve dict con desglose y total."""
    po = pob_objetivo(m, cfg, override)
    fu = factor_ubic(m, cfg)
    d = dias(cfg)
    eq = 1 + (cfg["boost"] / 100) * _equidad(m, estrato_over)
    base_dia = {
        "canasta": cfg["canasta"] * fu,
        "higiene": cfg["higiene"] * fu,
        "techo": cfg["techo"] * fu,
    }
    total_dia_persona = sum(base_dia.values())
    res = {k: round(v * po * d * eq) for k, v in base_dia.items()}
    res["total"] = round(total_dia_persona * po * d * eq)
    res["por_familia_dia"] = round(total_dia_persona * m["ppv"])
    res["por_persona_dia"] = round(total_dia_persona)
    res["pob_objetivo"] = po
    return res


def potencial(m, cfg, override, estrato_over):
    return indice_donacion(m, cfg, override, estrato_over)["total"]


POTNOM = {"muyalta": "Muy alto", "alta": "Alto", "media": "Medio",
          "baja": "Bajo", "minima": "Mínimo", "sindato": "Sin dato (% por definir)",
          "ninguna": "Sin afectación"}


def pot_niveles(muns, cfg, override, estrato_over):
    """Umbrales de quintiles del potencial, solo sobre municipios con potencial > 0."""
    vals = sorted(v for v in (potencial(m, cfg, override, estrato_over)
                              for m in muns if m["sev"] != "ninguna") if v > 0)
    if len(vals) < 5:
        return [0, 0, 0, 0]
    def q(pp):
        return vals[min(len(vals) - 1, int(pp * (len(vals) - 1)))]
    return [q(0.2), q(0.4), q(0.6), q(0.8)]


def nivel_potencial(m, potv, thr):
    if m["sev"] == "ninguna":
        return "ninguna"
    if potv <= 0:
        return "sindato"
    q20, q40, q60, q80 = thr
    if potv >= q80: return "muyalta"
    if potv >= q60: return "alta"
    if potv >= q40: return "media"
    if potv >= q20: return "baja"
    return "minima"


# ----------------------------------------------------------------------------
# Utilidades de formato
# ----------------------------------------------------------------------------
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
    cfg = dict(DEFAULTS)
    print("Municipios:", len(muns), "| Centros:", len(data["centros"]),
          "| Cuentas:", len(data["cuentas"]), "| Anuncios:", len(data["anuncios"]))
    # prueba de prioridad e índice en 3 municipios
    for name in ["Quibdó", "Nuquí", "Cali"]:
        m = next(x for x in muns if x["mun"] == name)
        p = prioridad(m, muns, cfg["pesos"], {})
        idx = indice_donacion(m, cfg, {}, {})
        ac = mas_cercano(m, data["centros"], "acopio")
        ho = mas_cercano(m, data["centros"], "hospital")
        print(f"\n{name}: prioridad={p} ({nivel_pri(m,p)}) | pobObj={idx['pob_objetivo']:,} "
              f"| familia/día={cop(idx['por_familia_dia'])} | índice 3m={abrev(idx['total'])}")
        print(f"   acopio+cercano: {ac['nombre'] if ac else '—'} ({ac['dist_km'] if ac else '—'} km)")
        print(f"   hospital+cercano: {ho['nombre'] if ho else '—'} ({ho['dist_km'] if ho else '—'} km)")
