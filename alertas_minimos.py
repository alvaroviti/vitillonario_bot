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
MAX_DIAS_SIN_DATO = 5  # descartar si el último dato es demasiado antiguo

# ticker de Yahoo Finance -> (exposición, posiciones que representa)
ACTIVOS = {
    "EUNL.DE": ("MSCI World", "Developed World S/D, MSCI World P, Amundi Core World, MyInvestor Indexado Global PP"),
    "EUNM.DE": ("MSCI Emerging Markets", "Vanguard EM Index S, Index MSCI EM"),
    "SXR8.DE": ("S&P 500", "iShares Core S&P 500"),
    "NQSE.DE": ("Nasdaq 100 (EUR hedged)", "iShares Nasdaq 100 Hedged"),
    "ZPRS.DE": ("MSCI World Small Cap", "Vanguard Global Small-Cap (proxy)"),
    "VHYL.AS": ("FTSE All-World High Div", "Vanguard FTSE All-World High Div Yield"),
    "PPFB.DE": ("Oro", "iShares Physical Gold ETC"),
    "BTC-EUR": ("Bitcoin", "Bitcoin, iShares Bitcoin ETP"),
    "TTWO":    ("Take-Two (USD)", "Take-Two"),
}


def cargar(ticker):
    hist = yf.Ticker(ticker).history(period="13mo", interval="1d", auto_adjust=True)["Close"].dropna()
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
    for ticker, (nombre, cubre) in ACTIVOS.items():
        try:
            hist = cargar(ticker)
            datos.append((ticker, nombre, cubre, hist, metricas(hist)))
            print(f"{ticker:10} ok")
        except Exception as e:  # un ticker caído no debe tumbar el resto
            avisos.append(f"{ticker}: {e}")
            print(f"{ticker:10} ERROR {e}")
    return datos, avisos


def modo_alertas(datos, avisos):
    alertas = []
    for ticker, nombre, cubre, hist, m in datos:
        nivel = nivel_minimo(hist, m)
        if nivel:
            print(f"{ticker:10} ALERTA {nivel}")
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
            f"   Mes: {fmt(m['var_mes'])}\n"
            f"   vs máx 12M: {m['vs_max']:+.1%} · vs mín 12M: {m['vs_min']:+.1%}\n"
            f"   mín {barra(m['rango'])} máx"
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
