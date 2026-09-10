import csv, os, statistics, time
from datetime import datetime, timedelta
from urllib.parse import quote
import requests
from fast_flights import FlightData, Passengers, get_flights

# --- Claves CallMeBot (GitHub las inyecta desde Secrets) ---
TELEFONO = os.environ["CALLMEBOT_PHONE"]
APIKEY = os.environ["CALLMEBOT_APIKEY"]

# --- Configuración ---
ORIGENES = ["BUE", "NYC"]  # códigos de ciudad: cubren todos los aeropuertos (Ezeiza+Aeroparque, JFK+Newark+LaGuardia)

DESTINOS = {
    "América": ["NYC", "BUE", "MIA", "CUN", "PUJ", "RIO", "GRU", "SCL", "LIM", "BOG", "MEX"],
    "Europa":  ["MAD", "BCN", "LIS", "ROM", "PAR", "LON", "AMS", "FRA", "IST", "ATH", "FCO"],
    "Asia":    ["DXB", "TLV", "DEL", "BKK", "SIN", "HKG", "TYO", "SEL"],
}

DIAS_ADELANTE = [30, 60, 90, 120]   # busca salidas a 1, 2, 3 y 4 meses
UMBRAL = 35        # % mínimo de baja para avisar
MIN_DATOS = 20     # registros históricos mínimos de una ruta antes de alertar
HIST = "historico.csv"
ENVIADAS = "ofertas_enviadas.csv"

def avisar(texto):
    requests.get("https://api.callmebot.com/whatsapp.php",
                 params={"phone": TELEFONO, "text": texto, "apikey": APIKEY}, timeout=30)

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

# --- Preparar archivos y memoria ---
crear_csv_si_falta(HIST, ["fecha_consulta", "origen", "destino", "region", "fecha_vuelo", "precio_min"])
crear_csv_si_falta(ENVIADAS, ["clave", "fecha_aviso"])

precios_pasados = {}   # {(origen, destino): [precios]}
for fila in leer_filas(HIST):
    try:
        precios_pasados.setdefault((fila[1], fila[2]), []).append(int(fila[5]))
    except (ValueError, IndexError):
        pass

ofertas_ya_enviadas = {fila[0] for fila in leer_filas(ENVIADAS)}

hoy = datetime.now().date()
fechas = [(hoy + timedelta(days=d)).isoformat() for d in DIAS_ADELANTE]
encontradas = 0

for origen in ORIGENES:
    for region, codigos in DESTINOS.items():
        for destino in codigos:
            if destino == origen:
                continue
            for fecha in fechas:
                try:
                    res = get_flights(
                        flight_data=[FlightData(date=fecha, from_airport=origen, to_airport=destino)],
                        trip="one-way", seat="economy",
                        passengers=Passengers(adults=1),
                        fetch_mode="fallback",
                    )
                except Exception as e:
                    print(f"{origen}->{destino} {fecha}: error ({e})")
                    time.sleep(3)
                    continue

                precios = []
                for v in res.flights:
                    if v.price:
                        try:
                            precios.append(a_numero(v.price))
                        except ValueError:
                            pass

                if precios:
                    minimo = min(precios)
                    agregar_fila(HIST, [datetime.now(), origen, destino, region, fecha, minimo])

                    base = precios_pasados.get((origen, destino), [])
                    if len(base) >= MIN_DATOS:
                        mediana = statistics.median(base)
                        if mediana > 0 and minimo < mediana * (1 - UMBRAL / 100):
                            descuento = round((1 - minimo / mediana) * 100)
                            nivel = (descuento // 10) * 10  # 20, 30, 40...
                            clave = f"{origen}-{destino}-{fecha}-{nivel}"
                            if clave not in ofertas_ya_enviadas:
                                link = "https://www.google.com/travel/flights?q=" + quote(
                                    f"vuelos de {origen} a {destino} {fecha}")
                                avisar(
                                    f"✈️ OFERTA -{nivel}% | {region}\n"
                                    f"{origen} → {destino}\n"
                                    f"Salida: {fecha} | USD {minimo}\n"
                                    f"Habitual: ~USD {round(mediana)}\n"
                                    f"{link}"
                                )
                                agregar_fila(ENVIADAS, [clave, datetime.now()])
                                ofertas_ya_enviadas.add(clave)
                                encontradas += 1
                                print(f"ALERTA: {origen}->{destino} {fecha} USD {minimo} (-{descuento}%)")
                time.sleep(2)  # pausa para no saturar

print(f"Terminado. Ofertas encontradas: {encontradas}")
