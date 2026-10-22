"""
Alertas de mínimos de 3, 6 y 12 meses y resumen semanal de la cartera.
Pensado para ejecutarse en GitHub Actions y avisar por Telegram.

Variables de entorno:
  TELEGRAM_TOKEN    token del bot (de @BotFather)
  TELEGRAM_CHAT_ID  id del chat donde enviar
  MODO              "alertas" (por defecto) o "resumen"
  DRY_RUN=true      imprime el mensaje en vez de enviarlo
"""
import html
import os
import warnings
from datetime import timedelta

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*inplace.*")

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
MODO = os.getenv("MODO", "alertas").lower()
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

# De mayor a menor: si es mínimo de 12M también lo es de 6M y 3M, se informa el mayor
VENTANAS = [("12 meses", 365), ("6 meses", 182), ("3 meses", 91)]
UMBRAL_CERCA = 0.02    # avisar si está a menos de un 2 % del mínimo de 12 meses
HORIZONTES = [("1 año", 365), ("2 años", 730), ("3 años", 1095)]
MAX_DIAS_SIN_DATO = 5  # descartar si el último dato es demasiado antiguo

# Cada activo: (nombre, posiciones que representa, ticker para alertas, ticker para resumen)
# - Alertas: ETF que cotiza en tiempo real, para detectar el mínimo el mismo día.
# - Resumen: tu fondo real (valor liquidativo diario); el domingo el retraso no importa.
ACTIVOS = [
    ("Developed World (MSCI World)", "Developed World S/D, MSCI World P, Amundi Core World, MyInvestor Indexado Global PP",
                                       "EUNL.DE",      "0P0001XF40.F"),
    ("Emerging Markets",              "iShares EM Index S, Index MSCI EM",
                                       "EUNM.DE",      "0P0001XF3Z.F"),
    ("S&P500 iShares clase S",         "iShares US Index S, iShares Core S&P 500",
                                       "SXR8.DE",      "0P0001XF42.F"),
    ("Nasdaq 100 (EUR hedged)",       "iShares Nasdaq 100 Hedged",
                                       "NQSE.DE",      "NQSE.DE"),
    ("Polar Capital Global Tech",     "Polar Capital Global Tech R EUR Hdg",
                                       "0P0001ANEI.F", "0P0001ANEI.F"),  # fondo activo: sin ETF homólogo
    ("Global Small-Cap",              "Vanguard Global Small-Cap Index",
                                       "IUSN.DE",      "0P00012I66.F"),
    ("FTSE All-World High Div",       "Vanguard FTSE All-World High Div Yield",
                                       "VHYL.AS",      "VHYL.AS"),
    ("Oro (USD)",                     "iShares Physical Gold ETC",
                                       "IGLN.L",       "IGLN.L"),
    ("Bitcoin",                       "Bitcoin, iShares Bitcoin ETP",
                                       "BTC-EUR",      "BTC-EUR"),
    ("Take-Two (USD)",                "Take-Two",
                                       "TTWO",         "TTWO"),
]


def cargar(ticker):
    hist = yf.Ticker(ticker).history(period="37mo", interval="1d", auto_adjust=True)["Close"].dropna()
    if hist.empty:
        raise ValueError("sin datos")
    ultima = hist.index[-1]
    ultima = ultima.tz_convert(None) if ultima.tzinfo is not None else ultima
    if (pd.Timestamp.now("UTC").tz_localize(None) - ultima).days > MAX_DIAS_SIN_DATO:
        raise ValueError("datos antiguos")
    return hist


def metricas(hist):
    fecha = hist.index[-1]
    precio = float(hist.iloc[-1])
    anual = hist[hist.index >= fecha - timedelta(days=365)]
    min12, max12 = float(anual.min()), float(anual.max())
    def variacion(dias):
        previo = hist[hist.index <= fecha - timedelta(days=dias)]
        return precio / float(previo.iloc[-1]) - 1 if not previo.empty else None

    rango = (precio - min12) / (max12 - min12) if max12 > min12 else 1.0

    # Distancia a máximo y mínimo a 1, 2 y 3 años (n/d si el histórico no llega)
    horizontes = []
    for etiqueta, dias in HORIZONTES:
        desde = fecha - timedelta(days=dias)
        if hist.index[0] > desde + timedelta(days=10):
            horizontes.append((etiqueta, None, None))
            continue
        tramo = hist[hist.index >= desde]
        horizontes.append((etiqueta, precio / float(tramo.max()) - 1, precio / float(tramo.min()) - 1))
    return {
        "fecha": fecha,
        "precio": precio,
        "min12": min12,
        "max12": max12,
        "vs_min": precio / min12 - 1,
        "vs_max": precio / max12 - 1,
        "var_sem": variacion(7),
        "var_mes": variacion(30),
        "rango": rango,
        "horizontes": horizontes,
    }


def nivel_minimo(hist, m):
    for etiqueta, dias in VENTANAS:
        ventana = hist[hist.index >= m["fecha"] - timedelta(days=dias)]
        if m["precio"] <= float(ventana.min()):
            return f"MÍNIMO de {etiqueta}"
    if m["precio"] <= m["min12"] * (1 + UMBRAL_CERCA):
        return f"a {m['vs_min']:.1%} del mínimo de 12 meses"
    return None


def barra(rango, ancho=16):
    llenos = round(max(0.0, min(1.0, rango)) * ancho)
    return "▓" * llenos + "░" * (ancho - llenos)


def enviar(texto):
    if DRY_RUN or not (TG_TOKEN and TG_CHAT):
        print("[DRY_RUN] Mensaje que se enviaría:\n" + texto)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()


def recoger():
    datos, avisos = [], []
    for nombre, cubre, t_alerta, t_resumen in ACTIVOS:
        ticker = t_resumen if MODO == "resumen" else t_alerta
        try:
            hist = cargar(ticker)
            datos.append((ticker, nombre, cubre, hist, metricas(hist)))
            print(f"{ticker:13} ok")
        except Exception as e:  # un ticker caído no debe tumbar el resto
            avisos.append(f"{ticker}: {e}")
            print(f"{ticker:13} ERROR {e}")
    return datos, avisos


def modo_alertas(datos, avisos):
    alertas = []
    for ticker, nombre, cubre, hist, m in datos:
        nivel = nivel_minimo(hist, m)
        if nivel:
            print(f"{ticker:13} ALERTA {nivel}")
            alertas.append(
                f"📉 <b>{html.escape(nombre)}</b> (<code>{ticker}</code>)\n"
                f"   {nivel} — precio {m['precio']:.2f} el {m['fecha']:%d/%m/%Y}\n"
                f"   Desde máximo 12M: {m['vs_max']:.1%}\n"
                f"   Afecta a: {html.escape(cubre)}"
            )
    if not alertas:
        print("Sin alertas hoy.")
        return
    texto = "<b>Oportunidades por mínimos</b>\n\n" + "\n\n".join(alertas)
    if avisos:
        texto += "\n\n⚠️ " + html.escape("; ".join(avisos))
    enviar(texto)


def modo_resumen(datos, avisos):
    # Ordenado por caída desde el máximo anual: lo más castigado, arriba
    datos = sorted(datos, key=lambda d: d[4]["vs_max"])
    bloques = []
    for ticker, nombre, _, _, m in datos:
        fmt = lambda v: f"{v:+.1%}" if v is not None else "n/d"
        bloques.append(
            f"<b>{html.escape(nombre)}</b> (<code>{ticker}</code>) {m['precio']:.2f}\n"
            f"   Semana: {fmt(m['var_sem'])}\n"
            f"   Mes: {fmt(m['var_mes'])}\n\n"
            + "".join(
                f"   {et}: máx {fmt(vmax)} · mín {fmt(vmin)}\n"
                for et, vmax, vmin in m["horizontes"]
                if vmax is not None  # sin histórico suficiente: no se muestra la línea
            )
            + f"   mín {barra(m['rango'])} máx"
        )
    texto = "<b>📊 Resumen semanal</b>\n<i>Ordenado de más a menos caída desde el máximo anual</i>\n\n"
    texto += "\n\n".join(bloques)
    if avisos:
        texto += "\n\n⚠️ " + html.escape("; ".join(avisos))
    enviar(texto)


def main():
    datos, avisos = recoger()
    if MODO == "resumen":
        modo_resumen(datos, avisos)
    else:
        modo_alertas(datos, avisos)


if __name__ == "__main__":
    main()
