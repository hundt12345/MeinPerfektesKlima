"""Ortsnamen: deutsche Anzeigenamen, Suchnamen und eine tolerante Ortssuche.

Wird zur Laufzeit von der App (``/api/cities/search``) und beim Bau der
Städteliste (``scripts/build_geo.py``) verwendet – beide Seiten normalisieren
Namen deshalb garantiert gleich.

Die Suche findet einen Ort unabhängig von

* Groß-/Kleinschreibung und Akzenten (``malaga`` → Málaga),
* deutscher Umschrift (``muenchen``, ``koeln``, ``duesseldorf``),
* deutschen oder landessprachlichen Namen (``Mailand``/``Milano``,
  ``Kopenhagen``/``Copenhagen``, ``Genf``/``Genève``),
* Füllwörtern und Klammern (``Mülheim an der Ruhr`` → „Mülheim (Ruhr)“).
"""
from __future__ import annotations

import unicodedata

# Buchstaben, die Unicode nicht in Grundbuchstabe + Akzent zerlegt.
_SPECIAL = str.maketrans({
    "ß": "ss", "ø": "o", "å": "a", "æ": "ae", "œ": "oe", "ł": "l", "đ": "d", "ð": "d",
    "þ": "th", "ı": "i", "ʼ": "", "’": "", "'": "", "`": "", "´": "",
})
# Deutsche Umschrift, wie man sie ohne Umlaut-Tastatur tippt.
_GERMAN = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})

# Füllwörter, die in Ortsnamen oft weggelassen oder anders geschrieben werden.
STOPWORDS = frozenset({
    "an", "am", "der", "die", "das", "den", "im", "in", "bei", "ob", "auf", "vor",
    "de", "del", "della", "di", "da", "do", "la", "le", "les", "el", "of", "the", "upon", "on", "sur",
})


def fold(text: str) -> str:
    """Kleinschreibung ohne Akzente; Satzzeichen werden zu Leerzeichen."""
    text = unicodedata.normalize("NFKD", str(text or "")).lower().translate(_SPECIAL)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def variants(text: str) -> list[str]:
    """Suchformen eines Namens: ohne Akzente und – falls abweichend – mit ae/oe/ue."""
    plain = fold(text)
    german = fold(str(text or "").translate(_GERMAN))
    return [plain] if german == plain else [plain, german]


def search_key(city: dict) -> str:
    """Alle Suchformen eines Orts (Anzeigename, Originalname, Aliasse) als ein Text."""
    names = [city.get("name", ""), city.get("name_local", ""), *(city.get("aliases") or [])]
    seen: list[str] = []
    for name in names:
        for form in variants(name):
            if form and form not in seen:
                seen.append(form)
    return " | ".join(seen)


def _tokens(text: str) -> list[str]:
    words = fold(text).split()
    useful = [w for w in words if w not in STOPWORDS]
    return useful or words


def match_rank(key: str, query: str) -> int | None:
    """Wie gut passt ``query`` zu einem Suchschlüssel? Kleiner = besser, None = gar nicht.

    0  ein Name ist genau die Eingabe
    1  ein Name beginnt mit der Eingabe
    2  jedes Wort der Eingabe ist der Anfang eines Worts im Namen
    3  die Eingabe steckt irgendwo im Namen (nur ab drei Zeichen)
    """
    forms = key.split(" | ")
    for q in dict.fromkeys(variants(query)):
        if not q:
            continue
        if q in forms:
            return 0
    best = None
    for q in dict.fromkeys(variants(query)):
        if not q:
            continue
        if any(form.startswith(q) for form in forms):
            return 1
        tokens = _tokens(q)
        for form in forms:
            words = form.split()
            if tokens and all(any(w.startswith(t) for w in words) for t in tokens):
                best = 2
                break
        if best is None and len(q) >= 3 and any(q in form for form in forms):
            best = 3
    return best


def search(cities: list[dict], keys: list[str], query: str, limit: int = 8,
           accept=None) -> list[tuple[dict, int]]:
    """Beste Treffer: zuerst nach Passgenauigkeit, dann nach Einwohnerzahl."""
    if not fold(query):
        return []
    hits = []
    for city, key in zip(cities, keys):
        if accept is not None and not accept(city):
            continue
        rank = match_rank(key, query)
        if rank is not None:
            hits.append((rank, -int(city.get("pop", 0) or 0), city))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [(city, rank) for rank, _pop, city in hits[: max(1, int(limit))]]


def matched_alias(city: dict, query: str) -> str | None:
    """Der eingegebene Zweitname (z. B. „Milano“ für Mailand), falls er nicht der Anzeigename ist."""
    q = fold(query)
    if not q:
        return None
    for name in [city.get("name_local", ""), *(city.get("aliases") or [])]:
        if name and name != city.get("name") and any(f.startswith(q) for f in variants(name)):
            if not any(f.startswith(q) for f in variants(city.get("name", ""))):
                return name
    return None


# ---------------------------------------------------------------------------
# Deutsche Anzeigenamen für bekannte Städte (Schlüssel = Name in GeoNames).
# Nur gebräuchliche deutsche Namen – sonst bleibt der Name aus GeoNames stehen.
GERMAN_NAMES = {
    "İstanbul": "Istanbul", "Moscow": "Moskau", "Saint Petersburg": "Sankt Petersburg",
    "Kiev": "Kiew", "İzmir": "Izmir", "Rome": "Rom", "Bucharest": "Bukarest",
    "Warsaw": "Warschau", "Vienna": "Wien", "Kharkiv": "Charkiw", "Belgrade": "Belgrad",
    "Munich": "München", "Milano": "Mailand", "Prague": "Prag", "Yerevan": "Jerewan",
    "Rostov-na-Donu": "Rostow am Don", "Nizhniy Novgorod": "Nischni Nowgorod", "Tbilisi": "Tiflis",
    "Dnipropetrovsk": "Dnipro", "Donetsk": "Donezk", "Brussels": "Brüssel", "Volgograd": "Wolgograd",
    "Napoli": "Neapel", "Zaporizhzhya": "Saporischschja", "Kraków": "Krakau", "L'viv": "Lwiw",
    "Athens": "Athen", "Genoa": "Genua", "Lisbon": "Lissabon", "The Hague": "Den Haag",
    "Thessaloníki": "Thessaloniki", "Florence": "Florenz", "Nice": "Nizza", "Strasbourg": "Straßburg",
    "Genève": "Genf", "Århus": "Aarhus", "Liège": "Lüttich", "Brugge": "Brügge",
    "Luxembourg": "Luxemburg", "Padova": "Padua", "Siracusa": "Syrakus", "Bolzano": "Bozen",
    "Trento": "Trient", "Trieste": "Triest", "Venice": "Venedig", "Mantova": "Mantua",
    "Merano": "Meran", "Irákleion": "Heraklion", "Chaniá": "Chania", "Ródos": "Rhodos",
    "Kérkyra": "Korfu", "Pátra": "Patras", "Copenhagen": "Kopenhagen", "Nicosia": "Nikosia",
    "Sevastopol": "Sewastopol", "Mercin": "Mersin", "Pilsen": "Pilsen",
    "Gasteiz / Vitoria": "Vitoria-Gasteiz", "Donostia / San Sebastián": "San Sebastián",
}

# Weitere Suchnamen (lokal, englisch, historisch deutsch, Insel/Region → Hauptort).
EXTRA_ALIASES = {
    "İstanbul": ["Konstantinopel"], "Moscow": ["Moskva", "Moscow"],
    "Saint Petersburg": ["Sankt-Peterburg", "St. Petersburg", "Petersburg", "Leningrad"],
    "Kiev": ["Kyiv", "Kyjiw"], "Rome": ["Roma"], "Bucharest": ["București"], "Warsaw": ["Warszawa"],
    "Vienna": ["Vienna"], "Kharkiv": ["Charkow"], "Belgrade": ["Beograd"], "Munich": ["Munich"],
    "Milano": ["Milan"], "Prague": ["Praha"], "Tbilisi": ["Tbilissi"], "Brussels": ["Bruxelles", "Brussel"],
    "Napoli": ["Naples"], "Kraków": ["Krakow"], "L'viv": ["Lemberg", "Lviv", "Lwow"], "Athens": ["Athina"],
    "Genoa": ["Genova"], "Lisbon": ["Lisboa"], "The Hague": ["Haag", "s-Gravenhage"],
    "Florence": ["Firenze"], "Nice": ["Nice"], "Genève": ["Geneva"], "Århus": ["Arhus"],
    "Liège": ["Luik"], "Brugge": ["Bruges"], "Luxembourg": ["Luxembourg"], "Turin": ["Torino"],
    "Venice": ["Venezia"], "Bolzano": ["Bolzano"], "Trieste": ["Trieste"], "Zürich": ["Zurich"],
    "Köln": ["Cologne"], "Nürnberg": ["Nuremberg"], "Göteborg": ["Gothenburg"], "Antwerpen": ["Antwerp"],
    "Gent": ["Ghent"], "Sevilla": ["Seville"], "Zaragoza": ["Saragossa"], "A Coruña": ["La Coruña"],
    "Copenhagen": ["København"], "Wrocław": ["Breslau"], "Gdańsk": ["Danzig"], "Poznań": ["Posen"],
    "Szczecin": ["Stettin"], "Brno": ["Brünn"], "Ostrava": ["Ostrau"], "Olomouc": ["Olmütz"],
    "Pilsen": ["Plzeň"], "Karlovy Vary": ["Karlsbad"], "České Budějovice": ["Budweis"],
    "Liberec": ["Reichenberg"], "Ústí nad Labem": ["Aussig"], "Bratislava": ["Pressburg"],
    "Cluj-Napoca": ["Klausenburg"], "Timişoara": ["Temeswar"], "Braşov": ["Kronstadt"],
    "Sibiu": ["Hermannstadt"], "Iaşi": ["Jassy"], "Ljubljana": ["Laibach"], "Maribor": ["Marburg"],
    "Zagreb": ["Agram"], "Kaliningrad": ["Königsberg"], "Tartu": ["Dorpat"], "Tallinn": ["Reval"],
    "Vilnius": ["Wilna"], "Kaunas": ["Kauen"], "Klaipėda": ["Memel"], "Chişinău": ["Kischinau"],
    "Yerevan": ["Eriwan"], "Thessaloníki": ["Saloniki"], "Irákleion": ["Iraklio", "Kreta"],
    "Chaniá": ["Kreta"], "Ródos": ["Rodos"], "Kérkyra": ["Corfu"], "Pátra": ["Patra"],
    "Nicosia": ["Lefkosia", "Zypern"], "Limassol": ["Lemesos", "Zypern"], "Larnaca": ["Zypern"],
    "Paphos": ["Zypern"], "Palma": ["Palma de Mallorca", "Mallorca"], "Ibiza": ["Eivissa"],
    "Cagliari": ["Sardinien"], "Ajaccio": ["Korsika"], "Bastia": ["Korsika"],
    "Palermo": ["Sizilien"], "Catania": ["Sizilien"], "Málaga": ["Malaga"],
    "Reykjavík": ["Island"], "Birkirkara": ["Malta", "Valletta"], "Mercin": ["Mercin"],
    "Donostia / San Sebastián": ["Donostia"], "Gasteiz / Vitoria": ["Vitoria"],
    "Frankfurt am Main": ["Frankfurt"], "Halle (Saale)": ["Halle an der Saale"],
    "Helsinki": ["Helsingfors"], "Porto": ["Oporto"], "Bilbao": ["Bilbo"], "Odessa": ["Odesa"],
}
