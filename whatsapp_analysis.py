#!/usr/bin/env python3
"""
whatsapp_analysis.py – erstellt aus WhatsApp-Chat-Exporten einen interaktiven HTML-Report.

ÜBERBLICK (wie eine Prozesskette in der Fertigung)
==================================================
  1. EINLESEN     parse_chat_file / load_chats
                  Textdateien (WhatsApp-Export) -> Tabelle mit einer Zeile pro Nachricht.
                  Mehrere Exporte desselben Chats werden zu einer lückenlosen Tabelle zusammengeführt.
  2. ANREICHERN   enrich_dataframe
                  Zusatzspalten berechnen (Stunde, Wochentag, Länge, Emojis, ...).
  3. AUSWERTEN    compute_* / build_* / split_move_in
                  Kennzahlen bilden: Summen, Häufigkeiten, Antwortzeiten, Vorher/Nachher-Vergleich.
  4. PLOTTEN      plot_*
                  Jede Funktion baut genau ein Diagramm (Plotly-Figure) aus den Kennzahlen.
  5. AUSGEBEN     build_html_report
                  Alle Diagramme + Überschriften in eine HTML-Datei schreiben (im Browser öffnen).

AUFRUF (im Terminal, im Projektordner)
======================================
    python whatsapp_analysis.py                                         # Standard: alle 3 Exporte aus input/
    python whatsapp_analysis.py --save-merged input/chat_merged.txt     # zusätzlich zusammengeführten Chat speichern
    python whatsapp_analysis.py --input input/chat.txt input/chat_complete.txt --output report.html
    python whatsapp_analysis.py --plots top_emojis,emoji_heatmap        # nur ausgewählte Diagramme
    python whatsapp_analysis.py --move-in 2026-04-01                    # Einzugsdatum für das Vorher/Nachher-Kapitel
    python whatsapp_analysis.py --move-in none                          # Vorher/Nachher-Kapitel weglassen

KLEINES GLOSSAR (Python/pandas-Begriffe, die unten oft vorkommen)
=================================================================
  DataFrame (df)  Tabelle wie ein Excel-Blatt: Spalten haben Namen ("user", "datetime", ...),
                  jede Zeile ist eine Nachricht. df["spalte"] liefert eine Spalte (= "Series").
  Series          Eine einzelne Spalte (Messreihe) mit Index (Zeilenbeschriftung).
  Vektorisiert    df["a"] / 2 rechnet für ALLE Zeilen gleichzeitig – wie eine Formel, die man in
                  Excel die ganze Spalte runterzieht. Schneller und kürzer als eine for-Schleife.
  Maske           df[df["user"] == "Julia"] – eine Ja/Nein-Spalte filtert die Zeilen (wie Autofilter).
                  Mehrere Bedingungen mit & (und), | (oder), ~ (nicht) verknüpfen, jeweils in Klammern.
  groupby         Gruppieren + Zusammenfassen, wie eine Pivot-Tabelle: df.groupby("user").size()
                  = "Anzahl Zeilen je Person".
  unstack         Macht aus einer langen Liste (Stunde, Wochentag) -> Anzahl eine 2D-Matrix
                  (Zeilen = Stunde, Spalten = Wochentag) – genau das, was eine Heatmap braucht.
  dict            Nachschlagetabelle Schlüssel -> Wert, z. B. {"Julia Rahn": "Julia"}.
  Counter         Ein dict, das Dinge zählt: Counter(["a", "b", "a"]) -> {"a": 2, "b": 1}.
  lambda          Kurze Funktion ohne Namen: lambda: plot_x(daten) wird erst beim Aufruf ausgeführt.
  Regex (re)      "Regulärer Ausdruck" = Suchmuster für Text, wie eine Schablone/Lehre:
                  \\d = Ziffer, {2} = genau 2 Stück, (...) = Teil merken, .* = beliebiger Rest.
  Optional[X]     Typ-Hinweis: Wert ist entweder X oder None ("nichts"). Nur Doku, keine Prüfung.
  f"..."          f-String: {ausdruck} im Text wird durch den Wert ersetzt, z. B. f"{n} Nachrichten".
                  {x:.1f} = 1 Nachkommastelle, {datum:%d.%m.%Y} = Datum als 01.04.2026.
"""

# "annotations" erlaubt Typ-Hinweise wie "list[str]" auch in älteren Python-Versionen.
from __future__ import annotations

# --- Standardbibliothek (immer bei Python dabei) ---
import re                                   # Regex: Textmuster suchen
import argparse                             # Kommandozeilen-Optionen (--input, --output, ...)
import base64                               # Bilder als Text kodieren, um sie direkt ins HTML einzubetten
from collections import Counter, defaultdict
from datetime import datetime
from io import BytesIO                      # "Datei im Arbeitsspeicher" für das Wortwolken-Bild
from itertools import combinations          # alle Paare aus einer Liste: (a,b), (a,c), (b,c)
from typing import Dict, List, Optional, Tuple

# --- Zusatzpakete (per pip/dnf installiert) ---
import emoji                                # erkennt Emojis im Text (auch zusammengesetzte wie 👍🏻)
import numpy as np                          # Numerik: Mittelwert, Median, Wurzel, Logarithmus, ...
import pandas as pd                         # Tabellen (DataFrames)
import plotly.express as px                 # Plotly "schnell": ganze Diagramme mit einem Befehl
import plotly.graph_objects as go           # Plotly "detailliert": Diagramm Stück für Stück aufbauen
from plotly.subplots import make_subplots   # mehrere Diagramme nebeneinander in einer Figure
import plotly.io as pio                     # Plotly-Figure -> HTML-Code
import networkx as nx                       # Graphen/Netzwerke (Knoten + Kanten), für die Emoji-"Love Map"
from wordcloud import WordCloud, STOPWORDS  # Wortwolken; STOPWORDS = englische Füllwörter
from PIL import Image                       # Bildverarbeitung (wird von wordcloud intern genutzt)


# =============================================================================
# 1) KONFIGURATION – hier stellt man das Verhalten ein, ohne die Logik anzufassen
# =============================================================================

# Eingabedateien. Es können mehrere Exporte DESSELBEN Chats sein; sie werden zusammengeführt
# (siehe load_chats). Die REIHENFOLGE ist wichtig: Wo sich Zeiträume überschneiden, gewinnt die
# Datei, die weiter vorne steht. Deshalb steht der genaueste Export (mit Sekunden) zuerst.
#   chat.txt          iPhone-Export, Zeit mit Sekunden,  15.10.23 – 23.07.24
#   chat_complete.txt iPhone-Export, nur Minuten,        15.10.23 – 05.12.25
#   WhatsApp Chat ... Android-Export, nur Minuten,       24.12.24 – 15.11.26
DEFAULT_INPUTS = ["input/chat.txt", "input/chat_complete.txt", "input/WhatsApp Chat with Julia Rahn.txt"]

# Dieselbe Person kann in verschiedenen Exporten unterschiedlich heißen (je nach Telefonbuch).
# Links der Name im Export, rechts der Name, unter dem sie im Report erscheinen soll.
USER_ALIASES = {"Julia Rahn": "Julia"}

DEFAULT_OUTPUT = "whatsapp_report.html"     # Name der erzeugten Report-Datei
TOP_EMOJI_COUNT = 20                        # wie viele Emojis im "Top-Emojis"-Diagramm erscheinen

# Einzugsdatum für das Kapitel "Seit wir zusammen wohnen".
# Verglichen wird: "nachher" = Einzug bis letzte Nachricht,
#                  "vorher"  = gleich langer Zeitraum direkt vor dem Einzug (fairer Vergleich).
MOVE_IN_DATE = datetime(2026, 4, 1)

# Farben für das Vorher/Nachher-Kapitel (aus einer farbfehlsichtigkeits-geprüften Palette).
COLOR_BEFORE = "#2a78d6"   # Blau  = vorher
COLOR_AFTER = "#eb6834"    # Orange = nachher
# Farbverlauf für die Differenz-Heatmap: Blau (weniger) -> Grau (unverändert) -> Rot (mehr).
# Format: [[Position 0..1, Farbe], ...]
DIVERGING_SCALE = [[0.0, "#184f95"], [0.5, "#f0efec"], [1.0, "#b8322f"]]
WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]   # pandas zählt Montag = 0 ... Sonntag = 6

# Was als "Wort" zählt (für den Wörter-Vergleich): mind. 3 Kleinbuchstaben inkl. Umlaute.
WORD_RE = re.compile(r"[a-zäöüß]{3,}")


# =============================================================================
# 2) TEXTMUSTER (Regex) FÜR DAS EINLESEN DER EXPORT-DATEIEN
# =============================================================================
# Ein WhatsApp-Export ist eine Textdatei. Jede Nachricht beginnt mit einer "Kopfzeile"
# (Datum, Uhrzeit, Name). Mehrzeilige Nachrichten gehen in den folgenden Zeilen OHNE Kopf weiter.
# Exporte enthalten manchmal unsichtbare Steuerzeichen am Zeilenanfang:
#   \u200e = "Left-to-Right Mark" (Schreibrichtung), \u200f = Gegenstück, \ufeff = "BOM" (Dateianfang).
# [\u200e\ufeff]* heißt: "beliebig viele davon (auch keins) am Anfang ignorieren".

# iPhone-Format:  [23.11.23, 06:53:39] Name: Nachricht
#   Gruppe 1 = Datum, 2 = Uhrzeit, 3 = Name (alles bis zum ersten ':'), 4 = Nachrichtentext
LINE_RE = re.compile(r'^[\u200e\ufeff]*\[(\d{2}\.\d{2}\.\d{2}), (\d{2}:\d{2}:\d{2})\] ([^:]+?): (.*)$')

# Android-Format (englisch eingestellt):  12/24/24, 23:43 - Name: Nachricht
#   Achtung: Monat/Tag/Jahr (amerikanisch), 24h-Uhr, keine Sekunden.
ANDROID_LINE_RE = re.compile(r'^[\u200e\ufeff]*(\d{1,2}/\d{1,2}/\d{2}), (\d{1,2}:\d{2}) - ([^:]+?): (.*)$')

# Zeilen MIT Zeitstempel, aber OHNE "Name: " sind Systemmeldungen von WhatsApp
# (z. B. "Christian ist ein Kontakt", Hinweis zur Verschlüsselung) -> werden übersprungen.
SYSTEM_LINE_RE = re.compile(r'^[\u200e\ufeff]*(\[\d{2}\.\d{2}\.\d{2}, \d{2}:\d{2}:\d{2}\]|\d{1,2}/\d{1,2}/\d{2}, \d{1,2}:\d{2} - )')

# Unsichtbare Steuerzeichen, die aus dem Nachrichtentext entfernt werden.
INVISIBLE_RE = re.compile(r'[\u200e\u200f\ufeff]')

# Fotos/Videos/... sind im Export nicht enthalten, nur ein Platzhaltertext – der heißt je nach
# Handy und Sprache anders. Alle Varianten werden auf EINEN einheitlichen Platzhalter gebracht.
MEDIA_PLACEHOLDER = "<Medien ausgeschlossen>"
MEDIA_RE = re.compile(r'^(<Medien ausgeschlossen>|<Media omitted>|(image|video|audio|sticker|GIF|document|Contact card) omitted)$')
# Vermerk "Nachricht wurde bearbeitet" am Textende – wird entfernt (gehört nicht zum Inhalt).
EDITED_RE = re.compile(r'\s*<(Diese Nachricht wurde bearbeitet\.?|This message was edited)>\s*$')
# Verschlüsselungs-Hinweis, der im iPhone-Export wie eine normale Nachricht aussieht.
SYSTEM_RE = re.compile(r'end-to-end encrypted|Ende-zu-Ende-verschlüsselt', re.I)   # re.I = Groß/klein egal
# Internet-Links (http://... bis zum nächsten Leerzeichen) – stören in Wortwolken und Wortlisten.
URL_RE = re.compile(r'https?://\S+')

# Deutsche Füllwörter, die in Wortwolken/Wortlisten nichts aussagen (die mitgelieferte STOPWORDS-
# Liste von wordcloud ist nur englisch). Bei Bedarf einfach Wörter ergänzen (klein geschrieben).
GERMAN_STOPWORDS = set("""
aber alle allem allen aller alles als also am an ander andere anderem anderen anderer anderes auch auf aus bei bin bis
bist bitte da dabei damit dann das dass dein deine deinem deinen deiner dem den denn der des dich die dir doch dort du
durch ein eine einem einen einer eines einfach er es etwas euch euer für gar gibt grad gerade habe haben hab hast hat
hatte hätte heute hier hin ich ihm ihn ihr im in ins ist ja jetzt kann kannst kein keine mal man mehr mein meine mich
mir mit muss musst nach nicht nichts noch nun nur ob oder ohne schon sehr sein seine sich sie sind so soll sollen
sondern über um und uns unser unsere unter viel vom von vor war waren warst was weil wenn wer werde werden wie wieder
will wir wird wirst wo wollte zu zum zur zwar net nen ne ok okay oh ah eh dass ganz gut dich bzw medien ausgeschlossen
diese dieser dieses diesen diesem seid sei wäre würde gehts geht gerne ganz
""".split())


# =============================================================================
# 3) KLEINE HILFSFUNKTIONEN
# =============================================================================

def extract_emojis(text: str) -> List[str]:
    """
    Liefert alle Emojis eines Textes als Liste, z. B. "Hi 😂😂 👍🏻" -> ["😂", "😂", "👍🏻"].
    Die Bibliothek "emoji" erkennt auch Emojis, die aus mehreren Zeichen bestehen
    (Hautfarbe, Familien, Flaggen) – mit einfacher Zeichensuche ginge das nicht zuverlässig.
    """
    try:
        return [e["emoji"] for e in emoji.emoji_list(text)]
    except Exception:
        return []   # bei kaputtem Text lieber "keine Emojis" als Programmabbruch


def hour_fraction(dt: datetime) -> float:
    """Uhrzeit als Kommazahl in Stunden, z. B. 07:30:00 -> 7.5 (für Diagramme mit Zeitachse 0..24)."""
    return dt.hour + dt.minute/60.0 + dt.second/3600.0


def apply_font_sizes(fig: go.Figure) -> go.Figure:
    """
    Einheitliche Schriftgrößen für alle Diagramme (zentral einstellbar, statt in jedem Plot einzeln).
    Hinweis: Wirkt nur auf die ERSTE x- und y-Achse. Bei Diagrammen mit mehreren Teilplots
    (make_subplots) müssen die übrigen Achsen extra eingestellt werden (siehe plot_move_in_people).
    """
    fig.update_layout(
        # width=560,
        # height=794,
        title_font=dict(size=25),
        xaxis_title_font=dict(size=20),
        yaxis_title_font=dict(size=20),
        xaxis_tickfont=dict(size=18),
        yaxis_tickfont=dict(size=18),
        legend=dict(font=dict(size=18))
    )
    return fig


# =============================================================================
# 4) EINLESEN UND ZUSAMMENFÜHREN DER CHAT-EXPORTE
# =============================================================================

def parse_chat_file(path: str) -> pd.DataFrame:
    """
    Liest EINE WhatsApp-Exportdatei und gibt eine Tabelle zurück – eine Zeile pro Nachricht.

    Erkannte Kopfzeilen-Formate:
        iPhone:  [23.11.23, 06:53:39] Name: Nachricht
        Android: 12/24/24, 23:43 - Name: Nachricht
    Zeilen ohne Kopfzeile sind die Fortsetzung der vorherigen (mehrzeiligen) Nachricht.

    Ergebnis-Spalten:
        datetime  Zeitpunkt der Nachricht
        user      Absender (nach USER_ALIASES vereinheitlicht)
        message   Text (bei Fotos etc. der einheitliche Platzhalter)
        is_media  True, wenn die Nachricht ein Foto/Video/... war
    """
    rows = []                   # hier sammeln wir die Nachrichten als Liste von dicts
    in_system_msg = False       # "Merker": gerade in einer (evtl. mehrzeiligen) Systemmeldung?
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:                           # Datei Zeile für Zeile lesen
            line = raw.rstrip("\r\n")           # Zeilenumbruch am Ende abschneiden
            # Prüfreihenfolge: iPhone-Kopf? Android-Kopf? Systemmeldung? Sonst: Fortsetzungszeile.
            # "m := ..." (Walross-Operator) weist zu UND prüft in einem Schritt.
            if m := LINE_RE.match(line):
                date_s, time_s, user, msg = m.groups()
                dt = datetime.strptime(f"{date_s} {time_s}", "%d.%m.%y %H:%M:%S")   # Text -> Zeitpunkt
            elif m := ANDROID_LINE_RE.match(line):
                date_s, time_s, user, msg = m.groups()
                dt = datetime.strptime(f"{date_s} {time_s}", "%m/%d/%y %H:%M")      # US-Datum!
            elif SYSTEM_LINE_RE.match(line):
                in_system_msg = True  # Systemmeldung samt ihrer Folgezeilen überspringen
                continue
            else:
                # Fortsetzung der vorherigen Nachricht -> mit Zeilenumbruch anhängen
                # (aber nicht, wenn die vorherige Zeile eine Systemmeldung war).
                if rows and not in_system_msg:
                    rows[-1]["message"] += "\n" + INVISIBLE_RE.sub("", line)
                continue
            in_system_msg = False
            user = USER_ALIASES.get(user.strip(), user.strip())   # Alias anwenden, falls vorhanden
            rows.append({"datetime": dt, "user": user, "message": INVISIBLE_RE.sub("", msg)})
    if not rows:
        raise SystemExit(f"No messages parsed from {path} — check file and format.")

    df = pd.DataFrame(rows)     # Liste von dicts -> Tabelle
    # Nachbearbeitung, jeweils für die ganze Spalte auf einmal (vektorisiert):
    df["message"] = df["message"].str.replace(EDITED_RE, "", regex=True).str.strip()   # "bearbeitet"-Vermerk weg
    df = df[~df["message"].str.contains(SYSTEM_RE)]          # Verschlüsselungs-Hinweis raus (~ = "nicht")
    df["is_media"] = df["message"].str.match(MEDIA_RE)       # True/False-Spalte: Foto/Video/...?
    df.loc[df["is_media"], "message"] = MEDIA_PLACEHOLDER    # alle Platzhalter-Varianten vereinheitlichen
    # Chronologisch sortieren; "stable" behält bei gleichem Zeitstempel die Reihenfolge der Datei bei.
    return df.sort_values("datetime", kind="stable").reset_index(drop=True)


def load_chats(paths: List[str]) -> pd.DataFrame:
    """
    Liest mehrere Exporte DESSELBEN Chats und fügt sie ohne doppelte Nachrichten zusammen.

    Prinzip (wie Messreihen aneinanderstückeln): Die Dateien werden in der angegebenen
    Reihenfolge verwendet. Jede weitere Datei liefert nur Nachrichten, die NACH der letzten
    bereits übernommenen Nachricht liegen. Bei Überschneidungen gewinnt also die vordere Datei –
    deshalb den Export mit den genauesten Zeitstempeln (Sekunden) zuerst angeben.

    Warum nicht einfach Duplikate über Zeit+Text suchen? Dieselbe Nachricht hat in verschiedenen
    Exporten oft um bis zu eine Minute abweichende Zeitstempel – ein exakter Abgleich findet sie nicht.
    """
    dfs = [parse_chat_file(p) for p in paths]       # jede Datei einzeln einlesen
    for p, d in zip(paths, dfs):                     # zip = paarweise durchlaufen: (Pfad, Tabelle)
        print(f"{p}: {len(d)} messages, {d['datetime'].min()} — {d['datetime'].max()}")
    merged = []
    last_ts = None                                   # Zeitpunkt der letzten übernommenen Nachricht
    for d in dfs:
        if last_ts is not None:
            d = d[d["datetime"] > last_ts]           # nur Neues übernehmen
        if not d.empty:
            merged.append(d)
            last_ts = d["datetime"].max()
    df = pd.concat(merged, ignore_index=True)        # Tabellen untereinanderhängen
    print(f"merged: {len(df)} messages")
    return df


def write_chat_file(df: pd.DataFrame, path: str) -> None:
    """
    Schreibt die (zusammengeführten) Nachrichten wieder als Textdatei im iPhone-Exportformat
    [tt.mm.jj, hh:mm:ss] Name: Nachricht – kann später wieder mit --input eingelesen werden.
    """
    with open(path, "w", encoding="utf-8") as f:
        for ts, user, msg in zip(df["datetime"], df["user"], df["message"]):
            f.write(f"[{ts:%d.%m.%y, %H:%M:%S}] {user}: {msg}\n")
    print(f"Merged chat written to {path} ({len(df)} messages)")


# =============================================================================
# 5) ANREICHERN – Zusatzspalten, die viele Diagramme brauchen
# =============================================================================

def enrich_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ergänzt abgeleitete Spalten (einmal hier berechnet, statt in jedem Diagramm neu):
        date         nur das Datum (ohne Uhrzeit) – zum Zählen pro Tag
        hour         Stunde 0..23
        weekday      Wochentag 0 = Montag ... 6 = Sonntag
        msg_length   Anzahl Zeichen (leer/NaN bei Fotos etc., damit Platzhalter nicht mitzählen)
        emojis       Liste der Emojis in der Nachricht
        emoji_count  Anzahl Emojis in der Nachricht
    """
    df = df.copy()   # Kopie, damit die übergebene Tabelle unverändert bleibt
    df["date"] = df["datetime"].dt.date                 # .dt = Datums-Werkzeuge für die ganze Spalte
    df["hour"] = df["datetime"].dt.hour
    df["weekday"] = df["datetime"].dt.day_of_week  # Montag = 0
    # .where(Bedingung) behält den Wert nur dort, wo die Bedingung stimmt, sonst NaN ("kein Wert").
    df["msg_length"] = df["message"].str.len().where(~df["is_media"])
    df["emojis"] = df["message"].apply(extract_emojis)  # .apply = Funktion auf jede Zeile anwenden
    df["emoji_count"] = df["emojis"].apply(len)
    return df


# =============================================================================
# 6) AUSWERTUNGEN (Kennzahlen) – noch keine Diagramme, nur Zahlen/Tabellen
# =============================================================================

def compute_emoji_counts(df: pd.DataFrame, top_n: int = TOP_EMOJI_COUNT):
    """
    Emoji-Statistiken. Rückgabe ist ein dict mit:
        users               Liste der Personen
        emoji_all           Tabelle mit einer Zeile pro einzelnem Emoji (siehe "explode")
        total_emoji_counts  Emojis gesamt je Person
        top_emojis          die top_n häufigsten Emojis insgesamt
        user_emojis         je Person ein Counter {Emoji: Anzahl}
        emoji_by_day_user   Emojis pro Tag und Person
    """
    users = df["user"].unique().tolist()

    # "explode": Eine Nachricht mit 3 Emojis wird zu 3 Zeilen mit je einem Emoji.
    # Nachrichten ohne Emoji ergeben eine leere Zeile, die dropna entfernt.
    emoji_all = df.explode("emojis").dropna(subset=["emojis"])

    # Emojis gesamt je Person (Summe der Spalte emoji_count, gruppiert nach Person)
    total_emoji_counts = df.groupby("user")["emoji_count"].sum().reindex(users).fillna(0).astype(int)

    # häufigste Emojis insgesamt
    if not emoji_all.empty:
        top_emojis = emoji_all["emojis"].value_counts().nlargest(top_n)   # value_counts = Strichliste
    else:
        top_emojis = pd.Series(dtype=int)

    # je Person: Strichliste ihrer Emojis
    emoji_per_user = emoji_all.groupby("user")["emojis"].value_counts()
    user_emojis = {}
    for user in users:
        try:
            s = emoji_per_user.loc[user]
            user_emojis[user] = Counter(s.to_dict())
        except Exception:
            user_emojis[user] = Counter()   # Person ohne ein einziges Emoji

    # Emojis pro Tag und Person (für den Verlauf über die Zeit)
    emoji_by_day_user = (df.explode("emojis")
                           .dropna(subset=["emojis"])
                           .groupby(["user","date"])
                           .size()
                           .reset_index(name="emoji_count_per_day"))
    return {
        "users": users,
        "emoji_all": emoji_all,
        "total_emoji_counts": total_emoji_counts,
        "top_emojis": top_emojis,
        "user_emojis": user_emojis,
        "emoji_by_day_user": emoji_by_day_user
    }


def compute_time_series_and_heatmaps(df: pd.DataFrame):
    """
    Zeitliche Auswertungen. Rückgabe ist ein dict mit:
        df_emoji_time   eine Zeile je Emoji mit Person und Uhrzeit (als Kommazahl) -> Violin-Plot
        heat_table      Matrix Stunde x Wochentag: Anzahl Emojis    -> Emoji-Heatmap
        msg_heat_table  Matrix Stunde x Wochentag: Anzahl Nachrichten -> Nachrichten-Heatmap
        daily_counts    Nachrichten und Emojis pro Tag + geglätteter Trend -> Timeline
    """
    # Eine Zeile pro Emoji mit Uhrzeit (iterrows = Zeile für Zeile; langsam, aber hier übersichtlich)
    emoji_time_rows = []
    for idx, row in df.iterrows():
        if row["emoji_count"] == 0:
            continue
        hf = hour_fraction(row["datetime"])
        for em in row["emojis"]:
            emoji_time_rows.append({"user": row["user"], "emoji": em, "hour_frac": hf})
    df_emoji_time = pd.DataFrame(emoji_time_rows)

    # Heatmap-Matrizen: zählen je (Stunde, Wochentag), dann Wochentag in die Spalten "kippen" (unstack)
    heat = df.explode("emojis").dropna(subset=["emojis"])
    heat_table = heat.groupby(["hour","weekday"]).size().unstack(fill_value=0)
    msg_heat_table = df.groupby(["hour","weekday"]).size().unstack(fill_value=0)

    # Nachrichten und Emojis pro Tag
    daily_counts = df.groupby("date").agg(
        messages=("message", "count"),
        emojis=("emoji_count", "sum")
    ).reset_index().sort_values("date")
    # Trendlinie = gleitender Mittelwert über 7 Tage (zentriert) – wirkt wie ein Tiefpassfilter
    # und glättet die starken Tag-zu-Tag-Schwankungen. min_periods=1: auch am Rand einen Wert liefern.
    # Hinweis: Tage ganz ohne Nachricht fehlen in der Tabelle und zählen daher nicht als 0.
    daily_counts['messages_trend'] = daily_counts['messages'].rolling(window=7, min_periods=1, center=True).mean()
    daily_counts['emojis_trend'] = daily_counts['emojis'].rolling(window=7, min_periods=1, center=True).mean()

    return {
        "df_emoji_time": df_emoji_time,
        "heat_table": heat_table,
        "msg_heat_table": msg_heat_table,
        "daily_counts": daily_counts
    }


def compute_response_times(df: pd.DataFrame) -> Dict[str, List[float]]:
    """
    Antwortzeiten in Minuten.

    Definition: Antwortzeit einer Person = Zeit von der letzten Nachricht der ANDEREN Person
    bis zu ihrer eigenen nächsten Nachricht. Gezählt wird nur beim "Sprecherwechsel" –
    schreibt jemand drei Nachrichten hintereinander, zählt nur die erste als Antwort.
    Lücken über 24 h werden ignoriert (das ist keine Antwort mehr, sondern ein neues Gespräch).

    Rückgabe (zwei Werte):
        response_times      dict Person -> Liste aller Antwortzeiten
        avg_response_time   dict Person -> Mittelwert (arithmetisch; reagiert stark auf Ausreißer!)
    """
    response_times = defaultdict(list)   # wie dict, legt bei neuem Schlüssel automatisch eine leere Liste an
    prev_user, prev_ts = None, None      # Absender und Zeit der vorherigen Nachricht
    for user, ts in zip(df["user"], df["datetime"]):
        if prev_user is not None and user != prev_user:          # Sprecherwechsel?
            diff_min = (ts - prev_ts).total_seconds() / 60.0
            if 0 <= diff_min < 60*24:
                response_times[user].append(diff_min)
        prev_user, prev_ts = user, ts
    avg_response_time = {user: (np.mean(response_times[user]) if response_times[user] else np.nan)
                         for user in df["user"].unique().tolist()}
    return response_times, avg_response_time


def build_emoji_cooccurrence_graph(df: pd.DataFrame):
    """
    Baut ein Netzwerk: Knoten = Emojis, Kante = "kamen zusammen in einer Nachricht vor".
    Kantengewicht = wie oft das Paar gemeinsam auftrat.

    Rückgabe:
        G          der Graph (networkx)
        pos        Positionen der Knoten zum Zeichnen (Federmodell, s. u.)
        node_freq  dict Emoji -> Gesamthäufigkeit
    """
    cooccurrence = Counter()
    for _, row in df.iterrows():
        ems = list(dict.fromkeys(row["emojis"]))  # Duplikate entfernen, Reihenfolge behalten
        if len(ems) > 1:
            for a,b in combinations(sorted(ems), 2):   # alle Paare; sortiert, damit (a,b) == (b,a)
                cooccurrence[(a,b)] += 1
    G = nx.Graph()
    for (a,b), w in cooccurrence.items():
        G.add_edge(a,b,weight=w)
    emoji_all = df.explode("emojis").dropna(subset=["emojis"])
    node_freq = emoji_all["emojis"].value_counts().to_dict()
    for n in G.nodes():
        G.nodes[n]["freq"] = node_freq.get(n, 1)
    pos = {}
    if len(G) > 0:
        # "spring_layout" = Feder-Masse-Modell: Kanten wirken wie Federn, Knoten stoßen sich ab;
        # simuliert, bis es im Gleichgewicht ist. seed=42 = fester Startwert -> reproduzierbares Bild.
        pos = nx.spring_layout(G, k=0.5, iterations=100, seed=42)
    return G, pos, node_freq


def generate_wordcloud_images(df: pd.DataFrame) -> Dict[str, str]:
    """
    Erzeugt je Person eine Wortwolke (häufige Wörter groß) und gibt sie als
    "data:image/png;base64,..."-Text zurück. So steckt das Bild direkt in der HTML-Datei –
    der Report bleibt eine einzige Datei ohne separate Bilddateien.
    """
    wordcloud_images = {}
    users = df["user"].unique().tolist()
    for user in users:
        msgs = df.loc[(df["user"]==user) & ~df["is_media"], "message"]   # ohne Foto-Platzhalter
        text = URL_RE.sub(" ", " ".join(msgs.tolist()))                  # alles zu einem Text, Links raus
        if not text.strip():
            continue
        stopwords = set(STOPWORDS) | GERMAN_STOPWORDS                     # | = Vereinigungsmenge
        wc = WordCloud(width=1200, height=600, background_color="white",
                       stopwords=stopwords, collocations=False).generate(text)
        img = wc.to_image()
        buffer = BytesIO()                       # PNG nicht auf Platte, sondern in den Arbeitsspeicher
        img.save(buffer, format="PNG")
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        wordcloud_images[user] = f"data:image/png;base64,{b64}"
    return wordcloud_images


# =============================================================================
# 7) DIAGRAMME – jede Funktion liefert eine Plotly-Figure (oder None, wenn keine Daten da sind)
# =============================================================================
# Grundprinzip Plotly (graph_objects):
#   fig = go.Figure()                 leeres Diagramm
#   fig.add_trace(go.Bar(...))        eine Datenreihe ("trace") hinzufügen: Balken, Linie, Heatmap, ...
#   fig.update_layout(...)            Titel, Achsen, Legende, Größe einstellen
# plotly.express (px) erledigt das für Standarddiagramme in einem einzigen Befehl.

def plot_total_emojis_per_user(total_emoji_counts: pd.Series) -> Optional[go.Figure]:
    """Balkendiagramm: Emojis gesamt je Person."""
    fig = px.bar(
        x=total_emoji_counts.index.astype(str),
        y=total_emoji_counts.values,
        labels={'x':'User','y':'Total Emojis'},
        title='Du natürlich :)'
    )
    apply_font_sizes(fig)
    return fig


def plot_emoji_time_violin(df_emoji_time: pd.DataFrame) -> Optional[go.Figure]:
    """
    Violin-Plot: Zu welcher Uhrzeit verschickt jede Person Emojis?
    Ein Violin-Plot ist eine "geglättete Häufigkeitsverteilung, hochkant und gespiegelt":
    Wo die Form breit ist, kamen zu dieser Uhrzeit viele Emojis. Punkte = einzelne Emojis.
    """
    if df_emoji_time.empty:
        return None
    violin_df = df_emoji_time.copy()
    fig = px.violin(
        violin_df, x="user", y="hour_frac", points="all",
        labels={'hour_frac':'Hour of day','user':'User'},
        title='Emoji Usage Time of Day (Violin plot)'
    )
    fig.update_yaxes(range=[0,24])
    apply_font_sizes(fig)
    return fig


def plot_emoji_usage_over_time(emoji_by_day_user: pd.DataFrame) -> Optional[go.Figure]:
    """Linien je Person: Emojis pro Tag über die gesamte Zeit (logarithmische y-Achse)."""
    if emoji_by_day_user.empty:
        return None
    # pivot: lange Liste (Tag, Person, Anzahl) -> Tabelle mit einer Spalte je Person
    pivot = emoji_by_day_user.pivot(index="date", columns="user", values="emoji_count_per_day").fillna(0)
    fig = go.Figure()
    for user in pivot.columns:
        fig.add_trace(go.Scatter(x=pivot.index, y=pivot[user], mode='lines+markers', name=user))
    fig.update_layout(title="Emoji Usage Over Time (per day)", xaxis_title="Date", yaxis_title="Emoji count")
    # Log-Achse: 1, 10, 100 haben gleiche Abstände. Achtung: Tage mit 0 können darauf nicht
    # dargestellt werden (log(0) gibt es nicht) – sie fallen aus der Linie heraus.
    fig.update_yaxes(type="log")
    apply_font_sizes(fig)
    return fig


def plot_top_emojis(top_emojis: pd.Series) -> Optional[go.Figure]:
    """Liegendes Balkendiagramm der häufigsten Emojis; das Emoji steht als Beschriftung am Balken."""
    if top_emojis.empty:
        return None
    labels = top_emojis.index.astype(str)
    values = top_emojis.values
    fig = px.bar(
        x=values[::-1],          # [::-1] = Reihenfolge umdrehen, damit der größte Balken oben steht
        text=labels[::-1],
        orientation='h',
        labels={'x':'Count','y':'Emoji'},
        title=f"Top {len(labels)} Emojis Overall"
    )
    fig.update_traces(textfont_size=26, textposition="outside")
    apply_font_sizes(fig)
    return fig


def plot_emoji_heatmap(heat_table: pd.DataFrame) -> go.Figure:
    """Heatmap Stunde (Zeilen) x Wochentag (Spalten): Wann werden die meisten Emojis verschickt?"""
    # reindex: sicherstellen, dass alle 24 Stunden und 7 Tage vorhanden sind (fehlende = 0)
    heat_mat = heat_table.reindex(index=range(24), columns=range(7), fill_value=0)
    fig = go.Figure(data=go.Heatmap(
        z=heat_mat.values,
        x=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
        y=[f"{h}:00" for h in heat_mat.index],
        colorscale="YlGnBu",
        colorbar=dict(title="Emoji count")
    ))
    fig.update_layout(title="Emoji Heatmap by Hour and Day of Week")
    fig.update_yaxes(autorange='reversed')   # 0:00 oben, 23:00 unten (wie ein Tageskalender)
    apply_font_sizes(fig)
    return fig


def plot_message_heatmap(msg_heat_table: pd.DataFrame) -> go.Figure:
    """Heatmap Stunde x Wochentag: Wann werden die meisten Nachrichten geschrieben?"""
    msg_mat = msg_heat_table.reindex(index=range(24), columns=range(7), fill_value=0)
    fig = go.Figure(data=go.Heatmap(
        z=msg_mat.values,
        x=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"],
        y=[f"{h}:00" for h in msg_mat.index],
        colorscale="Plasma",
        colorbar=dict(title="Messages")
    ))
    fig.update_layout(title="Message Activity Heatmap by Hour and Day of Week")
    fig.update_yaxes(autorange='reversed')
    apply_font_sizes(fig)
    return fig


def plot_message_length(df: pd.DataFrame) -> go.Figure:
    """Violin-Plot der Nachrichtenlänge (Zeichen) je Person, mit Boxplot und Mittelwertlinie."""
    users = df["user"].unique().tolist()
    fig = go.Figure()
    for user in users:
        lengths = df.loc[df['user']==user,'msg_length'].dropna()   # dropna: Fotos (NaN) weglassen
        if len(lengths) == 0:
            continue
        fig.add_trace(go.Violin(x=[user]*len(lengths), y=lengths, name=user, box_visible=True, meanline_visible=True))
    fig.update_layout(title="Message Length Distribution per User", yaxis_title="Characters")
    #fig.update_yaxes(type="log")
    apply_font_sizes(fig)
    return fig


def plot_response_time_hist(response_times: Dict[str, List[float]]) -> go.Figure:
    """Histogramm der Antwortzeiten je Person (halbtransparent übereinandergelegt)."""
    fig = go.Figure()
    for user, vals in response_times.items():
        if not vals:
            continue
        fig.add_trace(go.Histogram(x=vals, name=user, opacity=0.6))
    fig.update_layout(barmode='overlay', title="Response Time Distribution per User (minutes)", xaxis_title="Minutes")
    apply_font_sizes(fig)
    return fig


def plot_avg_response_time(avg_response_time: Dict[str, float]) -> go.Figure:
    """Balkendiagramm: mittlere Antwortzeit je Person (Mittelwert, siehe compute_response_times)."""
    avg_df = pd.DataFrame({"user": list(avg_response_time.keys()), "avg_min": list(avg_response_time.values())})
    fig = px.bar(avg_df.sort_values("avg_min"), x="user", y="avg_min",
                 labels={"avg_min":"Avg response (min)","user":"User"},
                 title="Average Response Time per User (minutes)")
    apply_font_sizes(fig)
    return fig


def plot_emoji_network(G: nx.Graph, pos: dict) -> Optional[go.Figure]:
    """
    Komplettes Emoji-Netzwerk (alle Emojis). Wird aktuell im Report NICHT gezeigt
    (dort steht die übersichtlichere "Love Map"), lässt sich aber mit --plots emoji_network erzeugen.
    """
    if len(G) == 0:
        return None
    # Kanten als Linienzüge: je Kante (x0, x1, None) – das None unterbricht die Linie,
    # so können alle Kanten in EINER Datenreihe gezeichnet werden.
    edge_x = []
    edge_y = []
    for u,v,data in G.edges(data=True):
        x0,y0 = pos[u]
        x1,y1 = pos[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    node_x = []
    node_y = []
    node_text = []
    node_size = []
    for n in G.nodes():
        x,y = pos[n]
        node_x.append(x)
        node_y.append(y)
        node_text.append(f"{n} (freq {G.nodes[n].get('freq', 1)})")
        node_size.append(max(20, G.nodes[n].get('freq', 1) * 6))

    edge_trace = go.Scatter(x=edge_x, y=edge_y, mode='lines', line=dict(width=3, color='#888'), hoverinfo='none')
    node_trace = go.Scatter(x=node_x, y=node_y, mode='markers+text',
                            text=[n for n in G.nodes()],
                            hovertext=node_text,
                            textposition="top center",
                            marker=dict(size=node_size, color='orange', line=dict(width=1)))
    fig = go.Figure(data=[edge_trace, node_trace])
    fig.update_layout(title="Emoji Co-occurrence Network", showlegend=False)
    apply_font_sizes(fig)
    return fig


def plot_emoji_love_map(G: nx.Graph, node_freq: dict) -> Optional[go.Figure]:
    """
    "Love Map": Netzwerk der 25 häufigsten Emojis. Linie = die beiden Emojis kamen gemeinsam
    in einer Nachricht vor. Kreisgröße = Häufigkeit des Emojis. Emojis, die oft zusammen
    vorkommen, werden vom Federmodell nah zueinander gezogen.
    """
    if len(G) == 0:
        return None
    TOP_N = 25
    top_emoji_nodes = sorted(node_freq.items(), key=lambda x: x[1], reverse=True)[:TOP_N]
    # Teilgraph mit FESTER Knotenreihenfolge aufbauen. (Früher: G.subgraph(set) – die Reihenfolge
    # einer Python-Menge ändert sich bei jedem Programmstart, dadurch sah das Bild jedes Mal anders aus.)
    subG = nx.Graph()
    subG.add_nodes_from(em for em, freq in top_emoji_nodes if em in G)
    subG.add_edges_from((u, v, d) for u, v, d in G.edges(data=True) if u in subG and v in subG)
    if len(subG) == 0:
        return None
    pos_sub = nx.spring_layout(subG, k=0.5, iterations=100, seed=42)

    edge_x = []
    edge_y = []
    for u, v, d in subG.edges(data=True):
        x0, y0 = pos_sub[u]
        x1, y1 = pos_sub[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    edge_trace = go.Scatter(
        x=edge_x,
        y=edge_y,
        mode='lines',
        line=dict(color='pink', width=1),
        hoverinfo='none'
    )

    node_x = []
    node_y = []
    node_text = []
    node_size = []
    for n in subG.nodes():
        x, y = pos_sub[n]
        node_x.append(x)
        node_y.append(y)
        freq = node_freq.get(n, 1)
        node_text.append(f"{n} (freq {freq})")
        # Wurzel statt linear: sonst wäre das häufigste Emoji (>1000x) über 600 Pixel groß.
        node_size.append(max(20, 4 * np.sqrt(freq)))

    node_trace = go.Scatter(
        x=node_x,
        y=node_y,
        mode='markers+text',
        text=[n for n in subG.nodes()],
        textposition="middle center",
        textfont=dict(size=30),
        hovertext=node_text,
        hoverinfo='text',
        marker=dict(size=node_size, color='lightcoral', line=dict(width=2, color='darkred')),
    )

    fig = go.Figure(data=[edge_trace, node_trace])
    fig.update_layout(
        title="Emoji Pairing 'Love' Map",
        showlegend=False,
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),   # Achsen ausblenden –
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),   # Koordinaten sind bedeutungslos
        margin=dict(t=40, b=0, l=0, r=0),
        height=1200,
    )
    apply_font_sizes(fig)
    return fig


def plot_emoji_sentiment_board(all_emoji_counts: Counter) -> go.Figure:
    """
    "Stimmungs-Tafel": Emojis nach Stimmung (positiv / neutral / negativ) gruppiert,
    Schriftgröße ~ Häufigkeit. Die Zuordnung unten ist bewusst klein gehalten – alles,
    was nicht drinsteht, landet bei "Neutral". Bei Bedarf einfach Emojis ergänzen.
    """
    emoji_sentiment = {
        '😀': 'Positive', '😃': 'Positive', '😂': 'Positive', '❤️': 'Positive', '👍': 'Positive',
        '😞': 'Negative', '😡': 'Negative', '😢': 'Negative', '👎': 'Negative',
        '😐': 'Neutral', '🤔': 'Neutral', '😶': 'Neutral'
    }
    sentiment_groups = {'Positive': [], 'Negative': [], 'Neutral': []}
    for em, cnt in all_emoji_counts.items():
        sentiment = emoji_sentiment.get(em, 'Neutral')   # .get(Schlüssel, Standardwert)
        sentiment_groups[sentiment].append((em, cnt))

    fig = go.Figure()
    x_offsets = {'Positive': -1, 'Neutral': 0, 'Negative': 1}   # x-Position der drei Gruppen
    y_base = 0
    for sentiment, emojis_list in sentiment_groups.items():
        for i, (em, cnt) in enumerate(emojis_list):     # enumerate liefert (laufende Nr., Element)
            fig.add_trace(go.Scatter(
                x=[x_offsets[sentiment] + i*0.05],      # jedes weitere Emoji etwas weiter rechts
                y=[y_base],
                mode='text',
                text=[em],
                textfont=dict(size=10 + 3 * np.sqrt(cnt)),  # Wurzel: sonst bis zu 640 Pixel Schrift
                hoverinfo='text',
                hovertext=f"{em}: {cnt} times ({sentiment})"
            ))
        # Gruppenname über der Gruppe
        fig.add_trace(go.Scatter(
            x=[x_offsets[sentiment]],
            y=[y_base + 0.3],
            mode='text',
            text=[sentiment],
            textfont=dict(size=20, family="Arial Black", color="black"),
            hoverinfo='skip'
        ))

    fig.update_layout(
        title="Emoji Sentiment Mood Board",
        showlegend=False,  # eine Datenreihe je Emoji -> die Legende hätte ~285 Einträge "trace N"
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[-2, 2]),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[-0.5, 0.5]),
        height=1200,
        margin=dict(t=40, b=20, l=20, r=20)
    )
    apply_font_sizes(fig)
    return fig


def plot_chat_timeline_snapshot(daily_counts: pd.DataFrame, move_in: Optional[datetime] = None) -> go.Figure:
    """
    Zeitverlauf: Nachrichten und Emojis pro Tag (Balken) plus 7-Tage-Trend (gestrichelt).
    Wenn move_in angegeben ist, markiert eine senkrechte Linie den Einzugstag.
    """
    fig = go.Figure(data=[
        go.Bar(name='Messages', x=daily_counts['date'], y=daily_counts['messages'], marker_color='blue'),
        go.Bar(name='Emojis', x=daily_counts['date'], y=daily_counts['emojis'], marker_color='orange'),
    ])
    fig.add_trace(go.Scatter(
        x=daily_counts['date'],
        y=daily_counts['messages_trend'],
        mode='lines',
        line=dict(color='blue', width=3, dash='dash'),
        name='Messages Trend'
    ))
    fig.add_trace(go.Scatter(
        x=daily_counts['date'],
        y=daily_counts['emojis_trend'],
        mode='lines',
        line=dict(color='orange', width=3, dash='dash'),
        name='Emojis Trend'
    ))
    fig.update_layout(
        barmode='group',
        title="Chat Timeline Snapshot: Messages & Emojis per Day",
        xaxis_title="Date",
        yaxis_title="Count",
        xaxis_tickformat='%d %b %Y',
        xaxis_tickangle=-45,
        legend_title_text="",
        template="simple_white"
    )
    if move_in is not None:
        # Senkrechte Linie über die ganze Höhe (yref="paper": y=0 unten, y=1 oben, unabhängig von den Daten)
        # + Beschriftung darüber. (Plotlys add_vline mit Beschriftung hat einen Fehler bei Datumsachsen.)
        fig.add_shape(type="line", x0=move_in, x1=move_in, yref="paper", y0=0, y1=1,
                      line=dict(color="#52514e", width=2, dash="dot"))
        fig.add_annotation(x=move_in, y=1, yref="paper", yanchor="bottom", showarrow=False,
                           text=f"🏠 Zusammengezogen ({move_in:%d.%m.%Y})", font=dict(size=18, color="#0b0b0b"))
    apply_font_sizes(fig)
    return fig


# =============================================================================
# 8) VORHER/NACHHER-VERGLEICH ZUM EINZUG
# =============================================================================
# Grundidee wie bei einem Versuch "vor und nach der Umrüstung": zwei gleich lange Messzeiträume
# direkt vor und direkt nach dem Einzug vergleichen. Gleich lang, weil sich der Chat auch ohne
# Umzug über die Jahre verändert hat (Kennenlernphase usw.) – ein kurzer Abstand ist fairer.

def split_move_in(df: pd.DataFrame, move_in: datetime) -> Optional[dict]:
    """
    Teilt den Chat in "vorher" und "nachher".
        nachher = Einzug bis letzte Nachricht
        vorher  = gleich langer Zeitraum direkt vor dem Einzug
    Rückgabe: dict mit beiden Teiltabellen, Grenzen und Länge in Tagen –
    oder None, wenn der Einzug nach der letzten Nachricht liegt.
    """
    end = df["datetime"].max()
    if move_in >= end:
        return None
    span = end - move_in                                   # Länge des "nachher"-Zeitraums
    start = max(move_in - span, df["datetime"].min())       # nicht vor Beginn des Chats
    return {
        "move_in": move_in, "start": start, "end": end,
        "before": df[(df["datetime"] >= start) & (df["datetime"] < move_in)],
        "after": df[df["datetime"] >= move_in],
        "days_before": (move_in - start).total_seconds() / 86400,   # 86400 s = 1 Tag
        "days_after": span.total_seconds() / 86400,
    }


def _period_labels(split: dict) -> Tuple[str, str]:
    """Legenden-Texte, z. B. 'vorher (15.08.25–31.03.26)' und 'nachher (01.04.26–15.11.26)'.
    (Der Unterstrich am Namensanfang heißt: Hilfsfunktion, nur innerhalb dieser Datei gedacht.)"""
    return (f"vorher ({split['start']:%d.%m.%y}–{split['move_in'] - pd.Timedelta(days=1):%d.%m.%y})",
            f"nachher ({split['move_in']:%d.%m.%y}–{split['end']:%d.%m.%y})")


def plot_move_in_overview(split: Optional[dict]) -> Optional[go.Figure]:
    """Gruppierte Balken: Ø Nachrichten, Emojis und Medien pro Tag – vorher vs. nachher."""
    if split is None:
        return None
    metrics = ["Nachrichten", "Emojis", "Medien (Fotos etc.)"]
    fig = go.Figure()
    # Schleife zweimal: einmal "vorher" (blau), einmal "nachher" (orange).
    for (part, days), label, color in zip([(split["before"], split["days_before"]), (split["after"], split["days_after"])],
                                          _period_labels(split), [COLOR_BEFORE, COLOR_AFTER]):
        # Durch die Anzahl Tage teilen -> Werte pro Tag (vergleichbar, auch wenn Zeiträume abweichen)
        vals = [len(part) / days, part["emoji_count"].sum() / days, part["is_media"].sum() / days]
        fig.add_trace(go.Bar(x=metrics, y=vals, name=label, marker_color=color,
                             text=[f"{v:.1f}".replace(".", ",") for v in vals], textposition="outside", textfont_size=18,
                             # hovertemplate = Text beim Darüberfahren mit der Maus; %{y:.2f} = y-Wert, 2 Nachkommastellen
                             hovertemplate="%{x}: %{y:.2f} pro Tag<extra>" + label + "</extra>"))
    fig.update_layout(title="Ø pro Tag – vor und nach dem Einzug", yaxis_title="pro Tag",
                      barmode="group", bargap=0.3, bargroupgap=0.05, template="simple_white")
    apply_font_sizes(fig)
    return fig


def plot_move_in_heatmap_diff(split: Optional[dict]) -> Optional[go.Figure]:
    """
    Differenz-Heatmap Stunde x Wochentag: Nachrichten pro Woche NACHHER minus VORHER.
    Rot = jetzt mehr, Blau = jetzt weniger, Grau = unverändert.
    Umgerechnet auf "pro Woche", damit die Werte anschaulich sind (z. B. "-3 Nachrichten/Woche").
    """
    if split is None:
        return None

    def per_week(part, days):
        """Matrix 24 Stunden x 7 Wochentage mit Nachrichten pro Woche."""
        t = part.groupby(["hour", "weekday"]).size().unstack(fill_value=0)
        return t.reindex(index=range(24), columns=range(7), fill_value=0) / (days / 7)

    b = per_week(split["before"], split["days_before"])
    a = per_week(split["after"], split["days_after"])
    diff = a - b                                          # Matrix minus Matrix, elementweise
    lim = float(np.abs(diff.values).max()) or 1.0         # symmetrische Farbskala: -lim ... +lim
    # Text für den Mauszeiger je Zelle (np.vectorize wendet die Funktion auf jedes Matrixelement an)
    hover = np.vectorize(lambda x, y: f"vorher {x:.1f} · nachher {y:.1f} pro Woche")(b.values, a.values)
    fig = go.Figure(go.Heatmap(
        z=diff.values, x=WEEKDAYS_DE, y=[f"{h}:00" for h in range(24)],
        colorscale=DIVERGING_SCALE, zmid=0, zmin=-lim, zmax=lim, xgap=2, ygap=2,   # zmid=0 -> 0 ist grau
        customdata=hover, hovertemplate="%{x} %{y}<br>%{customdata}<br>Differenz %{z:+.1f}<extra></extra>",
        colorbar=dict(title="Nachrichten/Woche<br>nachher − vorher"),
    ))
    fig.update_layout(title="Wann schreiben wir jetzt mehr (rot) oder weniger (blau)?", template="simple_white")
    fig.update_yaxes(autorange="reversed")
    apply_font_sizes(fig)
    return fig


def plot_move_in_hourly(split: Optional[dict]) -> Optional[go.Figure]:
    """
    Zwei Linien: Anteil der Nachrichten je Uhrzeit (in %), vorher vs. nachher.
    Anteile statt absoluter Zahlen -> zeigt die VERSCHIEBUNG über den Tag, unabhängig davon,
    dass insgesamt weniger geschrieben wird.
    """
    if split is None:
        return None
    fig = go.Figure()
    for part, label, color in zip([split["before"], split["after"]], _period_labels(split), [COLOR_BEFORE, COLOR_AFTER]):
        # value_counts(normalize=True) = relative Häufigkeit (Summe = 1), *100 -> Prozent
        share = part["hour"].value_counts(normalize=True).reindex(range(24), fill_value=0) * 100
        fig.add_trace(go.Scatter(x=list(range(24)), y=share.values, name=label, mode="lines+markers",
                                 line=dict(color=color, width=2), marker=dict(size=8),
                                 hovertemplate="%{x}:00 Uhr: %{y:.1f} %<extra>" + label + "</extra>"))
    fig.update_layout(title="Über den Tag verteilt – Anteil der Nachrichten pro Stunde",
                      xaxis=dict(title="Uhrzeit", tickmode="array", tickvals=list(range(0, 24, 2)),
                                 ticktext=[f"{h}:00" for h in range(0, 24, 2)]),
                      yaxis_title="% der Nachrichten", hovermode="x unified", template="simple_white")
    apply_font_sizes(fig)
    return fig


def plot_move_in_people(split: Optional[dict]) -> Optional[go.Figure]:
    """
    Zwei Diagramme nebeneinander:
      links:  Antwortzeit je Person (MEDIAN – robust gegen Ausreißer wie eine Nacht Funkstille)
      rechts: Anteil der Nachrichten je Person
    jeweils vorher vs. nachher.
    """
    if split is None:
        return None
    users = sorted(set(split["before"]["user"]) | set(split["after"]["user"]))
    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.12,
                        subplot_titles=("Antwortzeit (Median, Minuten)", "Anteil der Nachrichten (%)"))
    for i, (part, label, color) in enumerate(zip([split["before"], split["after"]], _period_labels(split),
                                                 [COLOR_BEFORE, COLOR_AFTER])):
        rt, _ = compute_response_times(part)     # "_" = zweiten Rückgabewert (Mittelwert) nicht verwenden
        med = [float(np.median(rt[u])) if rt.get(u) else np.nan for u in users]
        share = part["user"].value_counts(normalize=True).reindex(users, fill_value=0) * 100
        # legendgroup: beide Balken derselben Periode gehören zusammen (ein Klick in der Legende blendet beide aus)
        fig.add_trace(go.Bar(x=users, y=med, name=label, marker_color=color, legendgroup=label,
                             text=[f"{v:.0f} min" for v in med], textposition="outside", textfont_size=18,
                             hovertemplate="%{x}: %{y:.1f} min<extra>" + label + "</extra>"), row=1, col=1)
        fig.add_trace(go.Bar(x=users, y=share.values, name=label, marker_color=color, legendgroup=label,
                             showlegend=False, text=[f"{v:.0f} %" for v in share.values], textposition="outside", textfont_size=18,
                             hovertemplate="%{x}: %{y:.1f} %<extra>" + label + "</extra>"), row=1, col=2)
    fig.update_layout(title="Wer antwortet wie schnell, wer schreibt wie viel?", barmode="group",
                      bargap=0.3, bargroupgap=0.05, template="simple_white")
    apply_font_sizes(fig)
    fig.update_annotations(font_size=20)   # Überschriften der Teildiagramme
    fig.update_xaxes(tickfont_size=18)     # apply_font_sizes erwischt nur die Achsen des ersten Teildiagramms
    fig.update_yaxes(tickfont_size=18)
    return fig


def _rank_changes(before: Counter, after: Counter, n_before: int, n_after: int,
                  min_total: int, top_n: int) -> pd.DataFrame:
    """
    Findet, was seit dem Einzug am stärksten zu- bzw. abgenommen hat (für Emojis und Wörter).

    Vorgehen:
      1. Relative Häufigkeit je Zeitraum: Anzahl / Gesamtanzahl (vorher bzw. nachher).
      2. Verhältnis nachher/vorher -> "Faktor", z. B. 3 = dreimal so häufig, 0.5 = halb so häufig.
         +1 im Zähler und Nenner ("Glättung"): Sonst wäre ein Wort, das vorher 0x vorkam,
         unendlich-fach häufiger. Nebenwirkung: echte Faktoren werden leicht unterschätzt.
      3. log2 des Faktors: macht Zu- und Abnahme symmetrisch (2x mehr = +1, 2x weniger = -1),
         ähnlich wie Dezibel bei Pegeln.
      4. Seltenes (weniger als min_total Vorkommen insgesamt) wird ignoriert – sonst wären die
         Ranglisten voller Zufallstreffer (wie Messrauschen bei kleinen Stichproben).
    Rückgabe: Tabelle mit den top_n größten Abnahmen und den top_n größten Zunahmen.
    """
    rows = []
    for item in set(before) | set(after):
        b, a = before.get(item, 0), after.get(item, 0)
        if a + b < min_total:
            continue
        ratio = ((a + 1) / (n_after + 1)) / ((b + 1) / (n_before + 1))
        rows.append({"item": item, "before": b, "after": a, "log2": np.log2(ratio), "ratio": ratio})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Bei Gleichstand alphabetisch sortieren – sonst hinge die Reihenfolge von der (zufälligen)
    # Reihenfolge der Python-Menge oben ab und wäre bei jedem Programmstart anders.
    df = df.sort_values(["log2", "item"])
    # head = die ersten (stärkste Abnahme), tail = die letzten (stärkste Zunahme)
    return pd.concat([df.head(top_n), df.tail(top_n)]).drop_duplicates("item")


def _plot_changes(ch: pd.DataFrame, title: str, unit: str, label_size: int) -> Optional[go.Figure]:
    """
    Liegendes Balkendiagramm zu _rank_changes: nach rechts (orange) = häufiger seit dem Einzug,
    nach links (blau) = seltener. Die x-Achse ist logarithmisch beschriftet (2x, 4x, 8x, ...).
    """
    if ch.empty:
        return None
    ch = ch.sort_values(["log2", "item"])

    def fmt(f):
        """Zahl im deutschen Format; ab 10 ohne Nachkommastelle (z. B. '3,2' bzw. '73')."""
        return f"{f:.0f}" if f >= 10 else f"{f:.1f}".replace(".", ",")

    txt = [f"{fmt(r)}× häufiger" if r >= 1 else f"{fmt(1/r)}× seltener" for r in ch["ratio"]]
    fig = go.Figure(go.Bar(
        x=ch["log2"], y=ch["item"], orientation="h",
        marker_color=[COLOR_AFTER if v > 0 else COLOR_BEFORE for v in ch["log2"]],
        text=txt, textposition="outside", cliponaxis=False, textfont_size=16,
        # customdata = Zusatzdaten je Balken für den Mauszeiger-Text (Anzahl vorher/nachher)
        customdata=np.stack([ch["before"], ch["after"]], axis=1),
        hovertemplate="%{y}: vorher %{customdata[0]}×, nachher %{customdata[1]}× " + unit + "<extra></extra>",
    ))
    # Achsenbereich symmetrisch um 0, mit 35 % Reserve für die Beschriftung am Balkenende
    lim = float(np.abs(ch["log2"]).max()) * 1.35
    # Achsenteilung: ganze log2-Schritte (1 Schritt = Faktor 2); bei großen Werten nur jeden zweiten
    max_tick = int(np.ceil(np.abs(ch["log2"]).max()))
    ticks = [k for k in range(-max_tick, max_tick + 1) if k % (1 if max_tick <= 4 else 2) == 0]
    fig.update_layout(title=title, template="simple_white", showlegend=False,
                      height=max(500, 45 * len(ch) + 150),    # Höhe wächst mit der Anzahl Balken
                      xaxis=dict(title="← seltener seit dem Einzug · häufiger seit dem Einzug →",
                                 range=[-lim, lim], zeroline=True, zerolinecolor="#52514e",
                                 tickvals=ticks, ticktext=[f"{2**abs(k)}×" if k else "gleich" for k in ticks]))
    apply_font_sizes(fig)
    fig.update_yaxes(tickfont=dict(size=label_size))
    return fig


def plot_move_in_emoji_changes(split: Optional[dict], top_n: int = 8) -> Optional[go.Figure]:
    """Welche Emojis werden seit dem Einzug häufiger/seltener benutzt (je Nachricht gerechnet)?"""
    if split is None:
        return None
    # Alle Emojis eines Zeitraums zählen (verschachtelte Schleife: jede Nachricht, jedes Emoji darin)
    b = Counter(e for ems in split["before"]["emojis"] for e in ems)
    a = Counter(e for ems in split["after"]["emojis"] for e in ems)
    ch = _rank_changes(b, a, len(split["before"]), len(split["after"]), min_total=10, top_n=top_n)
    return _plot_changes(ch, "Emojis: was seit dem Einzug zu- und abgenommen hat", "verwendet", label_size=28)


def plot_move_in_word_changes(split: Optional[dict], top_n: int = 10) -> Optional[go.Figure]:
    """Welche Wörter kommen seit dem Einzug häufiger/seltener vor (je geschriebenem Wort gerechnet)?"""
    if split is None:
        return None
    stop = set(w.lower() for w in STOPWORDS) | GERMAN_STOPWORDS

    def words(part):
        """Alle Wörter eines Zeitraums zählen – ohne Fotos, Links und Füllwörter, alles klein geschrieben."""
        text = URL_RE.sub(" ", " ".join(part.loc[~part["is_media"], "message"]).lower())
        return Counter(w for w in WORD_RE.findall(text) if w not in stop)

    b, a = words(split["before"]), words(split["after"])
    ch = _rank_changes(b, a, sum(b.values()), sum(a.values()), min_total=15, top_n=top_n)
    return _plot_changes(ch, "Wörter: was seit dem Einzug zu- und abgenommen hat", "geschrieben", label_size=18)


# =============================================================================
# 9) HTML-REPORT ZUSAMMENBAUEN
# =============================================================================

# Kopf der HTML-Datei. Der CSS-Teil sorgt beim Drucken/PDF-Export für einen Seitenumbruch
# vor jedem Abschnitt (class 'pagebreak').
HTML_TEMPLATE_HEAD = """
<html>
<head>
<meta charset='utf-8'>
<title>WhatsApp Emoji Report</title>
<style>
@media print {
    .pagebreak {
        page-break-after: always;
    }
}
</style>
</head>
<body>
"""


def fig_to_html_div(fig):
    """Plotly-Figure -> HTML-Schnipsel. include_plotlyjs='cdn': Die Plotly-Bibliothek wird beim
    Öffnen aus dem Internet geladen (Report-Datei bleibt klein, braucht aber Internet zum Anzeigen)."""
    if fig is None:
        return "<p><i>No data for this plot.</i></p>"
    return pio.to_html(fig, full_html=False, include_plotlyjs='cdn')


def build_html_report(output_path: str,
                      df: pd.DataFrame,
                      users: List[str],
                      figs_map: Dict[str, Optional[go.Figure]],
                      wordcloud_images: Dict[str,str],
                      extra_info: dict):
    """
    Schreibt den Report als HTML-Datei.

    Der Aufbau steht in der Liste "sections": je Abschnitt (Überschrift, Diagramm-Schlüssel).
    Der Schlüssel verweist auf ein Diagramm in figs_map (siehe build_plot_registry);
    None bedeutet "hier die Wortwolken zeigen".
    -> Überschriften ändern oder Abschnitte umsortieren: einfach die Liste bearbeiten.
    Achtung: Die Zahlen in den Überschriften sind fest eingetippt und stammen aus der Auswertung
    vom 16.11.2026 (Chat bis 15.11.2026). Bei einem neuen Export müssen sie ggf. angepasst werden.
    """
    html_parts = []      # HTML-Stücke sammeln, am Ende zu einem Text zusammenfügen
    html_parts.append(HTML_TEMPLATE_HEAD)
    html_parts.append(f"<h1>WhatsApp Emoji & Chat Report</h1>")
    html_parts.append(f"<p>Messages parsed: {len(df)} · Users: {len(users)} · Date range: {df['datetime'].min()} — {df['datetime'].max()}</p>")

    # (Überschrift, Diagramm-Schlüssel) in Report-Reihenfolge
    sections = [
        ("Fangen wir künstlerisch an: Wenn unser Chat jeweils ein Kunstwerk wäre …", None),
        ("Unser Verkehr hat über die Zeit ordentlich zugenommen – von knapp 5 Nachrichten am Tag Ende 2023 "
         "auf über 17 Ende 2025. Aber was war denn am 26.10.2024 los? 234 Nachrichten – 233 davon Fotos. "
         "Da hast du wohl das ganze Album rübergeschickt 📸", "chat_timeline_snapshot"),
        ("Zu welcher Tageszeit in der Woche waren wir denn am fleißigsten am Texten? "
         "Sonntagabend um 19 Uhr ist unsere Chat-Primetime", 'message_heatmap'),
        ("…und am Emojis verschicken? Auch hier gewinnt der Sonntagabend, nur eine Stunde später", 'emoji_heatmap'),
        (f"Dabei waren unsere Top {TOP_EMOJI_COUNT} Emojis diese – 😂 mit 1.067 Mal unangefochten auf Platz 1, "
         f"🥰 mit 589 Mal auf Platz 2", 'top_emojis'),
        ("Aber wie lange musste ich denn immer so auf deine Antwort warten? Meistens geht's fix: "
         "Mehr als ein Drittel deiner Antworten kommt in unter 5 Minuten, bei mir sind es knapp 30 %", 'response_time_hist'),
        ("Jetzt schwarz auf weiß: Im Schnitt warte ich 14 Minuten länger auf dich (131 zu 117 Minuten) … "
         "aber zu deiner Ehrenrettung: Meistens bist du schneller – im Median antwortest du nach 14 Minuten, "
         "ich erst nach 20. Ein paar lange Funkstillen ziehen deinen Schnitt hoch", 'avg_response_time'),
        ("À propos länger: Deine Nachrichten sind es auch – im Schnitt 62 Zeichen bei dir, 42 bei mir. "
         "That's what the statistics says", 'message_length'),
        ("Ich lenke ab, zurück zu den Emojis: Welche tauchen gern gemeinsam in einer Nachricht auf? "
         "Unser Traumpaar ist 😂 + 🙈 (15 Mal), dicht gefolgt von 🥰 + 🥹 (14 Mal)", "emoji_love_map"),
        ("Unser Spirit Animal ist und bleibt Tim '😂' – 1.067 Mal, 806 davon gehen auf meine Kappe", "emoji_sentiment_board"),
        ("An den einzelnen Tagen kann man es nicht mehr ablesen, wer bei den Emojis führt …", 'emoji_usage_over_time'),
        ("Hier mal über die Tageszeit verteilt: Vor 7 Uhr kamen von dir 109 Emojis, von mir gerade mal 9 – "
         "dein Wecker klingelt also um kurz nach 6, meiner frühestens um halb 8 :)", 'emoji_time_violin'),
        ("Und wer hat denn nun die meisten Emojis verschickt? Du natürlich – 3.649 zu 1.914, "
         "fast doppelt so viele wie ich", 'total_emojis_per_user'),
    ]

    # Kapitel "Seit wir zusammen wohnen" nur, wenn ein Einzugsdatum gesetzt ist (siehe --move-in)
    split = extra_info.get("move_in_split")
    if split is not None:
        sections += [
            (f"🏠 Und dann kam der {split['move_in']:%d.%m.%Y}: Wir wohnen zusammen! Was hat sich seitdem verändert? "
             f"Erstmal: Wir schreiben uns weniger – 11,6 statt 16 Nachrichten am Tag. "
             f"Kein Wunder, ich kann's dir ja jetzt einfach sagen.<br>"
             f"<small>Verglichen werden die {split['days_after']:.0f} Tage seit dem Einzug mit den gleich vielen Tagen davor "
             f"({split['start']:%d.%m.%Y} – {split['move_in'] - pd.Timedelta(days=1):%d.%m.%Y}).</small>", 'move_in_overview'),
            ("Vor allem abends ist es ruhig geworden: Freitag und Sonntag um 19 Uhr ist fast alles blau – "
             "da sitzen wir ja jetzt zusammen auf dem Sofa. Dafür schreiben wir uns nachmittags unter der Woche etwas öfter", 'move_in_heatmap_diff'),
            ("Unser Chat ist zum Tagesgeschäft geworden: Fast die Hälfte unserer Nachrichten (49 %) schreiben wir jetzt "
             "zwischen 9 und 17 Uhr, vorher waren es 38 %. Abends ist der Anteil von 48 % auf 34 % gefallen", 'move_in_hourly'),
            ("Ich bin schneller geworden: 10 statt 16 Minuten bis zu meiner Antwort. Du lässt dir jetzt etwas mehr Zeit "
             "(9 statt 7 Minuten) – du weißt ja, wo du mich findest. Und ich hole beim Schreiben auf: "
             "47 % der Nachrichten kommen jetzt von mir", 'move_in_people'),
            ("Seit dem Einzug ist ♥️ von 0 auf 36 Mal gesprungen und 😍 kommt fast sechsmal so oft – "
             "hinter 🫣 versteckt sich dafür kaum noch jemand", 'move_in_emoji_changes'),
            ("Und worüber wir jetzt schreiben: Tschüss Immoscout, Kaltmiete und Besichtigungen – "
             "hallo Pupsi (54 Mal!), Einkaufen und Rückweg", 'move_in_word_changes'),
        ]

    first = True
    for title, key in sections:
        if not first:
            html_parts.append("<div class='pagebreak'></div>")   # Seitenumbruch beim Drucken
        first = False

        html_parts.append(f"<hr><h2>{title}</h2>")

        if key is None:
            # Wortwolken (Bild ist als Text eingebettet, siehe generate_wordcloud_images)
            for user, img_b64 in wordcloud_images.items():
                html_parts.append(f"<h3>{user}</h3>")
                html_parts.append(
                    f"<img src='{img_b64}' alt='Wordcloud for {user}' "
                    "style='max-width:100%;height:auto;"
                    "border:1px solid #ddd;padding:4px;margin-bottom:10px;'>"
                )
        else:
            # .get liefert None, wenn das Diagramm nicht erzeugt wurde -> "No data for this plot."
            fig_html = fig_to_html_div(figs_map.get(key))
            html_parts.append(fig_html)

    html_parts.append("</body></html>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))
    print(f"Report written to {output_path}. Open this file in a browser to explore the interactive plots.")


# =============================================================================
# 10) DIAGRAMM-REGISTER – welcher Schlüssel erzeugt welches Diagramm
# =============================================================================

def build_plot_registry(prepared_data: dict) -> Dict[str, callable]:
    """
    Verzeichnis aller Diagramme: Schlüssel (Name) -> Funktion, die das Diagramm erzeugt.

    Die "lambda: ..." sind Funktionen, die erst beim Aufruf rechnen. So werden mit
    --plots nur die gewünschten Diagramme wirklich berechnet.
    Neues Diagramm hinzufügen:
      1. plot_xyz-Funktion schreiben,
      2. hier eine Zeile "xyz": lambda: plot_xyz(prepared_data[...]) ergänzen,
      3. in build_html_report einen Abschnitt (Überschrift, "xyz") in "sections" eintragen.
    """
    registry = {
        "total_emojis_per_user": lambda: plot_total_emojis_per_user(prepared_data["compute_emoji"]["total_emoji_counts"]),
        "emoji_time_violin": lambda: plot_emoji_time_violin(prepared_data["compute_time"]["df_emoji_time"]),
        "emoji_usage_over_time": lambda: plot_emoji_usage_over_time(prepared_data["compute_emoji"]["emoji_by_day_user"]),
        "top_emojis": lambda: plot_top_emojis(prepared_data["compute_emoji"]["top_emojis"]),
        "emoji_heatmap": lambda: plot_emoji_heatmap(prepared_data["compute_time"]["heat_table"]),
        "message_heatmap": lambda: plot_message_heatmap(prepared_data["compute_time"]["msg_heat_table"]),
        "message_length": lambda: plot_message_length(prepared_data["df"]),
        "response_time_hist": lambda: plot_response_time_hist(prepared_data["response_times"]),
        "avg_response_time": lambda: plot_avg_response_time(prepared_data["avg_response_time"]),
        "emoji_network": lambda: plot_emoji_network(prepared_data["G"], prepared_data["pos"]),
        "emoji_love_map": lambda: plot_emoji_love_map(prepared_data["G"], prepared_data["node_freq"]),
        "emoji_sentiment_board": lambda: plot_emoji_sentiment_board(prepared_data["all_emoji_counts"]),
        "chat_timeline_snapshot": lambda: plot_chat_timeline_snapshot(prepared_data["compute_time"]["daily_counts"],
                                                                      prepared_data["move_in"]),
        "move_in_overview": lambda: plot_move_in_overview(prepared_data["move_in_split"]),
        "move_in_heatmap_diff": lambda: plot_move_in_heatmap_diff(prepared_data["move_in_split"]),
        "move_in_hourly": lambda: plot_move_in_hourly(prepared_data["move_in_split"]),
        "move_in_people": lambda: plot_move_in_people(prepared_data["move_in_split"]),
        "move_in_emoji_changes": lambda: plot_move_in_emoji_changes(prepared_data["move_in_split"]),
        "move_in_word_changes": lambda: plot_move_in_word_changes(prepared_data["move_in_split"]),
    }
    return registry


# =============================================================================
# 11) HAUPTPROGRAMM – verbindet alle Schritte der Prozesskette
# =============================================================================

def main():
    # --- Kommandozeilen-Optionen definieren (python whatsapp_analysis.py --help zeigt sie an) ---
    parser = argparse.ArgumentParser(description="WhatsApp interactive report generator (Plotly) — modular")
    parser.add_argument("--input", "-i", nargs="+", default=DEFAULT_INPUTS,     # nargs="+" = eine oder mehrere Dateien
                        help="One or more WhatsApp export files of the same chat (merged; earlier/more precise export first)")
    parser.add_argument("--save-merged", metavar="PATH",
                        help="Also write the merged chat to PATH (WhatsApp export format), e.g. input/chat_merged.txt")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Path to output HTML file")
    parser.add_argument("--move-in", default=MOVE_IN_DATE.strftime("%Y-%m-%d"),
                        help="Date you moved in together (YYYY-MM-DD) for the before/after chapter; 'none' to disable")
    parser.add_argument("--plots", "-p", default="", help="Comma-separated list of plots to generate (keys). If empty, generate all.")
    args = parser.parse_args()

    # --- Schritt 1+2: Einlesen, zusammenführen, anreichern ---
    df = load_chats(args.input)
    if args.save_merged:
        write_chat_file(df, args.save_merged)
    df = enrich_dataframe(df)

    move_in = None if args.move_in.lower() == "none" else datetime.strptime(args.move_in, "%Y-%m-%d")

    # --- Schritt 3: Kennzahlen berechnen ---
    compute_emoji = compute_emoji_counts(df)
    compute_time = compute_time_series_and_heatmaps(df)
    response_times, avg_response_time = compute_response_times(df)
    G, pos, node_freq = build_emoji_cooccurrence_graph(df)
    wordcloud_images = generate_wordcloud_images(df)

    # Emoji-Strichlisten beider Personen zu einer Gesamt-Strichliste addieren
    all_emoji_counts = Counter()
    for user_counts in compute_emoji["user_emojis"].values():
        all_emoji_counts.update(user_counts)

    # Alle Zwischenergebnisse in einem dict bündeln – daraus bedienen sich die Diagramm-Funktionen
    prepared_data = {
        "df": df,
        "users": df["user"].unique().tolist(),
        "compute_emoji": compute_emoji,
        "compute_time": compute_time,
        "response_times": response_times,
        "avg_response_time": avg_response_time,
        "G": G,
        "pos": pos,
        "node_freq": node_freq,
        "wordcloud_images": wordcloud_images,
        "all_emoji_counts": all_emoji_counts,
        "move_in": move_in,
        "move_in_split": split_move_in(df, move_in) if move_in else None,
    }

    # --- Schritt 4: Diagramme erzeugen ---
    registry = build_plot_registry(prepared_data)

    # Welche Diagramme? Mit --plots nur die genannten, sonst alle.
    if args.plots.strip():
        requested = [p.strip() for p in args.plots.split(",") if p.strip()]
    else:
        requested = list(registry.keys())

    figs_map = {}
    for key in requested:
        if key not in registry:
            print(f"[warning] Unknown plot key '{key}', skipping.")
            continue
        try:
            figs_map[key] = registry[key]()      # hier wird das Diagramm tatsächlich berechnet
        except Exception as e:
            # Ein fehlerhaftes Diagramm soll nicht den ganzen Report verhindern
            print(f"[error] Failed to generate plot '{key}': {e}")
            figs_map[key] = None

    # --- Schritt 5: HTML schreiben (fehlende Diagramme erscheinen als "No data for this plot.") ---
    build_html_report(
        output_path=args.output,
        df=df,
        users=prepared_data["users"],
        figs_map=figs_map,
        wordcloud_images=wordcloud_images,
        extra_info={"move_in_split": prepared_data["move_in_split"]},
    )


# Wird nur ausgeführt, wenn die Datei direkt gestartet wird (nicht beim "import" aus einem anderen Script).
if __name__ == "__main__":
    main()
