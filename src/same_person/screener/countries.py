"""Country names, ISO codes and nationality words mapped to one canonical name."""

from __future__ import annotations

import re

from same_person.screener.names import fold

# canonical name: (ISO alpha-2 code, other names and nationality words)
_COUNTRIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "Afghanistan": ("AF", ("afghan",)),
    "Albania": ("AL", ("albanian",)),
    "Algeria": ("DZ", ("algerian",)),
    "Angola": ("AO", ("angolan",)),
    "Argentina": ("AR", ("argentine", "argentinian")),
    "Armenia": ("AM", ("armenian",)),
    "Australia": ("AU", ("australian",)),
    "Austria": ("AT", ("austrian",)),
    "Azerbaijan": ("AZ", ("azerbaijani",)),
    "Bahamas": ("BS", ("bahamian", "the bahamas")),
    "Bahrain": ("BH", ("bahraini",)),
    "Bangladesh": ("BD", ("bangladeshi",)),
    "Belarus": ("BY", ("belarusian",)),
    "Belgium": ("BE", ("belgian",)),
    "Benin": ("BJ", ("beninese",)),
    "Bolivia": ("BO", ("bolivian",)),
    "Bosnia and Herzegovina": ("BA", ("bosnian", "bosnia")),
    "Botswana": ("BW", ("botswanan",)),
    "Brazil": ("BR", ("brazilian", "brasil")),
    "British Virgin Islands": ("VG", ("bvi",)),
    "Bulgaria": ("BG", ("bulgarian",)),
    "Burkina Faso": ("BF", ("burkinabe",)),
    "Cambodia": ("KH", ("cambodian",)),
    "Cameroon": ("CM", ("cameroonian",)),
    "Canada": ("CA", ("canadian",)),
    "Cayman Islands": ("KY", ("caymanian",)),
    "Chad": ("TD", ("chadian",)),
    "Chile": ("CL", ("chilean",)),
    "China": ("CN", ("chinese", "people's republic of china", "prc")),
    "Colombia": ("CO", ("colombian",)),
    "Congo": ("CG", ("congolese", "republic of the congo")),
    "Costa Rica": ("CR", ("costa rican",)),
    "Cote d'Ivoire": ("CI", ("ivorian", "ivory coast", "cote divoire")),
    "Croatia": ("HR", ("croatian",)),
    "Cuba": ("CU", ("cuban",)),
    "Cyprus": ("CY", ("cypriot",)),
    "Czech Republic": ("CZ", ("czech", "czechia")),
    "Democratic Republic of the Congo": ("CD", ("drc", "dr congo")),
    "Denmark": ("DK", ("danish",)),
    "Dominican Republic": ("DO", ("dominican",)),
    "Ecuador": ("EC", ("ecuadorian",)),
    "Egypt": ("EG", ("egyptian",)),
    "El Salvador": ("SV", ("salvadoran",)),
    "Eritrea": ("ER", ("eritrean",)),
    "Estonia": ("EE", ("estonian",)),
    "Ethiopia": ("ET", ("ethiopian",)),
    "Finland": ("FI", ("finnish",)),
    "France": ("FR", ("french",)),
    "Gambia": ("GM", ("gambian", "the gambia")),
    "Georgia": ("GE", ("georgian",)),
    "Germany": ("DE", ("german", "deutschland")),
    "Ghana": ("GH", ("ghanaian",)),
    "Gibraltar": ("GI", ()),
    "Greece": ("GR", ("greek",)),
    "Guatemala": ("GT", ("guatemalan",)),
    "Guernsey": ("GG", ()),
    "Guinea": ("GN", ("guinean",)),
    "Haiti": ("HT", ("haitian",)),
    "Honduras": ("HN", ("honduran",)),
    "Hong Kong": ("HK", ("hong kong special administrative region of china",)),
    "Hungary": ("HU", ("hungarian",)),
    "Iceland": ("IS", ("icelandic",)),
    "India": ("IN", ("indian",)),
    "Indonesia": ("ID", ("indonesian",)),
    "Iran": ("IR", ("iranian", "islamic republic of iran")),
    "Iraq": ("IQ", ("iraqi",)),
    "Ireland": ("IE", ("irish", "republic of ireland")),
    "Isle of Man": ("IM", ("manx",)),
    "Israel": ("IL", ("israeli",)),
    "Italy": ("IT", ("italian",)),
    "Jamaica": ("JM", ("jamaican",)),
    "Japan": ("JP", ("japanese",)),
    "Jersey": ("JE", ()),
    "Jordan": ("JO", ("jordanian",)),
    "Kazakhstan": ("KZ", ("kazakh", "kazakhstani")),
    "Kenya": ("KE", ("kenyan",)),
    "Kosovo": ("XK", ("kosovar",)),
    "Kuwait": ("KW", ("kuwaiti",)),
    "Kyrgyzstan": ("KG", ("kyrgyz",)),
    "Latvia": ("LV", ("latvian",)),
    "Lebanon": ("LB", ("lebanese",)),
    "Liberia": ("LR", ("liberian",)),
    "Libya": ("LY", ("libyan",)),
    "Liechtenstein": ("LI", ()),
    "Lithuania": ("LT", ("lithuanian",)),
    "Luxembourg": ("LU", ("luxembourgish",)),
    "Macao": ("MO", ("macao special administrative region of china", "macau")),
    "Malaysia": ("MY", ("malaysian",)),
    "Mali": ("ML", ("malian",)),
    "Malta": ("MT", ("maltese",)),
    "Mexico": ("MX", ("mexican",)),
    "Moldova": ("MD", ("moldovan", "republic of moldova")),
    "Monaco": ("MC", ("monegasque",)),
    "Mongolia": ("MN", ("mongolian",)),
    "Montenegro": ("ME", ("montenegrin",)),
    "Morocco": ("MA", ("moroccan",)),
    "Mozambique": ("MZ", ("mozambican",)),
    "Myanmar": ("MM", ("burmese", "burma")),
    "Namibia": ("NA", ("namibian",)),
    "Nepal": ("NP", ("nepalese", "nepali")),
    "Netherlands": ("NL", ("dutch", "the netherlands", "holland")),
    "New Zealand": ("NZ", ("new zealander",)),
    "Nicaragua": ("NI", ("nicaraguan",)),
    "Niger": ("NE", ("nigerien",)),
    "Nigeria": ("NG", ("nigerian",)),
    "North Korea": ("KP", ("north korean", "democratic people's republic of korea", "dprk")),
    "North Macedonia": ("MK", ("macedonian", "macedonia")),
    "Northern Mariana Islands": ("MP", ()),
    "Norway": ("NO", ("norwegian",)),
    "Oman": ("OM", ("omani",)),
    "Pakistan": ("PK", ("pakistani",)),
    "Palestine": ("PS", ("palestinian", "palestina", "state of palestine")),
    "Panama": ("PA", ("panamanian",)),
    "Paraguay": ("PY", ("paraguayan",)),
    "Peru": ("PE", ("peruvian",)),
    "Philippines": ("PH", ("filipino", "philippine", "the philippines")),
    "Poland": ("PL", ("polish",)),
    "Portugal": ("PT", ("portuguese",)),
    "Puerto Rico": ("PR", ("puerto rican",)),
    "Qatar": ("QA", ("qatari",)),
    "Romania": ("RO", ("romanian",)),
    "Russia": ("RU", ("russian", "russian federation")),
    "Rwanda": ("RW", ("rwandan",)),
    "Saudi Arabia": ("SA", ("saudi", "saudi arabian")),
    "Senegal": ("SN", ("senegalese",)),
    "Serbia": ("RS", ("serbian",)),
    "Sierra Leone": ("SL", ("sierra leonean",)),
    "Singapore": ("SG", ("singaporean",)),
    "Slovakia": ("SK", ("slovak",)),
    "Slovenia": ("SI", ("slovenian", "slovene")),
    "Somalia": ("SO", ("somali",)),
    "South Africa": ("ZA", ("south african",)),
    "South Korea": ("KR", ("south korean", "korea", "republic of korea")),
    "South Sudan": ("SS", ("south sudanese",)),
    "Spain": ("ES", ("spanish", "espana")),
    "Sri Lanka": ("LK", ("sri lankan",)),
    "Sudan": ("SD", ("sudanese",)),
    "Sweden": ("SE", ("swedish",)),
    "Switzerland": ("CH", ("swiss",)),
    "Syria": ("SY", ("syrian", "syrian arab republic")),
    "Taiwan": ("TW", ("taiwanese",)),
    "Tajikistan": ("TJ", ("tajik",)),
    "Tanzania": ("TZ", ("tanzanian",)),
    "Thailand": ("TH", ("thai",)),
    "Togo": ("TG", ("togolese",)),
    "Trinidad and Tobago": ("TT", ("trinidadian",)),
    "Tunisia": ("TN", ("tunisian",)),
    "Turkey": ("TR", ("turkish", "turkiye")),
    "Turkmenistan": ("TM", ("turkmen",)),
    "Uganda": ("UG", ("ugandan",)),
    "Ukraine": ("UA", ("ukrainian",)),
    "United Arab Emirates": ("AE", ("emirati", "uae")),
    "United Kingdom": (
        "GB",
        ("british", "uk", "great britain", "england", "scotland", "wales", "english", "scottish"),
    ),
    "United States": (
        "US",
        ("american", "usa", "united states of america", "u.s.", "u.s.a."),
    ),
    "Uruguay": ("UY", ("uruguayan",)),
    "Uzbekistan": ("UZ", ("uzbek",)),
    "Venezuela": ("VE", ("venezuelan",)),
    "Vietnam": ("VN", ("vietnamese", "viet nam")),
    "Yemen": ("YE", ("yemeni",)),
    "Zambia": ("ZM", ("zambian",)),
    "Zimbabwe": ("ZW", ("zimbabwean",)),
}

NOT_COUNTRIES = {"international", "europe", "global", "worldwide", "africa", "asia"}


def _key(text: str) -> str:
    text = fold(text).replace("’", "'")
    text = re.sub(r"[^\w\s']", " ", text)
    return " ".join(text.replace("'", "").split())


_EXACT: dict[str, str] = {}
_WORDS: dict[str, str] = {}
for _name, (_code, _others) in _COUNTRIES.items():
    _EXACT[_key(_name)] = _name
    _EXACT[_code.lower()] = _name
    _WORDS[_key(_name)] = _name
    for _other in _others:
        _EXACT[_key(_other)] = _name
        if len(_key(_other)) > 3:
            _WORDS[_key(_other)] = _name
_EXACT["gb"] = "United Kingdom"
_EXACT["uk"] = "United Kingdom"
_SCAN = re.compile(
    r"\b(" + "|".join(sorted((re.escape(w) for w in _WORDS), key=len, reverse=True)) + r")\b"
)


def canonical(value: str) -> str | None:
    """Canonical country for one cell value, a code, or a nationality word."""
    key = _key(value)
    if not key or key in NOT_COUNTRIES:
        return None
    if key in _EXACT:
        return _EXACT[key]
    return value.strip().title() if len(key) > 2 else None


def last_part(value: str) -> str:
    """The last comma-separated part, without bracketed labels such as ``[PEP]``."""
    value = re.sub(r"\[[^\]]*\]", "", value).strip().rstrip(",").strip()
    return value.split(",")[-1].strip() if value else ""


def countries_in_list(cell: str, *, last: bool = False) -> set[str]:
    """Countries from a semicolon-separated cell. ``last`` takes each entry's last part."""
    out: set[str] = set()
    for item in cell.split(";"):
        item = last_part(item) if last else re.sub(r"\[[^\]]*\]", "", item).strip()
        country = canonical(item)
        if country:
            out.add(country)
    return out


def countries_in_text(text: str) -> set[str]:
    """Countries named anywhere in free text, by name or nationality word."""
    return {_WORDS[m] for m in _SCAN.findall(_key(text))}
