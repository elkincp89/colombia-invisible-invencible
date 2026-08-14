"""
Colombia Invisible-Invencible
Veeduría de recursos donados y potencial de donación según afectaciones.
Ejecutar:  streamlit run app.py
"""
import hashlib
import io
import datetime as dt
import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium

import model as M

st.set_page_config(page_title="Colombia Invisible-Invencible",
                   page_icon="🇨🇴", layout="wide")

# ---------------------------------------------------------------- carga datos
@st.cache_data
def cargar():
    geo, muns = M.load_municipios()
    data = M.load_excel()
    return geo, muns, data

GEO, MUNS, DATA = cargar()
BYCODE = {m["code"]: m for m in MUNS}
DEPS = sorted({m["dep"] for m in MUNS})

# ---------------------------------------------------------------- estado
if "override" not in st.session_state:
    st.session_state.override = {}      # code -> % dam
if "estrato" not in st.session_state:
    st.session_state.estrato = {}       # code -> estrato 1-6
if "aportes" not in st.session_state:
    st.session_state.aportes = []       # registros de donativos

# ---------------------------------------------------------------- sidebar cfg
st.sidebar.header("Calibración del modelo")
cfg = dict(M.DEFAULTS)
st.sidebar.caption("Canasta diaria por persona (COP) — pilares básicos")
cfg["canasta"] = st.sidebar.number_input("Canasta familiar (alimentación)", 0, 100000, M.DEFAULTS["canasta"], 500)
cfg["higiene"] = st.sidebar.number_input("Higiene (agua ~15 L, aseo)", 0, 100000, M.DEFAULTS["higiene"], 500)
cfg["techo"] = st.sidebar.number_input("Techo (albergue / arriendo temporal)", 0, 100000, M.DEFAULTS["techo"], 500)
cfg["meses"] = st.sidebar.number_input("Horizonte (meses)", 1, 24, M.DEFAULTS["meses"])
cfg["rural"] = st.sidebar.slider("Recargo logístico rural (%)", 0, 100, M.DEFAULTS["rural"], 5)
cfg["boost"] = st.sidebar.slider("Refuerzo de equidad al potencial (%)", 0, 200, M.DEFAULTS["boost"], 10)

st.sidebar.caption("Pesos del índice de prioridad (suman ~100)")
pz = M.DEFAULTS["pesos"]
cfg["pesos"] = {
    "gravedad": st.sidebar.slider("Gravedad", 0, 100, pz["gravedad"]),
    "densidad": st.sidebar.slider("Densidad", 0, 100, pz["densidad"]),
    "lejania": st.sidebar.slider("Lejanía (ruralidad)", 0, 100, pz["lejania"]),
    "vivienda": st.sidebar.slider("Vivienda precaria", 0, 100, pz["vivienda"]),
    "estrato": st.sidebar.slider("Estrato (proxy)", 0, 100, pz["estrato"]),
}
st.sidebar.caption("% de población damnificada por nivel de severidad")
cfg["tier"] = {
    "critica": st.sidebar.number_input("Crítico", 0, 100, M.DEFAULTS["tier"]["critica"]),
    "alta": st.sidebar.number_input("Alto", 0, 100, M.DEFAULTS["tier"]["alta"]),
    "media": st.sidebar.number_input("Medio", 0, 100, M.DEFAULTS["tier"]["media"]),
    "baja": st.sidebar.number_input("Bajo", 0, 100, M.DEFAULTS["tier"]["baja"]),
    "sindato": st.sidebar.number_input("Sin dato", 0, 100, M.DEFAULTS["tier"]["sindato"]),
    "ninguna": 0,
}
cfg["usd_cop"] = st.sidebar.number_input("Tasa USD→COP (anuncios)", 1000, 10000, M.DEFAULTS["usd_cop"], 100)

OVR = st.session_state.override
EST = st.session_state.estrato

def prio(m):
    return M.prioridad(m, MUNS, cfg["pesos"], EST)

# ---------------------------------------------------------------- encabezado
st.markdown(
    "<h1 style='text-align:center;font-size:52px;margin-bottom:0;"
    "font-family:Georgia,serif;color:#0E2A5E'>Colombia Invisible-Invencible</h1>"
    "<p style='text-align:center;letter-spacing:.14em;text-transform:uppercase;"
    "font-size:13px;color:#8A6A10;font-weight:700;margin-top:4px'>"
    "Veeduría de recursos y potencial de donación · Sismo 10 ago 2026</p>",
    unsafe_allow_html=True)
st.markdown(
    "<p style='text-align:center;max-width:900px;margin:8px auto 4px;color:#44505c;font-size:16px'>"
    "Una veeduría ciudadana que hace <b>visible lo invisible</b>: no solo cuánto se ha donado y a dónde, "
    "sino <b>cuánto debería llegar a cada municipio</b> según la gravedad del sismo, su densidad y la "
    "vulnerabilidad de su gente. Aquí los municipios pequeños, remotos y con vivienda precaria no se pierden "
    "entre las cifras de las grandes ciudades: <b>se priorizan</b>, para que la ayuda llegue donde más duele y "
    "<b>nadie quede olvidado</b>. Porque un país que ve a todos sus territorios se vuelve <b>invencible</b>.</p>",
    unsafe_allow_html=True)

# ---------------------------------------------------------------- KPIs
tot_pot = sum(M.potencial(m, cfg, OVR, EST) for m in MUNS)
tot_pob = sum(M.pob_objetivo(m, cfg, OVR) for m in MUNS)
n_alta = sum(1 for m in MUNS if m["sev"] != "ninguna" and prio(m) >= 70)
donado = sum(a["monto"] for a in st.session_state.aportes if a.get("clase") == "dinero" and a.get("verificado"))
k1, k2, k3, k4 = st.columns(4)
k1.metric("Población objetivo", f"{tot_pob:,}".replace(",", "."))
k2.metric(f"Potencial de donación ({cfg['meses']} m)", M.abrev(tot_pot))
k3.metric("Prioridad muy alta", f"{n_alta} municipios")
k4.metric("Ya donado (veeduría)", M.abrev(donado),
          f"{round(donado/tot_pot*100) if tot_pot else 0}% del potencial")

TABS = st.tabs(["🗺️ Mapa y prioridad", "💧 Índice de donación",
                "🏛️ Aportes oficiales", "📝 Registrar donativo",
                "🏥 Centros y hospitales", "⚖️ Trámites y ayuda", "📚 Fuentes"])

# ================================================================ TAB 1: MAPA
with TABS[0]:
    modo = st.radio("Colorear el mapa por",
                    ["Potencial de donativo", "Prioridad (equidad, sin población)"],
                    horizontal=True,
                    help="El potencial combina población objetivo, afectación reportada y vulnerabilidad. "
                         "La prioridad es independiente de la población para no invisibilizar municipios pequeños.")
    por_pot = modo.startswith("Potencial")
    st.caption(("Color = **nivel de potencial de donativo** (población objetivo × afectación reportada × "
                "vulnerabilidad), en 5 niveles. " if por_pot else
                "Color = **índice de prioridad** (gravedad + densidad + lejanía + vivienda + estrato). ")
               + "Borde grueso = severidad crítica. Marcadores = acopios, hospitales y albergues.")
    thr = M.pot_niveles(MUNS, cfg, OVR, EST)
    colmap, coldet = st.columns([1.4, 1])

    with colmap:
        fmap = folium.Map(location=[4.9, -75.9], zoom_start=7, tiles="cartodbpositron")
        feats = []
        for f in GEO["features"]:
            props = f["properties"]
            potv = M.potencial(props, cfg, OVR, EST)
            p = prio(props)
            if por_pot:
                niv = M.nivel_potencial(props, potv, thr)
                nivnom = M.POTNOM.get(niv, "—")
            else:
                niv = M.nivel_pri(props, p)
                nivnom = M.PRINOM.get(niv, "—")
            f2 = {"type": "Feature", "geometry": f["geometry"],
                  "properties": dict(props, prio=(p if props["sev"] != "ninguna" else 0),
                                     potstr=M.abrev(potv), nivelnom=nivnom, col=M.PRICOL[niv])}
            feats.append(f2)
        gj = {"type": "FeatureCollection", "features": feats}
        folium.GeoJson(
            gj,
            style_function=lambda x: {"fillColor": x["properties"]["col"], "color": "#ffffff",
                                      "weight": 2 if x["properties"]["sev"] == "critica" else 0.6,
                                      "fillOpacity": 0.82},
            highlight_function=lambda x: {"weight": 3, "fillOpacity": 0.95},
            tooltip=folium.GeoJsonTooltip(
                fields=["mun", "dep", "nivelnom", "potstr", "prio"],
                aliases=["Municipio", "Depto",
                         "Nivel potencial" if por_pot else "Nivel", "Potencial", "Prioridad"]),
        ).add_to(fmap)

        iconos = {"acopio": ("blue", "box"), "hospital": ("red", "plus-sign"), "albergue": ("green", "home")}
        for c in DATA["centros"]:
            color, ic = iconos.get(c["tipo"], ("gray", "info-sign"))
            pop = (f"<b>{c['nombre']}</b><br>{c['categoria']}<br>{c['direccion']}<br>"
                   f"{('📞 '+c['telefono']+'<br>') if c['telefono'] else ''}"
                   f"{('🕑 '+c['horario']+'<br>') if c['horario'] else ''}"
                   f"{c['servicios'][:120]}<br><a href='{c['gmaps']}' target='_blank'>Google Maps</a>")
            folium.Marker([c["lat"], c["lng"]], tooltip=c["nombre"],
                          popup=folium.Popup(pop, max_width=280),
                          icon=folium.Icon(color=color, icon=ic)).add_to(fmap)
        st_folium(fmap, height=560, use_container_width=True)
        nom = M.POTNOM if por_pot else M.PRINOM
        claves = ["muyalta", "alta", "media", "baja", "minima"] + (["sindato"] if por_pot else [])
        chips = "".join(
            f"<span style='display:inline-block;margin:2px 10px 2px 0'>"
            f"<span style='display:inline-block;width:12px;height:12px;border-radius:3px;"
            f"background:{M.PRICOL[k]};vertical-align:middle'></span> {nom[k]}</span>"
            for k in claves)
        st.markdown(("**Nivel de potencial de donativo:** " if por_pot else "**Nivel de prioridad:** ") + chips,
                    unsafe_allow_html=True)
        st.caption("🔵 Acopio  🔴 Hospital / sangre  🟢 Albergue")

    with coldet:
        nombres = sorted([f"{m['mun']} · {m['dep']}" for m in MUNS])
        sel = st.selectbox("Municipio", nombres, index=nombres.index(
            next(f"{m['mun']} · {m['dep']}" for m in MUNS if m["mun"] == "Quibdó")))
        m = next(x for x in MUNS if f"{x['mun']} · {x['dep']}" == sel)
        p = prio(m); idx = M.indice_donacion(m, cfg, OVR, EST)
        potv = idx["total"]; nivp = M.nivel_potencial(m, potv, thr)
        st.markdown(f"### {m['mun']} "
                    f"<span style='background:{M.PRICOL[nivp]};color:#fff;padding:2px 10px;"
                    f"border-radius:20px;font-size:13px'>Potencial {M.POTNOM.get(nivp, M.SEVNOM[m['sev']])}</span>",
                    unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        c1.metric("Nivel potencial", M.POTNOM.get(nivp, "—"))
        c2.metric("Índice donación", M.abrev(idx["total"]))
        c3.metric("Prioridad", f"{p if m['sev']!='ninguna' else 0}/100")
        st.markdown("**Índice de donación en dinero** (canasta familiar · higiene · techo, "
                    f"{cfg['meses']} meses):")
        st.markdown(f"- 🍚 Canasta familiar: **{M.cop(idx['canasta'])}**\n"
                    f"- 🚿 Higiene: **{M.cop(idx['higiene'])}**\n"
                    f"- 🏠 Techo: **{M.cop(idx['techo'])}**\n"
                    f"- Por familia/día (~{m['ppv']} pers.): **{M.cop(idx['por_familia_dia'])}**")
        with st.expander("Datos del municipio"):
            st.write(f"Población 2024 (DANE): **{m['pob2024']:,}**".replace(",", "."))
            st.write(f"Densidad: **{m.get('dens','—')} hab/km²** · Rural: "
                     f"**{round(M._rural_share(m)*100)}%** · Vivienda precaria: "
                     f"**{round((m.get('vivPrec') or 0)*100)}%**")
        st.markdown("#### Centros más cercanos")
        for tipo, etq in [("acopio", "🔵 Acopio"), ("hospital", "🔴 Hospital / sangre"), ("albergue", "🟢 Albergue")]:
            c = M.mas_cercano(m, DATA["centros"], tipo)
            if not c:
                st.write(f"{etq}: sin registro en la base.")
                continue
            st.markdown(f"**{etq} — {c['nombre']}** · ~{c['dist_km']} km")
            st.caption(f"{c['direccion']}"
                       + (f" · 📞 {c['telefono']}" if c["telefono"] else "")
                       + (f" · 🕑 {c['horario']}" if c["horario"] else "")
                       + (f" · [Maps]({c['gmaps']})" if c["gmaps"] else ""))
        st.caption("Distancias aproximadas desde el punto de referencia del municipio.")

# ================================================================ TAB 2: ÍNDICE
with TABS[1]:
    st.subheader("Índice de donación por municipio")
    st.caption("En dinero, según canasta familiar, higiene y techo. Puedes editar el «% damnificados» "
               "por municipio; la población y demás datos son del DANE.")
    fdep = st.selectbox("Departamento", ["Todos"] + DEPS, key="idxdep")
    rows = []
    for m in MUNS:
        if fdep != "Todos" and m["dep"] != fdep:
            continue
        idx = M.indice_donacion(m, cfg, OVR, EST)
        rows.append({
            "Municipio": m["mun"], "Depto": m["dep"], "Nivel": M.SEVNOM[m["sev"]],
            "Prioridad": prio(m) if m["sev"] != "ninguna" else 0,
            "Pob. 2024": m["pob2024"],
            "% dam.": OVR.get(m["code"], cfg["tier"].get(m["sev"], 0)),
            "Pob. objetivo": idx["pob_objetivo"],
            "Canasta": idx["canasta"], "Higiene": idx["higiene"], "Techo": idx["techo"],
            "Índice donación": idx["total"], "code": m["code"],
        })
    df = pd.DataFrame(rows).sort_values(["Depto", "Prioridad"], ascending=[True, False])
    edited = st.data_editor(
        df.drop(columns=["code"]),
        disabled=[c for c in df.columns if c not in ("% dam.",)],
        hide_index=True, use_container_width=True, height=460,
        column_config={
            "Índice donación": st.column_config.NumberColumn(format="$%d"),
            "Canasta": st.column_config.NumberColumn(format="$%d"),
            "Higiene": st.column_config.NumberColumn(format="$%d"),
            "Techo": st.column_config.NumberColumn(format="$%d"),
        })
    # aplicar ediciones de % dam.
    for i, row in edited.iterrows():
        code = df.iloc[i]["code"]
        base = cfg["tier"].get(BYCODE[code]["sev"], 0)
        if row["% dam."] != base:
            OVR[code] = row["% dam."]
    st.caption("La referencia humanitaria (Esfera): ~2.100 kcal y ~15 L de agua por persona/día. "
               "Los valores en COP son de planeación, calibrables en la barra lateral.")

# ================================================================ TAB 3: APORTES OFICIALES
def badge(nombre):
    ini = "".join([w[0] for w in nombre.split()[:2]]).upper()
    col = "#" + hashlib.md5(nombre.encode()).hexdigest()[:6]
    return (f"<span style='display:inline-flex;align-items:center;justify-content:center;"
            f"width:38px;height:38px;border-radius:9px;background:{col};color:#fff;"
            f"font-weight:700;font-family:sans-serif;font-size:13px'>{ini}</span>")

with TABS[2]:
    st.subheader("Aportes oficiales anunciados")
    st.caption("Según lo anunciado públicamente por cada entidad. Los montos en especie no se cuantifican; "
               "los valores en USD se convierten con la tasa referencial de la barra lateral.")
    total_cop = 0
    for a in DATA["anuncios"]:
        nombre = str(a.get("Entidad / Donante", ""))
        montotxt = str(a.get("Monto / Recurso Anunciado", ""))
        cop_val, especie = M.parse_monto_cop(montotxt, cfg["usd_cop"])
        if cop_val:
            total_cop += cop_val
        c1, c2, c3 = st.columns([0.6, 3, 2])
        c1.markdown(badge(nombre), unsafe_allow_html=True)
        c2.markdown(f"**{nombre}**  \n<span style='color:#5B6472;font-size:12px'>"
                    f"{a.get('Tipo de Entidad','')}</span>", unsafe_allow_html=True)
        val = "En especie" if especie else M.cop(cop_val)
        c3.markdown(f"**{val}**  \n<span style='color:#5B6472;font-size:12px'>{montotxt}</span>",
                    unsafe_allow_html=True)
        st.caption(str(a.get("Destino y Alcance del Aporte", "")))
        st.divider()
    st.metric("Total anunciado en dinero (aprox.)", M.abrev(total_cop),
              help="Suma de aportes cuantificables; excluye aportes en especie. Cifras según lo anunciado.")

    st.subheader("Canales certificados para donar")
    st.caption("⚠️ Verifica siempre en la página oficial de cada organización antes de transferir. "
               "Estos canales fueron marcados como verificados en la base aportada.")
    cu = pd.DataFrame(DATA["cuentas"])
    st.dataframe(cu, hide_index=True, use_container_width=True)

# ================================================================ TAB 4: REGISTRAR DONATIVO
with TABS[3]:
    st.subheader("Registrar un donativo")
    st.caption("Organismos públicos y privados registran a qué municipio dirigieron su ayuda, en dinero o en especie.")
    with st.form("reg"):
        c1, c2 = st.columns(2)
        org = c1.text_input("Organismo donante")
        tipo = c2.selectbox("Tipo", ["privado", "público"])
        dep = c1.selectbox("Departamento", DEPS)
        muni = c2.selectbox("Municipio", sorted([m["mun"] for m in MUNS if m["dep"] == dep]))
        clase = c1.radio("Clase de aporte", ["dinero", "especie"], horizontal=True)
        monto = c2.number_input("Monto (COP) — si es dinero", 0, step=100000)
        categoria = c1.selectbox("Categoría (si es especie)", ["—"] + M.ESPECIE)
        cantidad = c2.text_input("Cantidad / unidad (si es especie)", placeholder="Ej. 500 mercados, 2 camiones")
        familia = c1.text_input("Barrio / familia (opcional)")
        evidencia = c2.text_input("Enlace a evidencia (opcional)")
        verificado = st.checkbox("Aporte verificado (solo estos cuentan como donado)")
        enviar = st.form_submit_button("Registrar aporte")
    if enviar:
        if not org or (clase == "dinero" and monto <= 0) or (clase == "especie" and categoria == "—"):
            st.error("Falta el organismo, o el monto (dinero) / la categoría (especie).")
        else:
            code = next(m["code"] for m in MUNS if m["mun"] == muni and m["dep"] == dep)
            st.session_state.aportes.append({
                "fecha": dt.date.today().isoformat(), "org": org, "tipo": tipo,
                "code": code, "municipio": muni, "dep": dep, "clase": clase,
                "monto": int(monto) if clase == "dinero" else 0,
                "categoria": categoria if clase == "especie" else "",
                "cantidad": cantidad if clase == "especie" else "",
                "familia": familia, "evidencia": evidencia, "verificado": verificado})
            st.success("Aporte registrado.")

    if st.session_state.aportes:
        dfa = pd.DataFrame(st.session_state.aportes)
        st.dataframe(dfa, hide_index=True, use_container_width=True)
        buff = io.StringIO(); dfa.to_csv(buff, index=False)
        st.download_button("Descargar CSV", buff.getvalue(), "aportes.csv", "text/csv")
    else:
        st.info("Aún no hay aportes registrados. El primero define la línea base de la veeduría.")
    st.caption("Prototipo: los aportes viven en tu sesión. Para una veeduría pública multiusuario, "
               "conecta una base de datos (Supabase/Firebase/Postgres).")

# ================================================================ TAB 5: CENTROS
with TABS[4]:
    st.subheader("Directorio de centros de acopio, hospitales, bancos de sangre y albergues")
    tsel = st.radio("Filtrar por tipo", ["Todos", "acopio", "hospital", "albergue"], horizontal=True)
    filas = [c for c in DATA["centros"] if tsel == "Todos" or c["tipo"] == tsel]
    dfc = pd.DataFrame([{
        "Tipo": c["tipo"], "Nombre": c["nombre"], "Ciudad": c["ciudad"],
        "Dirección": c["direccion"], "Nivel": c["nivel"], "Teléfono": c["telefono"],
        "Horario": c["horario"], "Servicios / requerimientos": c["servicios"],
        "Web": c["web"], "Maps": c["gmaps"],
    } for c in filas])
    st.dataframe(dfc, hide_index=True, use_container_width=True,
                 column_config={"Web": st.column_config.LinkColumn(),
                                "Maps": st.column_config.LinkColumn()})

# ================================================================ TAB 6: TRÁMITES
with TABS[5]:
    st.subheader("Trámites y ayuda para personas afectadas")
    colA, colB = st.columns(2)
    with colA:
        st.markdown("#### Pérdida o daño de vivienda")
        st.markdown(
            "1. **Que te censen:** pide al CMGRD / alcaldía que registre tu vivienda en el censo de "
            "damnificados (evaluación EDAN) y guarda la constancia.\n"
            "2. **Registro Único de Damnificados (RUD):** verifica que quedes inscrito; es la puerta a la ayuda.\n"
            "3. **Reúne pruebas:** fotos, recibos a tu nombre, testigos, constancia del CMGRD.\n"
            "4. **Si no te incluyen:** radica un derecho de petición (generador abajo). La Personería asesora gratis.")
        st.markdown("#### Fallecimiento de un familiar")
        st.markdown(
            "1. **Certificado de defunción** (IPS / Medicina Legal).\n"
            "2. **Registro civil de defunción** en Registraduría o notaría.\n"
            "3. **Apoyo funerario / humanitario:** consúltalo con alcaldía / UNGRD.\n"
            "4. **Menores a cargo:** el ICBF activa protección.")
    with colB:
        st.markdown("#### Entidades que ayudan")
        st.markdown(
            "- **Línea 123** — emergencias\n- **Alcaldía / CMGRD** — censo y ayuda local\n"
            "- **UNGRD** — coordinación nacional y RUD\n- **Cruz Roja / Defensa Civil** — socorro\n"
            "- **Personería / Defensoría** — asesoría legal gratuita\n- **ICBF** — niñez y familia")
        st.info("Guía informativa; no reemplaza asesoría legal. Datos locales (acopio, contactos) "
                "confírmalos con la alcaldía.")

    st.markdown("#### Generador de derecho de petición")
    with st.form("dp"):
        c1, c2 = st.columns(2)
        nombre = c1.text_input("Nombre completo")
        cedula = c2.text_input("Cédula")
        munsel = c1.selectbox("Municipio", sorted([m["mun"] for m in MUNS]))
        motivo = c2.selectbox("Motivo", ["Inclusión en damnificados / ayuda por vivienda",
                                         "Entrega de ayuda humanitaria",
                                         "Apoyo funerario por fallecimiento"])
        direc = c1.text_input("Dirección de la vivienda afectada")
        notif = c2.text_input("Datos de notificación (correo/teléfono)")
        hechos = st.text_area("Hechos (breve relato)")
        gen = st.form_submit_button("Generar texto")
    if gen:
        hoy = dt.date.today().strftime("%d/%m/%Y")
        texto = f"""Señores
ALCALDÍA MUNICIPAL DE {munsel.upper()} / UNGRD
E. S. D.

{munsel}, {hoy}

Asunto: Derecho de petición (Art. 23 C.P. y Ley 1755 de 2015)

Yo, {nombre or '[NOMBRE]'}, identificado(a) con cédula No. {cedula or '[CÉDULA]'}, residente en \
{direc or '[DIRECCIÓN]'}, {munsel}, en ejercicio del derecho fundamental de petición (Art. 23 C.P. \
y Ley 1755 de 2015), respetuosamente solicito: {motivo.lower()} por la afectación derivada del sismo \
del 10 de agosto de 2026.

HECHOS
{hechos or '[Relato de los hechos]'}

FUNDAMENTOS
La entidad debe resolver de fondo dentro de los quince (15) días hábiles siguientes. Si no fuere \
posible, deberá informarlo antes del vencimiento, con los motivos y el nuevo plazo.

NOTIFICACIONES
{notif or '[Correo / teléfono / dirección]'}

Atentamente,

_____________________________
{nombre or '[NOMBRE]'}  ·  C.C. {cedula or '[CÉDULA]'}

— Plantilla informativa, no constituye asesoría jurídica. Apoyo gratuito: Personería / Defensoría del Pueblo. —"""
        st.text_area("Texto generado (cópialo o descárgalo)", texto, height=340)
        st.download_button("Descargar .txt", texto, "derecho-de-peticion.txt")

# ================================================================ TAB 7: FUENTES
with TABS[6]:
    st.subheader("Fuentes de datos")
    st.markdown(
        "- **OPS/UNGRD SITREP 3 y Asocapitales** (11 ago 2026) → severidad y víctimas por departamento.\n"
        "- **DANE** → población proyectada 2024, densidad, CNPV 2018 (viviendas y precariedad), GEIH 2024 "
        "(personas por hogar), MGN 2022 (geometría de municipios).\n"
        "- **Excel aportado** → red hospitalaria y bancos de sangre, centros de acopio y albergues "
        "georreferenciados, canales certificados y anuncios oficiales.\n"
        "- **UNGRD · Registro Único de Damnificados** → *en consolidación*; reemplazará los % por nivel.")
    st.caption("Los índices de canasta, higiene y techo, los pesos de prioridad y el % de damnificados son "
               "supuestos calibrables (referencia Esfera), no cifras oficiales. Los trámites se basan en "
               "mecanismos legales reales de Colombia (Ley 1755 de 2015).")
