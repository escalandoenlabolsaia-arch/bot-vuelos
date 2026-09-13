import csv, os, statistics, time
from datetime import date, datetime, timedelta
from urllib.parse import quote

import requests
from fast_flights import FlightData, Passengers, get_flights

try:
    from fast_flights import url_create   # arma link de Google Flights con la búsqueda ya cargada
except ImportError:
    url_create = None

# --- Búsqueda ---
ORIGENES = ["EZE"]                       # solo Ezeiza
DESTINOS = {"Europa": ["FCO", "TRN"]}    # Roma Fiumicino, Turín
SOLO_DIRECTOS = True                     # descarta vuelos con escalas

FECHA_DESDE = date(2027, 4, 1)           # ventana fija de salidas
FECHA_HASTA = date(2027, 5, 30)

# --- Umbral de oferta ---
# Alerta si el pasaje cuesta MENOS del 35% de su valor habitual
# (ej.: habitual ~USD 600 -> alerta por debajo de ~USD 210).
# Si querés ser menos exigente, subilo (50 = la mitad del habitual).
UMBRAL = 35
MIN_DATOS = 20      # registros históricos mínimos de una ruta antes de alertar

HIST = "historico.csv"
ENVIADAS = "ofertas_enviadas.csv"
DEBUG = os.environ.get("DEBUG_VUELOS") == "1"


def a_numero(t):
    return int("".join(c for c in t if c.isdigit()))


def crear_csv_si_falta(archivo, cabecera):
    if not os.path.exists(archivo):
        with open(archivo, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(cabecera)


def leer_filas(archivo):
    with open(archivo, newline="", encoding="utf-8") as f:
        filas = list(csv.reader(f))
    return filas[1:] if filas else []


def agregar_fila(archivo, fila):
    with open(archivo, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(fila)


def link_google(origen, destino, fecha_txt):
    """Link con la búsqueda precargada (ruta + fecha + ida + economía).
    Entra directo a la lista de resultados, sin configurar nada."""
    if url_create:
        try:
            return url_create(
                flights=[FlightData(date=fecha_txt, from_airport=origen, to_airport=destino)],
                trip="one-way", seat="economy",
                passengers=Passengers(adults=1),
            )
        except Exception:
            pass
    return "https://www.google.com/travel/flights?q=" + quote(
        f"vuelos de {origen} a {destino} el {fecha_txt} solo ida")


def enviar_ntfy(texto):
    MAX_CARACTERES = 3800
    partes, resto = [], texto
    while len(resto) > MAX_CARACTERES:
        corte = resto.rfind("\n", 0, MAX_CARACTERES)
        if corte == -1:
            corte = MAX_CARACTERES
        partes.append(resto[:corte])
        resto = resto[corte:].lstrip("\n")
    if resto:
        partes.append(resto)

    fallas = 0
    for i, parte in enumerate(partes, start=1):
        enviado = False
        for intento in range(3):
            try:
                r = requests.post(
                    f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}",
                    data=parte.replace("\n", "  \n").encode("utf-8"),
                    headers={
                        "Title": "Ofertas de vuelos",
                        "Priority": "high",
                        "Tags": "airplane",
                        "Markdown": "yes",
                    },
                    timeout=30,
                )
                r.raise_for_status()
                enviado = True
                break
            except Exception as e:
                print(f"  intento {intento + 1} falló (parte {i}): {e}")
                time.sleep(5)
        print(f"Parte {i}/{len(partes)}: {'enviada ✅' if enviado else 'FALLÓ ❌'}")
        if not enviado:
            fallas += 1
        time.sleep(2)

    if fallas:
        raise RuntimeError(f"{fallas} parte(s) no pudieron enviarse")


# --- Preparar archivos y memoria ---
crear_csv_si_falta(HIST, ["fecha_consulta", "origen", "destino", "region", "fecha_vuelo", "precio_min"])
crear_csv_si_falta(ENVIADAS, ["clave", "fecha_aviso"])

rutas_nuestras = {(o, d) for o in ORIGENES for _, cods in DESTINOS.items() for d in cods}

precios_pasados = {}   # {(origen, destino): [precios]}
for fila in leer_filas(HIST):
    try:
        precios_pasados.setdefault((fila[1], fila[2]), []).append(int(fila[5]))
    except (ValueError, IndexError):
        pass

tenemos_historial = any(clave in precios_pasados for clave in rutas_nuestras)
ofertas_ya_enviadas = {fila[0] for fila in leer_filas(ENVIADAS)}

hoy = date.today()
fechas = [FECHA_DESDE + timedelta(days=i)
          for i in range((FECHA_HASTA - FECHA_DESDE).days + 1)]

ofertas = []
busquedas_ok = 0
minimos_hoy = []
total_busquedas = len(ORIGENES) * sum(len(c) for c in DESTINOS.values()) * len(fechas)

for origen in ORIGENES:
    for region, codigos in DESTINOS.items():
        for destino in codigos:
            for fecha in fechas:
                fecha_txt = fecha.isoformat()
                try:
                    res = get_flights(
                        flight_data=[FlightData(date=fecha_txt, from_airport=origen, to_airport=destino)],
                        trip="one-way", seat="economy",
                        passengers=Passengers(adults=1),
                        fetch_mode="fallback",
                    )
                except Exception as e:
                    print(f"{origen}->{destino} {fecha_txt}: error ({e})")
                    time.sleep(3)
                    continue

                mejor = None
                for v in res.flights:
                    if not v.price:
                        continue
                    escala = getattr(v, "stops", None)
                    if SOLO_DIRECTOS and escala is not None and "nonstop" not in str(escala).lower():
                        continue
                    try:
                        precio = a_numero(v.price)
                    except ValueError:
                        continue
                    if mejor is None or precio < mejor["precio"]:
                        mejor = {
                            "precio": precio,
                            "aerolinea": getattr(v, "name", "") or "",
                            "sale": getattr(v, "departure", "") or "",
                            "llega": getattr(v, "arrival", "") or "",
                            "escala": str(escala) if escala is not None else "",
                            "duracion": getattr(v, "duration", "") or "",
                        }

                if mejor:
                    busquedas_ok += 1
                    agregar_fila(HIST, [datetime.now(), origen, destino, region, fecha_txt, mejor["precio"]])
                    minimos_hoy.append((mejor["precio"], f"{origen}->{destino} {fecha_txt}"))

                    base = precios_pasados.get((origen, destino), [])
                    if len(base) >= MIN_DATOS:
                        mediana = round(statistics.median(base))
                        if mediana > 0 and mejor["precio"] < mediana * (UMBRAL / 100):
                            descuento = round((1 - mejor["precio"] / mediana) * 100)
                            nivel = (descuento // 10) * 10
                            clave = f"{origen}-{destino}-{fecha_txt}-{nivel}"
                            if clave not in ofertas_ya_enviadas:
                                mejor.update({
                                    "origen": origen, "destino": destino, "region": region,
                                    "fecha": fecha_txt, "mediana": mediana,
                                    "descuento": descuento, "clave": clave,
                                    "link": link_google(origen, destino, fecha_txt),
                                })
                                ofertas.append(mejor)
                                print(f"OFERTA: {origen}->{destino} {fecha_txt} "
                                      f"USD {mejor['precio']} (habitual ~{mediana}, -{descuento}%)")
                time.sleep(2)  # pausa para no saturar a Google

print(f"Búsquedas con datos: {busquedas_ok}/{total_busquedas}")
for precio, ruta in sorted(minimos_hoy)[:5]:
    print(f"  mínimo {ruta}: USD {precio}")

# --- Envío: UN solo mensaje con todas las ofertas ---
if ofertas:
    ofertas.sort(key=lambda o: -o["descuento"])
    lineas = [f"✈️ **Ofertas ida EZE — salidas {FECHA_DESDE.strftime('%d/%m')} a {FECHA_HASTA.strftime('%d/%m/%Y')}**", ""]
    for o in ofertas:
        lineas.append(f"🟢 **EZE → {o['destino']} — {o['fecha']}**")
        lineas.append(f"USD {o['precio']} (habitual ~USD {o['mediana']} · **−{o['descuento']}%**)")
        detalle = [p for p in [o["aerolinea"], o["sale"] and f"sale {o['sale']}",
                               o["escala"], o["duracion"]] if p]
        if detalle:
            lineas.append(" · ".join(detalle))
        lineas.append(f"[Ver en Google Flights]({o['link']})")
        lineas.append("")
    enviar_ntfy("\n".join(lineas))
    for o in ofertas:
        agregar_fila(ENVIADAS, [o["clave"], datetime.now()])
    print(f"Enviadas {len(ofertas)} ofertas en un solo mensaje ✅")
elif not tenemos_historial:
    # Primer corrida con el historial vacío para estas rutas: confirmamos que el bot funciona
    enviar_ntfy(
        "✈️ **Bot de vuelos activo**\n\n"
        f"Primera corrida: histórico inicial creado ({busquedas_ok} rutas/fechas registradas).\n"
        f"Ventana: {FECHA_DESDE.strftime('%d/%m/%Y')} a {FECHA_HASTA.strftime('%d/%m/%Y')} · "
        "solo directos EZE → FCO/TRN · ida.\n\n"
        "A partir de la próxima corrida aviso ofertas bajo el umbral. Silencio = sin ofertas."
    )
elif DEBUG:
    enviar_ntfy(
        f"🔍 DEBUG vuelos: {busquedas_ok}/{total_busquedas} búsquedas con datos. "
        "Sin ofertas bajo el umbral hoy."
    )
else:
    print("Sin ofertas bajo el umbral. No se envía nada (silencio).")
