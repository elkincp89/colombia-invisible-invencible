"""Colombia Invisible-Invencible — priorización y asignación de recursos. streamlit run app.py"""
import hashlib, io, datetime as dt
import pandas as pd
import streamlit as st
import folium
import branca.colormap as cm
from streamlit_folium import st_folium
import model as M

st.set_page_config(page_title="Colombia Invisible-Invencible", page_icon="🇨🇴", layout="wide")

@st.cache_data
def cargar():
    geo, muns = M.load_municipios()
    return geo, muns, M.load_excel(), M.load_sipsa()

GEO, MUNS, DATA, SIPSA = cargar()
BYCODE = {m["code"]: m for m in MUNS}
DEPS = sorted({m["dep"] for m in MUNS})
LABEL = {m["code"]: f"{m['mun']} · {m['dep']}" for m in MUNS}
CODE_BY_LABEL = {v: k for k, v in LABEL.items()}
QUIBDO = next(m["code"] for m in MUNS if m["mun"] == "Quibdó")
NIVCOL = {"muyalta": "#8A0F1C", "alta": "#CE1126", "media": "#E8720C", "baja": "#F6C21B",
          "minima": "#EBD9A3", "ninguna": "#C9D3CD"}
ESTCOL = {"apto": "#2E7D5B", "completo": "#C8871F", "desbordado": "#CE1126", "sin": "#8892A0"}
ESTNOM = {"apto": "Apto", "completo": "Completo", "desbordado": "Desbordado", "sin": "Sin albergue"}

def miles(n):
    try: return f"{round(n):,}".replace(",", ".")
    except Exception: return str(n)

ss = st.session_state
ss.setdefault("sel_code", QUIBDO); ss.setdefault("det_sel", LABEL[QUIBDO])
ss.setdefault("override", {}); ss.setdefault("pledges", [])

def enfocar(code, set_det):
    ss.sel_code = code
    if set_det: ss.det_sel = LABEL[code]

OVR = ss.override

# ---------------- sidebar
st.sidebar.header("Calibración del modelo")
cfg = dict(M.DEFAULTS)
st.sidebar.caption("Costo de ayuda por persona/día")
cfg["entrega"] = st.sidebar.slider("Factor de entrega (mayorista SIPSA → ayuda)", 1.0, 3.0, M.DEFAULTS["entrega"], 0.1)
cfg["higiene"] = st.sidebar.number_input("Higiene (COP)", 0, 50000, M.DEFAULTS["higiene"], 500)
cfg["techo"] = st.sidebar.number_input("Techo (COP)", 0, 50000, M.DEFAULTS["techo"], 500)
cfg["meses"] = st.sidebar.number_input("Horizonte (meses)", 1, 24, M.DEFAULTS["meses"])
cfg["rural"] = st.sidebar.slider("Recargo logístico rural (%)", 0, 100, M.DEFAULTS["rural"], 5)
cfg["boost"] = st.sidebar.slider("Refuerzo de equidad (%)", 0, 150, M.DEFAULTS["boost"], 10)
cfg["shelter_frac"] = st.sidebar.slider("% de afectados que requieren albergue", 1, 100, M.DEFAULTS["shelter_frac"], 1)
st.sidebar.caption("% de población damnificada por nivel")
cfg["tier"] = {k: st.sidebar.number_input(M.SEVNOM[k], 0, 100, M.DEFAULTS["tier"][k], key=f"t_{k}")
               for k in ["critica", "alta", "media", "baja", "sindato"]}
cfg["tier"]["ninguna"] = 0
cfg["k"] = st.sidebar.slider("Número de clústeres (K-means)", 2, 6, M.DEFAULTS["k"])
cfg["usd_cop"] = st.sidebar.number_input("Tasa USD→COP", 1000, 10000, M.DEFAULTS["usd_cop"], 100)

CL = M.clusters(MUNS, cfg, OVR)
DON = {m["code"]: M.donacion(m, cfg, OVR, SIPSA) for m in MUNS}

# ---------------- encabezado
st.markdown("<h1 style='text-align:center;font-size:52px;margin-bottom:0;font-family:Georgia,serif;"
            "color:#0E2A5E'>Colombia Invisible-Invencible</h1>"
            "<p style='text-align:center;letter-spacing:.14em;text-transform:uppercase;font-size:13px;"
            "color:#8A6A10;font-weight:700;margin-top:4px'>Modelo de priorización y asignación de recursos · "
            "Sismo 10 ago 2026</p>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center;max-width:900px;margin:8px auto 4px;color:#44505c;font-size:16px'>"
            "Un modelo que hace <b>visible lo invisible</b>: estima <b>cuánto se debe donar a cada municipio</b> "
            "cruzando población, afectación reportada, vulnerabilidad y vivienda, con precios reales de canasta. "
            "Prioriza a los territorios pequeños, remotos y vulnerables para que la ayuda llegue donde más duele "
            "y <b>nadie quede olvidado</b>.</p>", unsafe_allow_html=True)
fm = BYCODE[ss.sel_code]
st.info(f"🎯 Municipio en foco: **{fm['mun']} · {fm['dep']}** — clic en el mapa o cambia el selector.")

tot_don = sum(DON.values()); tot_af = sum(M.afectada(m, cfg, OVR) for m in MUNS)
n_muyalta = sum(1 for m in MUNS if M.nivel_rel(m, MUNS, OVR) == "muyalta")
n_desb = sum(1 for m in MUNS if M.estado_albergue(m, DATA["centros"], cfg, OVR)[0] == "desbordado")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Total a donar (modelo)", M.abrev(tot_don), help=M.cop(tot_don))
k2.metric("Población afectada estimada", miles(tot_af))
k3.metric("Relevancia muy alta", f"{n_muyalta} municipios")
k4.metric("Albergues desbordados", f"{n_desb} municipios")

TABS = st.tabs(["🗺️ Mapa · cuánto donar", "🎯 Priorización y clústeres",
                "🤝 Donantes y distribución", "🏥 Centros y capacidad", "📐 Modelo y fuentes"])

# ================================================================ TAB 1
with TABS[0]:
    ver = st.radio("Colorear el mapa por", ["Cuánto donar (COP)", "Relevancia (0–100)", "Clúster"],
                   horizontal=True)
    st.caption("Coroplético del **indicador principal: cuánto se debe donar por municipio**. "
               "Marcadores: 🟢 albergue · 🔵 acopio · 🟣 PMU · 🔴 hospital · ⭕ banco de sangre.")
    colmap, coldet = st.columns([1.5, 1])
    with colmap:
        fmap = folium.Map(location=[4.9, -75.9], zoom_start=7, tiles="cartodbpositron")
        vals = sorted(v for v in DON.values() if v > 0)
        qs = [vals[min(len(vals) - 1, int(p * (len(vals) - 1)))] for p in (0, .2, .4, .6, .8, 1)] if len(vals) >= 5 else [0, 1, 2, 3, 4, 5]
        qs = sorted(set(qs)) or [0, 1]
        paleta = ["#FFEDA0", "#FED976", "#FEB24C", "#FD8D3C", "#F03B20", "#8A0F1C"][:max(1, len(qs) - 1)]
        cmap = cm.StepColormap(paleta, index=qs, vmin=qs[0], vmax=qs[-1],
                               caption="Cuánto donar por municipio (COP, 3 meses)")
        cluster_pal = ["#0033A0", "#CE1126", "#2E7D5B", "#E8720C", "#7A4FBF", "#00897B"]

        def stylefn(feat):
            p = feat["properties"]; code = p["code"]
            if ver.startswith("Cuánto"):
                col = "#C9D3CD" if DON[code] <= 0 else cmap(DON[code])
            elif ver.startswith("Relev"):
                col = NIVCOL[M.nivel_rel(BYCODE[code], MUNS, OVR)]
            else:
                cl = CL.get(code, (None,))[0]
                col = "#C9D3CD" if cl is None else cluster_pal[cl % len(cluster_pal)]
            return {"fillColor": col, "color": "#fff",
                    "weight": 2 if p["sev"] == "critica" else 0.5, "fillOpacity": 0.85}

        feats = []
        for f in GEO["features"]:
            p = f["properties"]; code = p["code"]
            feats.append({"type": "Feature", "geometry": f["geometry"], "properties": dict(
                p, donstr=M.cop(DON[code]), rel=M.relevancia(p, MUNS, OVR),
                clus=CL.get(code, (None, "—"))[1])})
        folium.GeoJson({"type": "FeatureCollection", "features": feats}, style_function=stylefn,
                       highlight_function=lambda x: {"weight": 3, "fillOpacity": 0.97},
                       tooltip=folium.GeoJsonTooltip(fields=["mun", "dep", "donstr", "rel", "clus"],
                       aliases=["Municipio", "Depto", "Donar", "Relevancia", "Clúster"])).add_to(fmap)
        if ver.startswith("Cuánto"):
            cmap.add_to(fmap)
        ico = {"albergue": ("green", "home"), "acopio": ("blue", "box"), "pmu": ("purple", "flag"),
               "hospital": ("red", "plus-sign"), "sangre": ("darkred", "tint")}
        for c in DATA["centros"]:
            color, ic = ico.get(c["tipo"], ("gray", "info-sign"))
            pop = (f"<b>{c['nombre']}</b><br>{c['categoria']}<br>{c['direccion']}<br>"
                   f"{('🧮 Capacidad: '+c['cap_txt']+'<br>') if c['cap_txt'] else ''}"
                   f"{('📞 '+c['contacto']+'<br>') if c['contacto'] else ''}"
                   f"<a href='{c['gmaps']}' target='_blank'>Google Maps</a>")
            folium.Marker([c["lat"], c["lng"]], tooltip=f"{c['nombre']} ({c['tipo']})",
                          popup=folium.Popup(pop, max_width=280),
                          icon=folium.Icon(color=color, icon=ic)).add_to(fmap)
        ret = st_folium(fmap, height=580, use_container_width=True, key="mapa",
                        returned_objects=["last_active_drawing"])
        draw = (ret or {}).get("last_active_drawing")
        if draw and draw.get("properties", {}).get("code") and draw["properties"]["code"] != ss.sel_code:
            enfocar(draw["properties"]["code"], True); st.rerun()

    with coldet:
        det = st.selectbox("Municipio (o clic en el mapa)", sorted(LABEL.values()), key="det_sel")
        cs = CODE_BY_LABEL[det]
        if cs != ss.sel_code: enfocar(cs, False)
        m = BYCODE[ss.sel_code]
        don = DON[m["code"]]; desg = M.desglose(m, cfg, OVR, SIPSA)
        rel = M.relevancia(m, MUNS, OVR); est, cap, need = M.estado_albergue(m, DATA["centros"], cfg, OVR)
        st.markdown(f"### {m['mun']} <span style='color:#5B6472;font-size:14px'>{m['dep']}</span>",
                    unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        c1.metric("💰 Cuánto donar", M.abrev(don), help=M.cop(don))
        c2.metric("Población afectada", miles(M.afectada(m, cfg, OVR)))
        c1.metric("Relevancia", f"{rel}/100")
        c2.metric("Clúster", CL.get(m["code"], (None, "—"))[1])
        st.markdown(f"**Desglose del donativo** ({cfg['meses']} meses):")
        st.markdown("".join(f"- {k}: **{M.cop(v)}**\n" for k, v in desg.items()))
        st.markdown(f"<div style='padding:8px 12px;border-radius:8px;background:{ESTCOL[est]}22;"
                    f"border-left:4px solid {ESTCOL[est]}'>🏠 <b>Albergue: {ESTNOM[est]}</b> — "
                    f"capacidad {miles(cap)} · requieren ~{miles(need)}</div>", unsafe_allow_html=True)
        st.markdown("#### Centros más cercanos")
        for tipo, etq in [("albergue", "🟢 Albergue"), ("acopio", "🔵 Acopio"), ("pmu", "🟣 PMU"),
                          ("hospital", "🔴 Hospital"), ("sangre", "⭕ Banco de sangre")]:
            c = M.mas_cercano(m, DATA["centros"], tipo)
            if not c: continue
            st.markdown(f"**{etq} — {c['nombre']}** · ~{c['dist_km']} km")
            st.caption(f"{c['direccion']}" + (f" · 🧮 {c['cap_txt']}" if c['cap_txt'] else "")
                       + (f" · 📞 {c['contacto']}" if c['contacto'] else "")
                       + (f" · [Maps]({c['gmaps']})" if c['gmaps'] else ""))

# ================================================================ TAB 2
with TABS[1]:
    st.subheader("Priorización por cuánto donar y clústeres de población")
    fdep = st.selectbox("Departamento", ["Todos"] + DEPS, key="rk_dep")
    rows = []
    for m in MUNS:
        if fdep != "Todos" and m["dep"] != fdep: continue
        est, cap, need = M.estado_albergue(m, DATA["centros"], cfg, OVR)
        rows.append({"Municipio": m["mun"], "Depto": m["dep"], "Nivel": M.SEVNOM[m["sev"]],
                     "Relevancia": M.relevancia(m, MUNS, OVR), "Clúster": CL.get(m["code"], (None, "—"))[1],
                     "Pob. afectada": miles(M.afectada(m, cfg, OVR)),
                     "Cuánto donar": M.cop(DON[m["code"]]), "Albergue": ESTNOM[est], "_d": DON[m["code"]]})
    df = pd.DataFrame(rows).sort_values("_d", ascending=False).drop(columns=["_d"]).reset_index(drop=True)
    st.dataframe(df, hide_index=True, use_container_width=True, height=420,
                 column_config={"Relevancia": st.column_config.ProgressColumn("Relevancia", min_value=0, max_value=100, format="%.0f")})
    st.markdown("#### Clústeres (K-means, no supervisado)")
    cc = {}
    for m in MUNS:
        lab = CL.get(m["code"], (None, "Sin afectación"))[1]
        cc.setdefault(lab, []).append(m["mun"])
    for lab, muns_ in cc.items():
        st.markdown(f"**{lab}** ({len(muns_)}): " + ", ".join(sorted(muns_)[:12])
                    + (" …" if len(muns_) > 12 else ""))

# ================================================================ TAB 3
def badge(nombre):
    ini = "".join(w[0] for w in str(nombre).split()[:2]).upper()
    col = "#" + hashlib.md5(str(nombre).encode()).hexdigest()[:6]
    return (f"<span style='display:inline-flex;align-items:center;justify-content:center;width:36px;height:36px;"
            f"border-radius:9px;background:{col};color:#fff;font-weight:700;font-size:12px'>{ini}</span>")

with TABS[2]:
    st.subheader("Donantes que publican su aporte")
    st.caption("Aquí se registran organizaciones, empresas o personas que anuncian su donativo. "
               "Con el total en dinero, el modelo estima cómo distribuirlo según el factor por municipio.")
    total_anuncios = 0
    st.markdown("**Anuncios oficiales**")
    for a in DATA["anuncios"]:
        nombre = str(a.get("Entidad / Donante", ""))
        montotxt = str(a.get("Monto / Recurso Anunciado", ""))
        val, especie = M.parse_monto_cop(montotxt, cfg["usd_cop"])
        if val: total_anuncios += val
        c1, c2, c3 = st.columns([0.5, 3, 2])
        c1.markdown(badge(nombre), unsafe_allow_html=True)
        c2.markdown(f"**{nombre}** · {a.get('Tipo de Entidad','')}")
        c3.markdown(f"**{'En especie' if especie else M.cop(val)}** · {montotxt}")
    st.divider()
    st.markdown("**Registrar un nuevo donante**")
    c1, c2 = st.columns(2)
    dn = c1.text_input("Donante (organización / empresa / persona)")
    dt_ = c2.selectbox("Tipo", ["Empresa", "ONG", "Persona", "Público", "Cooperación"])
    clase = c1.radio("Clase", ["dinero", "especie"], horizontal=True)
    monto = c2.number_input("Monto (COP) si es dinero", 0, step=1000000)
    categoria = c1.selectbox("Categoría (especie)", ["—"] + M.ESPECIE)
    cobertura = c2.text_input("Destino / cobertura (opcional)", placeholder="Nacional / municipio")
    if st.button("Registrar donante", type="primary"):
        if not dn or (clase == "dinero" and monto <= 0) or (clase == "especie" and categoria == "—"):
            st.error("Falta donante, o monto (dinero) / categoría (especie).")
        else:
            ss.pledges.append({"fecha": dt.date.today().isoformat(), "donante": dn, "tipo": dt_,
                               "clase": clase, "monto": int(monto) if clase == "dinero" else 0,
                               "categoria": categoria if clase == "especie" else "", "cobertura": cobertura})
            st.success("Donante registrado.")
    total_pledges = sum(p["monto"] for p in ss.pledges)
    if ss.pledges:
        st.dataframe(pd.DataFrame(ss.pledges), hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("Estimador de distribución por municipio")
    base_total = total_anuncios + total_pledges
    st.caption(f"Anuncios oficiales (dinero): {M.cop(total_anuncios)} · registrados: {M.cop(total_pledges)}")
    monto_dist = st.number_input("Monto total a distribuir (COP)", 0, value=int(base_total), step=1000000)
    if monto_dist > 0:
        alloc = M.distribuir(monto_dist, MUNS, cfg, OVR, SIPSA)
        dd = pd.DataFrame([{"Municipio": BYCODE[c]["mun"], "Depto": BYCODE[c]["dep"],
                            "Asignación sugerida": M.cop(v), "_v": v} for c, v in alloc.items()])
        dd = dd.sort_values("_v", ascending=False).drop(columns=["_v"]).reset_index(drop=True)
        st.dataframe(dd, hide_index=True, use_container_width=True, height=360)
        buff = io.StringIO(); dd.to_csv(buff, index=False)
        st.download_button("Descargar asignación (CSV)", buff.getvalue(), "distribucion.csv", "text/csv")

# ================================================================ TAB 4
with TABS[3]:
    st.subheader("Centros de acopio, albergues, PMU, hospitales y bancos de sangre")
    m = BYCODE[ss.sel_code]
    st.caption(f"Municipio en foco: **{m['mun']}** — más cercanos:")
    cols = st.columns(5)
    for col, tipo, etq in zip(cols, ["albergue", "acopio", "pmu", "hospital", "sangre"],
                              ["🟢 Albergue", "🔵 Acopio", "🟣 PMU", "🔴 Hospital", "⭕ Sangre"]):
        c = M.mas_cercano(m, DATA["centros"], tipo)
        col.markdown(f"**{etq}**  \n{c['nombre'] if c else '—'}"
                     + (f"  \n~{c['dist_km']} km · cap: {c['cap_txt'] or 's/d'}" if c else ""))
    st.markdown("#### Aptitud de albergues por municipio")
    filas = []
    for mm in MUNS:
        est, cap, need = M.estado_albergue(mm, DATA["centros"], cfg, OVR)
        if cap == 0 and need == 0: continue
        filas.append({"Municipio": mm["mun"], "Depto": mm["dep"], "Capacidad": miles(cap),
                      "Requieren albergue": miles(need), "Estado": ESTNOM[est], "_n": need})
    dfa = pd.DataFrame(filas).sort_values("_n", ascending=False).drop(columns=["_n"]).reset_index(drop=True)
    st.dataframe(dfa, hide_index=True, use_container_width=True, height=280)
    st.markdown("#### Directorio de centros")
    tsel = st.radio("Tipo", ["Todos", "albergue", "acopio", "pmu", "hospital", "sangre"], horizontal=True)
    fil = [c for c in DATA["centros"] if tsel == "Todos" or c["tipo"] == tsel]
    dfc = pd.DataFrame([{"Tipo": c["tipo"], "Nombre": c["nombre"], "Ciudad": c["ciudad"],
                         "Categoría": c["categoria"], "Capacidad": c["cap_txt"], "Dirección": c["direccion"],
                         "Contacto": c["contacto"], "Horario": c["horario"], "Maps": c["gmaps"]} for c in fil])
    st.dataframe(dfc, hide_index=True, use_container_width=True,
                 column_config={"Maps": st.column_config.LinkColumn()})

# ================================================================ TAB 5
with TABS[4]:
    st.subheader("Modelo y fuentes")
    st.markdown("""
**¿Por qué no red neuronal ni random forest?** Ambos son modelos *supervisados*: requieren miles de
ejemplos etiquetados con la respuesta correcta (cuánto se necesitó/donó de verdad) para entrenarse.
Aquí hay **98 municipios y ninguna etiqueta**; entrenar un RF o una red sobre eso memoriza ruido y, además,
una veeduría pública exige un modelo **explicable**, no una caja negra.

**Modelo idóneo (el que sí encaja con estos datos):**
1. **Índice de relevancia (0–100), estilo DFS** — población expuesta × privación (vulnerabilidad),
   reescalado. Valida bien: Cali ≈ 100, como en el reporte DFS.
2. **Clustering no supervisado (K-means)** — agrupa municipios por población, densidad, ruralidad,
   vivienda precaria, estrato y severidad. No necesita etiquetas.
3. **Indicador principal — cuánto donar por municipio (COP)** = población afectada × costo diario
   (canasta **SIPSA** + higiene + techo, con recargo rural y refuerzo de equidad) × horizonte.
""")
    st.markdown("**Víctimas por departamento — OPS/UNGRD SITREP 4 (13 ago)** y exposición DFS (MMI VI+):")
    st.dataframe(pd.DataFrame([{"Departamento": d, "Fallecidos": v["fall"], "Heridos": v["her"],
                                "Desaparecidos": v["desap"], "Rescatados": v["resc"],
                                "Total víctimas": v["total"], "Población expuesta (DFS)": miles(v["expuestos"])}
                               for d, v in M.DEPVICT.items()]), hide_index=True, use_container_width=True)
    st.markdown(f"**Precios de canasta (SIPSA, {SIPSA.get('_fecha','2026')}, mayorista COP/persona/día):** "
                + " · ".join(f"{k}: {M.cop(v)}" for k, v in SIPSA.items() if not k.startswith('_')))
    st.caption("Fuentes: DANE (población 2024, densidad, CNPV 2018, GEIH 2024, MGN 2022) · OPS/UNGRD SITREP 4 · "
               "DFS Panorama Rápido (relevancia y fallecidos modelados) · DANE-SIPSA (precios) · "
               "Excel de centros y anuncios. Los supuestos son calibrables; el % de damnificados se "
               "reemplazará con el Registro Único de Damnificados de la UNGRD.")
