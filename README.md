# Colombia Invisible-Invencible

Veeduría ciudadana de recursos donados y **potencial de donación** por municipio,
para la respuesta al sismo de San José del Palmar (Chocó, M 7,4 · 10 ago 2026).
Prioriza los municipios pequeños, remotos y vulnerables para que **nadie quede olvidado**.

## Qué hace
- **Mapa de prioridad**: colorea los 98 municipios de Chocó, Risaralda, Quindío y Valle
  según un índice que combina gravedad reportada, densidad, lejanía, vivienda precaria y estrato.
- **Índice de donación en dinero** por municipio, según canasta familiar, higiene y techo.
- **Centros en el mapa**: acopios, hospitales/bancos de sangre y albergues, con el más cercano
  a cada municipio y toda su información de contacto.
- **Aportes oficiales**: montos anunciados por empresa y canales certificados para donar.
- **Registro de donativos** en dinero o en especie (alimentos, agua, medicamentos, higiene,
  ropa, mano de obra, transporte, maquinaria, protección).
- **Trámites**: guía y generador de derecho de petición (Ley 1755 de 2015).

## Ejecutar localmente
```bash
pip install -r requirements.txt
streamlit run app.py
```
Se abre en http://localhost:8501

## Publicar gratis (Streamlit Community Cloud)
1. Sube esta carpeta a un repositorio de GitHub (incluye la carpeta `data/`).
2. Entra a https://share.streamlit.io , conecta el repo y elige `app.py`.
3. Queda con URL pública. (Para veeduría multiusuario real, conecta una base de datos.)

## Estructura
- `app.py` — interfaz (Streamlit).
- `model.py` — datos y modelo (prioridad, índice de donación, centro más cercano).
- `data/municipios.geojson` — municipios + datos DANE (población, densidad, vivienda, severidad).
- `data/donaciones.xlsx` — centros, cuentas y anuncios aportados.

## Notas de transparencia
Los valores de canasta/higiene/techo, los pesos de prioridad y el % de damnificados son
supuestos **calibrables** (referencia Esfera), no cifras oficiales. Cuando salga el Registro
Único de Damnificados de la UNGRD, reemplaza los % por nivel. Verifica los canales de donación
en la web oficial de cada organización antes de transferir.
