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
HORIZONTES = [("3 meses", 91), ("6 meses", 182), ("1 año", 365), ("2 años", 730), ("3 años", 1095), ("4 años", 1460)]
MAX_DIAS_SIN_DATO = 5  # descartar si el último dato es demasiado antiguo
USD_A_EUR = {"TTWO", "IGLN.L"}  # cotizan en USD: se convierten a EUR con EURUSD=X

# Cada activo: (nombre, posiciones que representa, ticker para alertas, ticker para resumen)
# - Alertas: ETF que cotiza en tiempo real, para detectar el mínimo el mismo día.
# - Resumen: tu fondo real (valor liquidativo diario); el domingo el retraso no importa.
ACTIVOS = [
    ("Developed World (MSCI World)", "Developed World S/D, MSCI World P, Amundi Core World, MyInvestor Indexado Global PP",
                                       "EUNL.DE",      "0P0001XF40.F"),
    ("Emerging Markets",              "iShares EM Index S, Index MSCI EM",
                                       "EUNM.DE",      "0P0001XF3Z.F"),