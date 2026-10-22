"""
Alertas de mínimos de 3, 6 y 12 meses sobre las exposiciones principales de la cartera.
Pensado para ejecutarse a diario (GitHub Actions) y avisar por Telegram.

Variables de entorno:
  TELEGRAM_TOKEN    token del bot (de @BotFather)
  TELEGRAM_CHAT_ID  id del chat donde enviar
  DRY_RUN=true      imprime el mensaje en vez de enviarlo
"""
import os
from datetime import timedelta

import pandas as pd
import requests
import yfinance as yf
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*inplace.*")

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

# De mayor a menor: si es mínimo de 12M también lo es de 6M y 3M, se informa el mayor
VENTANAS = [("12 meses", 365), ("6 meses", 182), ("3 meses", 91)]
UMBRAL_CERCA = 0.50    # avisar si está a menos de un 2 % del mínimo de 12 meses
MAX_DIAS_SIN_DATO = 5  # descartar si el último dato es demasiado antiguo

# ticker de Yahoo Finance -> (exposición, posiciones que representa)
# Los marcados con (VERIFICAR) conviene comprobarlos en finance.yahoo.com antes de usarlos.
ACTIVOS = {
    "EUNL.DE": ("MSCI World", "Developed World S/D, MSCI World P, Amundi Core World, MyInvestor Indexado Global PP"),
    "EUNM.DE": ("MSCI Emerging Markets", "Vanguard EM Index S, Index MSCI EM"),
    "SXR8.DE": ("S&P 500", "iShares Core S&P 500"),
    "NQSE.DE": ("Nasdaq 100 (EUR hedged)", "iShares Nasdaq 100 Hedged"),               # (VERIFICAR)
    "ZPRS.DE": ("MSCI World Small Cap", "Vanguard Global Small-Cap (proxy)"),          # (VERIFICAR)
    "VHYL.AS": ("FTSE All-World High Div", "Vanguard FTSE All-World High Div Yield"),
    "PPFB.DE": ("Oro", "iShares Physical Gold ETC"),                                   # (VERIFICAR)
    "BTC-EUR": ("Bitcoin", "Bitcoin, iShares Bitcoin ETP"),
    "TTWO":    ("Take-Two (USD)", "Take-Two"),
    # "TICKER": ("MSCI World Quality Div ESG", "iShares MSCI World Quality Div ESG"),  # añadir ticker
}


def analizar(ticker):
    hist = yf.Ticker(ticker).history(period="13mo", interval="1d", auto_adjust=True)["Close"].dropna()
    if hist.empty:
        return None, f"sin datos para {ticker}"

    fecha = hist.index[-1]
    ultimo = float(hist.iloc[-1])
    ultima = fecha.tz_convert(None) if fecha.tzinfo is not None else fecha
    if (pd.Timestamp.now("UTC").tz_localize(None) - ultima).days > MAX_DIAS_SIN_DATO:
        return None, f"datos antiguos para {ticker}"

    anual = hist[hist.index >= fecha - timedelta(days=365)]
    min12, max12 = float(anual.min()), float(anual.max())
    caida = ultimo / max12 - 1

    nivel = None
    for etiqueta, dias in VENTANAS:
        ventana = hist[hist.index >= fecha - timedelta(days=dias)]
        if ultimo <= float(ventana.min()):
            nivel = f"MÍNIMO de {etiqueta}"
            break

    if nivel is None and ultimo <= min12 * (1 + UMBRAL_CERCA):
        nivel = f"a {(ultimo / min12 - 1):.1%} del mínimo de 12 meses"

    if nivel is None:
        return None, None

    return {
        "ticker": ticker,
        "nivel": nivel,
        "precio": ultimo,
        "fecha": fecha.strftime("%d/%m/%Y"),
        "caida": caida,
    }, None


def enviar(texto):
    if DRY_RUN or not (TG_TOKEN and TG_CHAT):
        print("[DRY_RUN] Mensaje que se enviaría:\n" + texto)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": texto, "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()


def main():
    alertas, avisos = [], []
    for ticker, (nombre, cubre) in ACTIVOS.items():
        try:
            res, aviso = analizar(ticker)
        except Exception as e:  # un ticker caído no debe tumbar el resto
            res, aviso = None, f"error en {ticker}: {e}"
        if aviso:
            avisos.append(aviso)
        if res:
            alertas.append(
                f"📉 {nombre} ({res['ticker']})\n"
                f"   {res['nivel']} — cierre {res['precio']:.2f} el {res['fecha']}\n"
                f"   Desde máximo 12M: {res['caida']:.1%}\n"
                f"   Afecta a: {cubre}"
            )
        print(f"{ticker:10} {'ALERTA' if res else 'ok'} {aviso or ''}")

    if alertas:
        texto = "Oportunidades por mínimos\n\n" + "\n\n".join(alertas)
        if avisos:
            texto += "\n\n⚠️ " + "; ".join(avisos)
        enviar(texto)
    else:
        print("Sin alertas hoy.")


if __name__ == "__main__":
    main()
