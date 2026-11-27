import argparse
import os
import re
import time
import json
from collections import Counter
from typing import List, Optional, Literal, Tuple, get_args
import requests
import psycopg
import yfinance as yf
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

try:
    from ai_usage_logger import record_ai_usage
except ImportError:
    from pipeline.ai_usage_logger import record_ai_usage

DATABASE_URL = os.environ["DATABASE_URL"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
BATCH_LIMIT = int(os.environ.get("PRODUCT_BATCH_LIMIT", "15"))

GENERIC_SEGMENT_RE = re.compile(
    r"\b(business|segment|division|operations|services|activities)\b",
    re.IGNORECASE,
)

GENERIC_ADV_CATEGORY_RE = re.compile(
    r"^\s*(electronic\s+materials?|advanced\s+materials?(\s+portfolio)?|"
    r"specialty\s+materials?|performance\s+chemicals?|engineered\s+materials?|"
    r"industrial\s+materials?|semiconductor\s+materials?|battery\s+materials?)\s*$",
    re.IGNORECASE,
)

INVALID_RAW_MATERIAL_RE = re.compile(
    r"^\s*("
    r"labor|direct\s+labor|manufacturing\s+labor|"
    r"raw\s+materials?|direct\s+materials?|other\s+raw\s+materials?|"
    r"feedstocks?|industrial\s+feedstocks?|chemical\s+feedstocks?|petrochemical\s+feedstocks?|"
    r"specialty\s+chemical\s+intermediates?|chemical\s+intermediates?|intermediates?|"
    r"specialty\s+chemicals?|industrial\s+chemicals?|base\s+chemicals?|"
    r"commodities|metals?|precious\s+metals?|rare\s+earths?|minerals?|polymers?|resins?|plastic\s+resins?|"
    r"energy|electricity|power|utilities|water|freight|logistics|transportation|"
    r"packaging|consumables?|components?|parts?|equipment|tooling|scrap|general|other"
    r")\s*$"
    r"|\b(labor|raw\s+materials?|industrial\s+feedstocks?|specialty\s+chemical\s+intermediates?)\b",
    re.IGNORECASE,
)

CANONICAL_CODE_NAMES = {
    "HG=F": "Copper",
    "NG=F": "Natural Gas",
    "CL=F": "WTI Crude Oil",
    "BZ=F": "Brent Crude Oil",
    "ALI=F": "Aluminum",
    "PL=F": "Platinum",
    "SI=F": "Silver",
    "PNICKUSDM": "Nickel",
    "PCOBAUSDM": "Cobalt",
    "PNGASEUUSDM": "Natural Gas",
    "WPU101": "Iron and Steel",
    "NDPR_CN": "Neodymium-Praseodymium (NdPr) Oxide",
    "LICARB_CN": "Lithium Carbonate",
    "GALLIUM_9999": "Gallium",
    "GERMANIUM_INGOT": "Germanium",
    "POLYSILICON_N": "Polysilicon",
    "SPHERICAL_GRAPHITE": "Spherical Graphite",
    "COBALT_SULFATE": "Cobalt Sulfate",
    "CARBON_FIBER_T700": "Polyacrylonitrile (PAN) Precursor",
}

PRESERVE_ACRONYMS = {
    "wti": "WTI",
    "pan": "PAN",
    "bpa": "BPA",
    "lng": "LNG",
    "ndpr": "NdPr",
    "pvc": "PVC",
    "ptfe": "PTFE",
    "pfa": "PFA",
    "peek": "PEEK",
    "pps": "PPS",
    "mdi": "MDI",
    "tdi": "TDI",
    "lipf6": "LiPF6",
    "sic": "SiC",
    "gan": "GaN",
    "gaas": "GaAs",
    "inp": "InP",
    "ccl": "CCL",
    "abf": "ABF",
    "cmp": "CMP",
    "euv": "EUV",
    "arf": "ArF",
    "oled": "OLED",
    "mlcc": "MLCC",
    "soi": "SOI",
    "hbm": "HBM",
    "ic": "IC",
    "rf": "RF",
    "ev": "EV",
    "pgm": "PGM",
    "pvoh": "PVOH",
    "ncma": "NCMA",
    "nmc": "NMC",
    "lfp": "LFP",
    "ndfeb": "NdFeB",
}

LOWER_CONNECTORS = {"and", "or", "of", "in", "for", "to", "with"}


def _to_canonical_title_case(raw: str) -> str:
    words = []
    for idx, w in enumerate(raw.split(" ")):
        low = w.lower()
        clean_low = low.strip("()")
        if clean_low in PRESERVE_ACRONYMS:
            token = PRESERVE_ACRONYMS[clean_low]
            words.append(f"({token})" if low.startswith("(") and low.endswith(")") else token)
        elif idx > 0 and low in LOWER_CONNECTORS:
            words.append(low)
        elif "-" in w:
            parts = []
            for p in w.split("-"):
                p_low = p.lower()
                if p_low in PRESERVE_ACRONYMS:
                    parts.append(PRESERVE_ACRONYMS[p_low])
                else:
                    parts.append(p.capitalize())
            words.append("-".join(parts))
        else:
            words.append(w.capitalize())
    return " ".join(words)


def normalize_raw_material_name(raw_name: str, matched_code: Optional[str] = None) -> Optional[str]:
    if matched_code and matched_code in CANONICAL_CODE_NAMES:
        return CANONICAL_CODE_NAMES[matched_code]
    cleaned = re.sub(r"\s*\([^)]*\)", " ", (raw_name or "").strip())
    cleaned = re.sub(
        r"\b(futures?|spot|benchmarks?|ind(ex|ices)|prices?|pricing|contracts?|quotes?|feedstocks?|raw\s+materials?)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(european|global|us|u\.s\.|north\s+american|asian|chinese|domestic|imported|refined|raw|crude|primary|virgin|pure|ultra[- ]?pure|high[- ]?purity|electronic[- ]?grade|battery[- ]?grade|n[- ]?type)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—,:;")
    if not cleaned or len(cleaned) < 3 or INVALID_RAW_MATERIAL_RE.search(cleaned):
        return None
    if cleaned.lower() in ("european natural gas", "ttf natural gas", "lng", "liquefied natural gas"):
        return "Natural Gas"
    return _to_canonical_title_case(cleaned)


# ---------------------------------------------------------------------------
# Advanced-material admission rules
#
# A category or product name is admitted only if (1) a curated alias/family
# mapping claims it, or (2) after stripping modifiers, generic heads, process
# words and end-market words, at least one token that could name a physical
# product survives, it is not a descriptive phrase, and it is not an
# unprocessed raw feedstock. Curated mappings always run before heuristics.
# Keep ADV_MATERIAL_ALIASES / COHESIVE_AND_CLASSES in sync with the TS atlas.
# ---------------------------------------------------------------------------

MaterialForm = Literal[
    "chemical_formulation",
    "polymer_or_resin",
    "slurry_or_abrasive",
    "wafer_or_substrate",
    "film_or_laminate",
    "fiber_or_composite",
    "powder_or_oxide",
    "specialty_gas",
    "alloy_or_metal_product",
    "ceramic_or_glass",
    "magnet",
    "electrolyte_or_electrode_material",
    "not_a_material",
]
MATERIAL_FORMS = set(get_args(MaterialForm))

_SPELLING_FIXES = [
    (re.compile(r"\bspeciality\b", re.IGNORECASE), "Specialty"),
    (re.compile(r"\bspecialities\b", re.IGNORECASE), "Specialties"),
]


def _fix_spelling(s: str) -> str:
    for pat, rep in _SPELLING_FIXES:
        s = pat.sub(rep, s)
    return s


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _tokens(s: str) -> List[str]:
    k = _key(s)
    return k.split() if k else []


def _strip_parens(s: str) -> str:
    # Strips closed "(...)" and broken unclosed "(..." tags.
    return re.sub(r"\s*\([^)]*\)?", " ", s)


STOP_TOKENS = {
    "and", "or", "of", "for", "in", "the", "to", "with", "a", "an", "from",
    "used", "serving", "supporting", "enabling",
}

# Adjectives that cannot be a product on their own.
MODIFIER_TOKENS = {
    "stainless", "specialty", "specialties", "advanced", "engineered", "performance",
    "high", "industrial", "electronic", "precision", "structural", "functional",
    "premium", "technical", "specialized", "custom", "proprietary", "next",
    "generation", "novel", "innovative", "critical", "strategic", "synthetic",
    "natural", "ultra", "pure", "purity", "grade",
}

# Generic head nouns that name a category of thing, not a thing.
GENERIC_HEAD_TOKENS = {
    "material", "materials", "chemical", "chemicals", "product", "products",
    "solution", "solutions", "technology", "technologies", "system", "systems",
    "service", "services", "portfolio", "offering", "offerings", "business",
    "segment", "segments", "division", "line", "equipment", "device", "devices",
    "component", "components", "part", "parts", "application", "applications",
    "market", "markets", "industry", "industries", "sector", "sectors",
    "mineral", "minerals", "ore", "ores", "metal", "metals",
}

# Activities, not outputs.
PROCESS_TOKENS = {
    "mining", "refining", "processing", "recycling", "smelting", "extraction",
    "production", "manufacturing", "fabrication", "separation", "distribution",
    "trading", "supply", "sales", "beneficiation",
}

# End markets / verticals, not products.
END_MARKET_TOKENS = {
    "semiconductor", "semiconductors", "electronics", "aerospace", "defense",
    "defence", "automotive", "medical", "healthcare", "energy", "nuclear",
    "mobility", "optics", "battery", "batteries",
}

# Residuals that survive token stripping but are still not a product.
BARE_AMBIGUOUS_PHRASES = {"rare earth", "rare earths", "silicon"}

VAGUE_ADV_RE = re.compile(
    r"^(electronic|electronic\s+materials?|advanced\s+materials?(\s+portfolio)?|"
    r"specialty\s+materials?|performance\s+chemicals?|engineered\s+materials?|"
    r"industrial\s+materials?|semiconductor\s+materials?|battery\s+materials?|"
    r"materials?|chemicals?|polymers?|resins?|plastics?|metals?|alloys?|mining|"
    r"rare\s+earth(\s+mining)?|specialty\s+chemicals?|industrial\s+chemicals?|"
    r"composite\s+materials?|advanced\s+polymers?|electronic\s+chemicals?|"
    r"batteries|battery\s+cells?|devices?|parts?|components?|aerospace|"
    r"aerospace\s+and\s+defense|fuel|fuels|life\s+sciences?|mobility|optics|nuclear)$",
    re.IGNORECASE,
)

# "Materials for X", "Chemicals used in X"
_LEADING_DESCRIPTIVE_RE = re.compile(
    r"^(materials?|chemicals?|products?|solutions?|technolog(y|ies)|components?|"
    r"equipment|systems?|services?|supplies|consumables?)\s+"
    r"(for|used\s+(in|for)|in|serving|supporting|enabling|to)\b",
    re.IGNORECASE,
)
# "... Manufacturing", "... Industry"
_TRAILING_DESCRIPTIVE_RE = re.compile(
    r"\b(manufacturing|fabrication|industry|industries|sectors?|markets?|"
    r"applications?|segments?|business|division|operations)$",
    re.IGNORECASE,
)

# Unprocessed raw feedstocks: ores, concentrates, sand/quartz, base and precious
# metals in elemental form, energy hydrocarbons, bulk commodity chemicals.
# Engineered forms (fused quartz, fumed silica, epoxy resin, alumina powders)
# are deliberately NOT here so legitimate producers are not blocked.
_PURE_FEEDSTOCK_PHRASES = [
    "copper", "aluminum", "aluminium", "nickel", "cobalt", "gold", "silver",
    "platinum", "palladium", "rhodium", "iridium", "ruthenium", "tungsten",
    "molybdenum", "steel", "iron", "iron and steel", "iron ore", "steel scrap",
    "natural gas", "crude oil", "wti crude oil", "brent crude oil",
    "ethane", "ethylene", "propylene", "naphtha", "benzene", "toluene",
    "xylene", "methanol", "ammonia", "chlorine", "sodium hydroxide",
    "caustic soda", "sulfuric acid", "phenol", "acetone",
    "quartz", "quartz sand", "silica sand", "high purity quartz sand",
    "bauxite", "ilmenite", "rutile", "magnesite", "colemanite",
    "spodumene", "spodumene concentrate", "lithium",
    "rare earth concentrate", "bastnaesite", "monazite",
    "natural flake graphite", "petroleum needle coke",
    "calcined petroleum coke", "coal tar pitch",
    "metallurgical silicon", "silicon metal", "titanium sponge",
]


def _residual_tokens(phrase: str) -> List[str]:
    return [
        t for t in _tokens(phrase)
        if t not in STOP_TOKENS
        and t not in MODIFIER_TOKENS
        and t not in GENERIC_HEAD_TOKENS
        and t not in PROCESS_TOKENS
        and t not in END_MARKET_TOKENS
    ]


PURE_FEEDSTOCK_KEYS = {_key(p) for p in _PURE_FEEDSTOCK_PHRASES} | {
    " ".join(_residual_tokens(p)) for p in _PURE_FEEDSTOCK_PHRASES
}
PURE_FEEDSTOCK_KEYS.discard("")

COHESIVE_AND_CLASSES = {
    "cmp slurries and pads",
    "specialty steel and alloys",
    "stainless and high performance alloys",
    "industrial and specialty gases",
    "polyurethanes and isocyanates",
    "polyolefins and engineered resins",
    "rare earth oxides and metals",
    "lithium hydroxide and carbonate",
    "fluoropolymers and fluorochemicals",
    "compound semiconductors and photonics",
    "photoresists and semiconductor chemicals",
    "biocompatible polymers and biomaterials",
    "optical fiber and display glass",
    "fused quartz and fused silica",
}

ADV_MATERIAL_ALIASES = {
    "battery electrolyte": "Battery Electrolytes",
    "battery electrolytes": "Battery Electrolytes",
    "fluorochem": "Fluoropolymers and Fluorochemicals",
    "fluorochemicals": "Fluoropolymers and Fluorochemicals",
    "fluoropolymers": "Fluoropolymers and Fluorochemicals",
    "fluoropolymers and titanium technologies": "Fluoropolymers and Fluorochemicals",
    "carbon fiber": "Carbon Fiber Composites",
    "carbon fibers": "Carbon Fiber Composites",
    "carbon fiber composites": "Carbon Fiber Composites",
    "aerospace carbon fiber composites": "Carbon Fiber Composites",
    "advanced films": "Advanced Polymer Films",
    "polymer films": "Advanced Polymer Films",
    "tire cord": "Industrial Tire Cord Fabrics",
    "tire cord fabrics": "Industrial Tire Cord Fabrics",
    "lithium refining": "Lithium Hydroxide and Carbonate",
    "lithium chemicals": "Lithium Hydroxide and Carbonate",
    "lithium carbonate": "Lithium Hydroxide and Carbonate",
    "lithium hydroxide": "Lithium Hydroxide and Carbonate",
    "gases": "Industrial and Specialty Gases",
    "industrial gases": "Industrial and Specialty Gases",
    "specialty gases": "Industrial and Specialty Gases",
    "rare earth mining and magnets": "Rare Earth Permanent Magnets",
    "rare earth magnets": "Rare Earth Permanent Magnets",
    "ndfeb permanent magnets": "Rare Earth Permanent Magnets",
    "rare earth permanent magnets": "Rare Earth Permanent Magnets",
    "rare earth oxides": "Rare Earth Oxides and Metals",
    "rare earth metals": "Rare Earth Oxides and Metals",
    "rare earth oxides and metals": "Rare Earth Oxides and Metals",
    "polyurethane": "Polyurethanes and Isocyanates",
    "polyurethanes": "Polyurethanes and Isocyanates",
    "polyurethanes and isocyanates": "Polyurethanes and Isocyanates",
    "specialty steel": "Specialty Steel and Alloys",
    "specialty steel and alloys": "Specialty Steel and Alloys",
    "stainless and specialty steel": "Specialty Steel and Alloys",
    "stainless and high performance alloys": "Stainless and High-Performance Alloys",
    "photoresists": "Photoresists and Semiconductor Chemicals",
    "photoresists and semiconductor chemicals": "Photoresists and Semiconductor Chemicals",
    "semiconductor packaging": "Semiconductor Packaging Materials",
    "semiconductor packaging and chemicals": "Semiconductor Packaging Materials",
    "compound semiconductors": "Compound Semiconductors and Photonics",
    "compound semiconductors and photonics": "Compound Semiconductors and Photonics",
    "natural graphite anode materials": "Natural Graphite Anode Materials",
    "synthetic graphite anodes": "Synthetic Graphite Anode Materials",
    "synthetic graphite anode materials": "Synthetic Graphite Anode Materials",
    "copper clad laminates": "Copper Clad Laminates (CCL)",
    "high speed copper clad laminates": "High-Speed Copper Clad Laminates (CCL)",
    "high frequency copper clad laminates": "High-Speed Copper Clad Laminates (CCL)",
    "silicon wafer": "Silicon Wafers",
    "silicon wafers": "Silicon Wafers",
    "silicon wafers and pvc": "Silicon Wafers",
    "engineered soi wafers": "Engineered SOI Wafers",
    "silicon carbide wafer": "Silicon Carbide (SiC) Wafers",
    "silicon carbide wafers": "Silicon Carbide (SiC) Wafers",
    "sic wafers": "Silicon Carbide (SiC) Wafers",
    "cmp slurries": "CMP Slurries and Pads",
    "cmp slurries and pads": "CMP Slurries and Pads",
    "cmp slurries and wet chemicals": "CMP Slurries and Pads",
    "cmp slurries and abrasives": "CMP Slurries and Pads",
    "high nickel cathodes": "High-Nickel Cathode Materials",
    "high nickel cathode materials": "High-Nickel Cathode Materials",
    "cathodes and synthetic anodes": "High-Nickel Cathode Materials",
    "cathode active materials": "High-Nickel Cathode Materials",
    "olefins and polyolefins": "Polyolefins and Engineered Resins",
    "packaging and specialty plastics": "Polyolefins and Engineered Resins",
    "medical devices and biomaterials": "Biocompatible Polymers and Biomaterials",
    "biomaterials": "Biocompatible Polymers and Biomaterials",
    "ceram": "Ceramics",
    "ceramic": "Ceramics",
    "ceramics": "Ceramics",
    "hydrogen cat": "Hydrogen Catalysts",
    "hydrogen catalysts": "Hydrogen Catalysts",
    "isotropic graphite": "Isotropic Graphite",
    # Engineered quartz is a legitimate advanced product; bare quartz is a feedstock.
    "fused quartz": "Fused Quartz and Fused Silica",
    "fused silica": "Fused Quartz and Fused Silica",
    "fused quartz and fused silica": "Fused Quartz and Fused Silica",
    "quartz crucibles": "Fused Quartz and Fused Silica",
}

# Families of phrasings that exact-key aliases miss.
FAMILY_ALIASES = [
    (
        re.compile(r"\bstainless\b.*\b(steels?|alloys?)\b|\bspecialty\s+steels?\b|\bspecialty\s+alloys?\b"),
        "Specialty Steel and Alloys",
    ),
    (
        re.compile(r"\brare earth\b.*\b(refin\w*|process\w*|separat\w*)\b"),
        "Rare Earth Oxides and Metals",
    ),
]

DANGLING_CONJUNCTION_RE = re.compile(r"^(and|or|&|,|-)\b|\b(and|or|&|,|-)$", re.IGNORECASE)
_ACRONYM_RE = re.compile(r"\((CCL|SiC|SOI|ABF|HBM|OLED|EV|PTFE)\)", re.IGNORECASE)

# (raw string, reason) -> count, reported at the end of a run.
REJECT_STATS: Counter = Counter()


def _match_family_alias(lookup_key: str) -> Optional[str]:
    for pat, canonical in FAMILY_ALIASES:
        if pat.search(lookup_key):
            return canonical
    return None


def _is_informative_product(phrase: str) -> bool:
    residual = _residual_tokens(phrase)
    if not residual:
        return False
    return " ".join(residual) not in BARE_AMBIGUOUS_PHRASES


def adv_rejection_reason(cleaned: str, lookup_key: str, is_name: bool = False) -> Optional[str]:
    """Returns a rejection reason, or None if the phrase can name an advanced material.

    Product names may legitimately read as descriptions ("Pads for CMP
    Manufacturing"), so trailing-descriptive rejection applies to categories only.
    """
    if VAGUE_ADV_RE.match(cleaned.strip()):
        return "vague-umbrella"
    if _LEADING_DESCRIPTIVE_RE.search(cleaned):
        return "descriptive-phrase"
    if not is_name and _TRAILING_DESCRIPTIVE_RE.search(cleaned):
        return "descriptive-phrase"
    residual = _residual_tokens(cleaned)
    if not residual:
        return "no-product-noun"
    residual_key = " ".join(residual)
    if residual_key in BARE_AMBIGUOUS_PHRASES:
        return "ambiguous-bare-term"
    if lookup_key in PURE_FEEDSTOCK_KEYS or residual_key in PURE_FEEDSTOCK_KEYS:
        return "raw-feedstock"
    return None


def _expand_abbrevs(s: str) -> str:
    subs = [
        (r"\bict\b", "Information and Communications Technology"),
        (r"\bsemicon\b", "Semiconductor"),
        (r"\bpkg\b", "Packaging"),
        (r"\bind\b", "Industrial"),
        (r"\bchem\b", "Chemicals"),
        (r"\bfluorochem\b", "Fluorochemicals"),
        (r"\bceram\b", "Ceramics"),
        (r"\bcat\b", "Catalysts"),
        (r"\bmat\b", "Materials"),
        (r"\bhigh[- ]perf\b", "High-Performance"),
        (r"\btech\b", "Technologies"),
        (r"\bmfg\b", "Manufacturing"),
    ]
    for pat, rep in subs:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    return s


def _clean_segment(segment: str) -> str:
    s = _strip_parens(segment)
    s = re.sub(r"^[^(\s]*\)", " ", s)  # orphan "Nuc)" style tails
    s = s.replace(".", "")
    s = _expand_abbrevs(s)
    s = re.sub(r"\bmining\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" -–—,:;()")


def _reject(trace: Optional[list], segment: str, reason: str) -> None:
    if trace is not None:
        trace.append((segment, reason))
    return None


def _normalize_single_adv_segment(
    segment: str,
    acronym: Optional[str] = None,
    trace: Optional[list] = None,
    strict: bool = False,
) -> Optional[str]:
    cleaned = _clean_segment(segment)
    if not cleaned or len(cleaned) < 3 or DANGLING_CONJUNCTION_RE.search(cleaned):
        return _reject(trace, segment, "empty-or-fragment")

    key = _key(cleaned)

    # 1. Curated mappings win over every heuristic.
    if key.startswith("polyurethane"):
        return "Polyurethanes and Isocyanates"
    if key in ADV_MATERIAL_ALIASES:
        return ADV_MATERIAL_ALIASES[key]
    family = _match_family_alias(key)
    if family:
        return family

    # 2. Hard rejects.
    reason = adv_rejection_reason(cleaned, key)
    if reason:
        return _reject(trace, segment, reason)

    # 3. Strict mode never mints a class from free text.
    if strict:
        return _reject(trace, segment, "strict-unmapped")

    # 4. Free-text fallback.
    formatted = _to_canonical_title_case(cleaned)
    if acronym and not re.search(rf"\b{re.escape(acronym)}\b", formatted, re.IGNORECASE):
        formatted = f"{formatted} ({acronym})"
    return formatted


def resolve_adv_classes(
    raw_name: str,
    trace: Optional[list] = None,
    strict: bool = False,
) -> List[str]:
    """Mirror of the atlas resolver: returns zero or more canonical classes."""
    raw = _fix_spelling((raw_name or "").strip())
    if not raw:
        return []

    m = _ACRONYM_RE.search(raw)
    acronym = PRESERVE_ACRONYMS.get(m.group(1).lower(), m.group(1).upper()) if m else None

    normalized = raw.replace(".", "")
    normalized = re.sub(r"\s*&\s*", " and ", normalized)
    normalized = re.sub(r"\s*/\s*", " and ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    full_key = _key(_strip_parens(normalized))

    if full_key in ADV_MATERIAL_ALIASES:
        return [ADV_MATERIAL_ALIASES[full_key]]
    family = _match_family_alias(full_key)
    if family:
        return [family]

    if full_key in COHESIVE_AND_CLASSES:
        single = _normalize_single_adv_segment(normalized, acronym, trace, strict=False)
        return [single] if single else []

    if re.search(r"\band\b", normalized, re.IGNORECASE):
        parts = re.split(r"\s+and\s+", normalized, flags=re.IGNORECASE)
        # "Stainless and X": a modifier-only half means "and" joins adjectives.
        has_fragment = any(not _is_informative_product(_strip_parens(p)) for p in parts)
        if not has_fragment:
            results: List[str] = []
            seen = set()
            for part in parts:
                resolved = _normalize_single_adv_segment(part, acronym, trace, strict)
                if resolved and resolved.lower() not in seen:
                    seen.add(resolved.lower())
                    results.append(resolved)
            return results
        # otherwise evaluate the whole string as one phrase

    single = _normalize_single_adv_segment(normalized, acronym, trace, strict)
    return [single] if single else []


def resolve_adv_category(
    raw_cat: str,
    fallback_name: str = "",
    trace: Optional[list] = None,
) -> Tuple[Optional[str], bool]:
    """Returns (canonical single class or None, used_name_fallback).

    The category column holds exactly one class. If the model glued several
    product lines together, the first resolved class is kept.
    """
    classes = resolve_adv_classes(raw_cat or "", trace)
    if classes:
        if len(classes) > 1 and trace is not None:
            trace.append((raw_cat, "multi-class-truncated"))
        return classes[0], False
    classes = resolve_adv_classes(fallback_name or "", trace)
    if classes:
        return classes[0], True
    return None, False


def product_name_rejection_reason(name: str) -> Optional[str]:
    cleaned = _strip_parens(_fix_spelling((name or "").strip())).strip()
    if not cleaned:
        return "empty-name"
    key = _key(cleaned)
    if key in ADV_MATERIAL_ALIASES:
        return None
    return adv_rejection_reason(cleaned, key, is_name=True)


NON_ADV_MAT_RE = re.compile(
    r"\b("
    r"pharmaceuticals?|oncology|biologics?|vaccines?|therapeutics?|clinical\s+trials?|"
    r"prescription\s+drugs?|fertility\s+drugs?|monoclonal\s+antibod(y|ies)|cdmo|cro|"
    r"frozen\s+foods?|seasonings?|beverages?|snacks?|pet\s+food|animal\s+feed|"
    r"post-?it|scotch\s+tape|consumer\s+retail|apparel|footwear|cosmetics?|"
    r"enterprise\s+software|cloud\s+saas|avionics|building\s+automation|"
    r"insurance|banking|residential\s+housing|turnkey\s+machinery|capital\s+equipment|metrology|"
    r"recycling|"
    r"battery\s+cells?|prismatic\s+cells?|cylindrical\s+cells?|pouch\s+cells?|"
    r"battery\s+modules?|battery\s+packs?|ess\s+(racks?|systems?|batteries)|"
    r"energy\s+storage\s+systems?|all-?solid-?state\s+batter(y|ies)|"
    r"optical\s+transceivers?|wiring\s+harness(es)?|connectors?|cables?|"
    r"sensors?|actuators?|housings?|display\s+panels?|sub-?assembl(y|ies)|finished\s+components?"
    r")\b",
    re.IGNORECASE,
)

NON_MAT_CUSTOMER_RE = re.compile(
    r"\b(walmart|target|cvs|walgreens|kroger|costco|pfizer|novartis|roche|sanofi|astrazeneca|amgen|nestle|unilever)\b",
    re.IGNORECASE,
)


def _build_gemini_client():
    if not GEMINI_API_KEY:
        return None
    try:
        return genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=45000),
        )
    except TypeError:
        return genai.Client(api_key=GEMINI_API_KEY)


client = _build_gemini_client()

MAX_GEMINI_CALLS = 24
gemini_calls_used = 0
groq_calls_used = 0

GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]
exhausted_gemini_models = set()

GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-safeguard-20b",
]
exhausted_groq_models = set()


def connect_db():
    return psycopg.connect(
        DATABASE_URL,
        autocommit=True,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def ensure_conn(conn):
    if conn is not None and not conn.closed and not conn.broken:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    print("Reconnecting to Neon PostgreSQL...", flush=True)
    return connect_db()


def fetch_live_taxonomy(conn) -> tuple[str, str]:
    """Live canonical Advanced Material categories and Raw Material names from Neon.

    Two properties matter here because this text is fed back to the model as
    "reuse this EXACT spelling":
      1. Only categories that pass the admission gate are exposed. Otherwise a
         bad legacy category ("Stainless", "Rare Earth Refining") is
         reinforced on every subsequent extraction.
      2. Ranking is by usage frequency, not alphabetical. Alphabetical
         truncation at 100 hid every category past roughly "M", which caused the
         model to mint duplicates of them.
    """
    conn = ensure_conn(conn)
    cat_counts: Counter = Counter()
    raw_counts: Counter = Counter()
    for name in CANONICAL_CODE_NAMES.values():
        raw_counts[name] += 0

    with conn.cursor() as cur:
        cur.execute("""
            SELECT category, COUNT(*)
            FROM company_products
            WHERE status = 'active' AND category IS NOT NULL
            GROUP BY category;
        """)
        for cat, n in cur.fetchall():
            clean_cat, _ = resolve_adv_category(cat)
            if clean_cat:
                cat_counts[clean_cat] += n

        cur.execute("""
            SELECT elem->>'name', COUNT(*)
            FROM company_products, jsonb_array_elements(raw_materials) AS elem
            WHERE status = 'active' AND elem->>'name' IS NOT NULL
            GROUP BY 1;
        """)
        for rname, n in cur.fetchall():
            norm = normalize_raw_material_name(rname)
            if norm:
                raw_counts[norm] += n

        try:
            cur.execute("""
                SELECT commodity_name, COUNT(*)
                FROM equity_commodity_drivers
                WHERE exposure_role = 'Cost Input' AND commodity_name IS NOT NULL
                GROUP BY 1;
            """)
            for cname, n in cur.fetchall():
                norm = normalize_raw_material_name(cname)
                if norm:
                    raw_counts[norm] += n
        except Exception:
            pass

    cats = [c for c, _ in cat_counts.most_common(100)]
    raws = [r for r, _ in raw_counts.most_common(120)]
    return ", ".join(cats), ", ".join(raws)


class RawMaterialSpec(BaseModel):
    name: str = Field(
        description="Specific physical chemical, metal, mineral, gas, or hydrocarbon feedstock (e.g., 'Copper', 'Natural Gas', 'Lithium Carbonate', 'Epoxy Resin', 'Quartz'). NEVER 'Labor', 'Utilities', 'Raw Materials', 'Industrial Feedstock', 'Specialty Chemical Intermediates', or 'Spot'/'Futures'/'European' prefixes."
    )
    commodity_ref: Optional[str] = Field(
        default=None,
        description="Exact input_code from the provided registry list ONLY if an exact match exists; otherwise null. NEVER invent codes.",
    )
    role: str = Field(description="Functional role in the product (e.g., 'abrasive', 'precursor metal', 'resin base').")
    basis: Literal["disclosed", "inferred"]


class AdvancedSpec(BaseModel):
    material_form: MaterialForm = Field(
        description="Closest physical form of the product. Use 'not_a_material' if the item is a segment, component, device, service, or process rather than a material."
    )
    performance_spec: str = Field(description="Measurable performance parameters (purity, defectivity, thermal stability, dielectric loss, tensile modulus).")
    qualification_months: Optional[int] = Field(default=None, description="Explicitly disclosed customer qualification timeline in months, else null.")
    switching_barrier: str = Field(description="Why switching requires customer process requalification.")


class DownstreamSpec(BaseModel):
    process_step: str = Field(description="Customer manufacturing step where this material is consumed.")
    end_product: str = Field(description="Intermediate component or device built by the customer.")
    end_demand: str = Field(description="Secular end-market demand driver.")
    demand_proxy_ref: Optional[str] = Field(
        default=None,
        description="Exact input_code from the provided demand proxy registry list if applicable, else null.",
    )
    customers: Optional[List[str]] = Field(
        default=None,
        description="Explicitly disclosed customers/OEMs for this advanced material if stated in source text, else null.",
    )


class RevenueSpec(BaseModel):
    basis: str = Field(description="'product' if disclosed at product level, else 'business_unit' or 'company'.")
    share_pct: Optional[float] = Field(default=None, description="Disclosed % of company revenue if explicitly stated, else null.")


class SourceSpec(BaseModel):
    type: Literal["annual_report", "filing", "product_page", "inferred"]
    ref: Optional[str] = Field(default=None, description="Filing form/date or document reference, else null.")


class ProductItem(BaseModel):
    id: str = Field(description="Lowercase slug prefixed with ticker, e.g. 'entg-cmp-slurries'.")
    name: str = Field(description="Specific engineered material product name. Never an umbrella division ('Electronic Materials'), a process ('Rare Earth Refining'), an end-market description ('Materials for Advanced Semiconductor Manufacturing'), a bare adjective ('Stainless', 'Specialty'), or an unprocessed raw feedstock ('Quartz', 'Copper').")
    category: str = Field(description="ONE canonical physical material class naming what the company produces (noun phrase, e.g. 'CMP Slurries and Pads', 'Silicon Carbide (SiC) Wafers'). Reuse an existing category from the live taxonomy when applicable. Never a process, end market, sub-sector label, bare adjective, or raw feedstock.")
    business_unit: str
    status: Literal["active", "divested"]
    as_of: str = Field(description="Reporting period, e.g. 'FY2025' or 'Q3 2026'.")
    advanced: AdvancedSpec
    raw_materials: List[RawMaterialSpec]
    downstream: DownstreamSpec
    pricing_structure: Optional[str] = Field(default=None, description="Override only if product contract type differs from company default, else null.")
    revenue: RevenueSpec
    peers: List[str] = Field(default_factory=list, description="Peer tickers from the provided universe list.")
    src: SourceSpec
    conf: Literal["high", "med", "low"]


class ExcludedItem(BaseModel):
    name: str
    reason: str


class CompanyProductsBatch(BaseModel):
    note: Optional[str] = None
    products: List[ProductItem]
    excluded: List[ExcludedItem] = Field(default_factory=list)


def _call_ai(prompt: str, schema) -> Optional[dict]:
    global gemini_calls_used, groq_calls_used

    if client and gemini_calls_used < MAX_GEMINI_CALLS:
        for g_model in GEMINI_MODELS:
            if g_model in exhausted_gemini_models:
                continue
            for attempt in range(2):
                try:
                    gemini_calls_used += 1
                    resp = client.models.generate_content(
                        model=g_model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=schema,
                            temperature=0.1,
                        ),
                    )
                    tok_total = getattr(getattr(resp, "usage_metadata", None), "total_token_count", 0) or 0
                    record_ai_usage(
                        DATABASE_URL,
                        g_model,
                        "Google Gemini",
                        calls_delta=1,
                        tokens_delta=tok_total,
                    )
                    return json.loads(resp.text)
                except Exception as e:
                    err = str(e)
                    if "429" in err or "RESOURCE_EXHAUSTED" in err or "404" in err or "NOT_FOUND" in err:
                        exhausted_gemini_models.add(g_model)
                        if "429" in err or "RESOURCE_EXHAUSTED" in err:
                            record_ai_usage(
                                DATABASE_URL,
                                g_model,
                                "Google Gemini",
                                calls_delta=0,
                                quota_exhausted=True,
                            )
                        print(f"Gemini {g_model} quota/unavailable — trying next model.", flush=True)
                        break
                    if any(k in err for k in ("503", "UNAVAILABLE", "disconnected", "timed out")) and attempt == 0:
                        time.sleep(3)
                        continue
                    exhausted_gemini_models.add(g_model)
                    print(f"Gemini {g_model} error ({e}) — trying next model.", flush=True)
                    break

    if not GROQ_API_KEY:
        return None

    schema_json = json.dumps(schema.model_json_schema())
    trimmed_prompt = prompt[:16000]

    for model_id in GROQ_MODELS:
        if model_id in exhausted_groq_models:
            continue

        for attempt in range(2):
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model_id,
                        "response_format": {"type": "json_object"},
                        "temperature": 0.1,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are an institutional advanced materials analyst. "
                                    "Return ONLY a valid JSON object matching this exact schema:\n"
                                    f"{schema_json}"
                                ),
                            },
                            {"role": "user", "content": trimmed_prompt},
                        ],
                    },
                    timeout=45,
                )
            except requests.RequestException as e:
                print(f"Groq network error on {model_id}: {e}", flush=True)
                break

            resp_headers = dict(resp.headers)

            if resp.status_code == 429:
                err_lower = resp.text.lower()
                if "tokens per day" in err_lower or "requests per day" in err_lower:
                    print(f"Groq daily limit reached for {model_id} — switching model.", flush=True)
                    exhausted_groq_models.add(model_id)
                    record_ai_usage(
                        DATABASE_URL,
                        model_id,
                        "Groq",
                        calls_delta=0,
                        headers=resp_headers,
                        quota_exhausted=True,
                    )
                    break
                if attempt == 0:
                    print(f"Groq TPM limit on {model_id} — waiting 15s...", flush=True)
                    record_ai_usage(
                        DATABASE_URL,
                        model_id,
                        "Groq",
                        calls_delta=0,
                        headers=resp_headers,
                    )
                    time.sleep(15)
                    continue
                break

            if resp.status_code in (400, 404, 413):
                print(f"Groq {resp.status_code} on {model_id} — switching model.", flush=True)
                if resp.status_code == 404:
                    exhausted_groq_models.add(model_id)
                break

            resp.raise_for_status()
            data = resp.json()
            groq_calls_used += 1
            tok_total = data.get("usage", {}).get("total_tokens", 0) or 0
            record_ai_usage(
                DATABASE_URL,
                model_id,
                "Groq",
                calls_delta=1,
                tokens_delta=tok_total,
                headers=resp_headers,
            )
            time.sleep(2)
            return json.loads(data["choices"][0]["message"]["content"])

    return None


def get_registry_and_universe(conn):
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (input_code) input_code, input_name, category
            FROM raw_inputs
            ORDER BY input_code, observation_date DESC;
        """)
        registry = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        cur.execute("SELECT ticker, company_name, sub_sector FROM equities_universe;")
        universe = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    return conn, registry, universe


def _normalize_product_slug(raw_id: str, ticker: str, prod_name: str) -> str:
    prefix = re.sub(r"[^a-z0-9]+", "-", ticker.lower()).strip("-")
    base = re.sub(r"[^a-z0-9]+", "-", (raw_id or prod_name).lower()).strip("-")
    if not base:
        base = re.sub(r"[^a-z0-9]+", "-", prod_name.lower()).strip("-") or "product"
    if not base.startswith(f"{prefix}-"):
        base = f"{prefix}-{base}"
    return base[:80]


def _reasons_text(trace: list) -> str:
    return ", ".join(sorted({r for _, r in trace})) or "unresolved"


def run_product_extraction(conn):
    conn, registry, universe = get_registry_and_universe(conn)
    registry_guide = "\n".join([f"- {code}: {name} ({cat})" for code, (name, cat) in sorted(registry.items())])
    universe_tickers_str = ", ".join(sorted(universe.keys()))

    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.ticker, e.company_name, e.sub_sector, e.adv_mat_rev_pct, e.market_cap_usd
            FROM equities_universe e
            WHERE NOT EXISTS (SELECT 1 FROM company_products p WHERE p.ticker = e.ticker)
              AND NOT EXISTS (SELECT 1 FROM company_product_profiles pr WHERE pr.ticker = e.ticker)
            ORDER BY e.market_cap_usd DESC NULLS LAST, e.ticker ASC
            LIMIT %s;
        """, (BATCH_LIMIT,))
        targets = cur.fetchall()

    print(f"Starting product extraction for {len(targets)} companies (BATCH_LIMIT={BATCH_LIMIT})...", flush=True)

    for idx, (ticker, company_name, sub_sector, adv_mat_rev_pct, _) in enumerate(targets, start=1):
        print(f"[{idx}/{len(targets)}] Extracting {ticker} ({company_name})...", flush=True)
        conn = ensure_conn(conn)
        existing_categories, existing_raw_materials = fetch_live_taxonomy(conn)

        with conn.cursor() as cur:
            cur.execute("""
                SELECT filing_type, filing_date::text, headline, executive_summary, pricing_and_margins, capex_and_backlog
                FROM company_filings_ai
                WHERE ticker = %s
                ORDER BY
                    CASE
                        WHEN filing_type IN ('10-K', '20-F', '40-F', 'Annual Report') THEN 1
                        WHEN filing_type IN ('10-Q', '6-K', 'Quarterly Report', 'Half-Year Report') THEN 2
                        ELSE 3
                    END,
                    filing_date DESC
                LIMIT 2;
            """, (ticker,))
            filings = cur.fetchall()

        context_lines = [
            f"[{f[0]} {f[1]}] {f[2]} | Summary: {f[3]} | Margins: {f[4] or 'N/A'} | Capex: {f[5] or 'N/A'}"
            for f in filings
        ]
        if not context_lines:
            try:
                biz = (yf.Ticker(ticker).info or {}).get("longBusinessSummary", "")[:3000]
                context_lines.append(f"Business Summary: {biz}")
            except Exception as e:
                print(f"  Warning fetching Yahoo summary for {ticker}: {e}", flush=True)

        src_type = "filing" if filings and filings[0][0] in ("10-K", "10-Q", "20-F", "40-F", "6-K") else "inferred"
        src_ref = f"{filings[0][0]} ({filings[0][1]})" if filings and src_type == "filing" else None
        rev_share_note = f"{adv_mat_rev_pct}% of company revenue" if adv_mat_rev_pct is not None else "specialist segment"

        prompt = f"""
        Decompose {company_name} ({ticker}) | Assigned Advanced-Materials Sub-sector: {sub_sector} ({rev_share_note}) into specific engineered advanced-materials products.

        CONGLOMERATE & MATERIALS-VS-COMPONENTS BOUNDARY (CRITICAL):
        - Extract ONLY true engineered materials, formulated chemicals, resins, epoxy molding compounds (EMC), CMP slurries, spin-on dielectrics (SOD), OLED host/dopant materials, photoresists, specialty gases, wafers, films, fibers, powders, precursors, or specialty alloys belonging to "{sub_sector}".
        - STRICTLY EXCLUDE assembled components, discrete devices, and finished battery cells/modules/packs (e.g., prismatic/cylindrical/pouch EV battery cells, ESS racks, optical transceivers, connectors, sensors, housings, display panels, machinery/equipment) as well as pharma, food, consumer goods, and software. Put all excluded components/divisions in excluded[] instead.

        CATEGORY AND NAME ADMISSION RULES (ENFORCED IN CODE — NON-CONFORMING ITEMS ARE DISCARDED):
        - `category` names the PHYSICAL MATERIAL CLASS the company produces, as a noun phrase for what is made. Good: 'CMP Slurries and Pads', 'Silicon Carbide (SiC) Wafers', 'Fluoropolymers and Fluorochemicals', 'Rare Earth Permanent Magnets', 'Specialty Steel and Alloys', 'Fused Quartz and Fused Silica'.
        - The assigned sub-sector "{sub_sector}" is a company-level classification label. NEVER copy it into `category` or `name` unless it is itself a specific physical material class.
        - `category` and `name` must NOT be any of the following:
          (a) a process or activity (mining, refining, processing, recycling, smelting, manufacturing). 'Rare Earth Refining' is wrong; the product is 'Rare Earth Oxides and Metals'.
          (b) an end market or application description. 'Materials for Advanced Semiconductor Manufacturing' is wrong; name the actual material (e.g., 'CMP Slurries and Pads').
          (c) a bare adjective or fragment: 'Stainless', 'Specialty', 'Speciality', 'Advanced', 'Engineered', 'Performance'. Name the noun ('Stainless Steel Flat Products' under 'Specialty Steel and Alloys').
          (d) an unprocessed raw feedstock: ores, concentrates, sand, quartz, elemental base or precious metals, bulk commodity chemicals. Those belong in raw_materials[]. Engineered forms are valid products (e.g., 'Fused Quartz and Fused Silica', 'Specialty Steel and Alloys').
        - Use the spelling 'Specialty'.
        - For each product's `category`, FIRST check whether it fits one of the existing canonical categories below and reuse that EXACT spelling:
          [{existing_categories}]
        - ONLY if the product is a genuinely distinct engineered material class not covered above, mint a new specific Title Case class name that satisfies (a)-(d).
        - Each `category` names ONE class. Never glue two separate product lines together with 'and' unless it is a standard single class like 'CMP Slurries and Pads' or 'Specialty Steel and Alloys'.
        - Set advanced.material_form to the closest physical form. If an item cannot be given a material form (it is a segment, component, device, service, or process), set 'not_a_material' or put it in excluded[].
        - Never use vague umbrella names like 'Electronic Materials', 'Advanced Materials', 'Specialty Materials', or 'Performance Chemicals' as a product name or category.

        RAW MATERIALS RESOLUTION (OPEN-ENDED CANONICAL):
        - List ONLY specific physical chemicals, metals, minerals, gases, or hydrocarbons (never 'Labor', 'Utilities', 'Raw Materials', 'Industrial Feedstock', 'Specialty Chemical Intermediates', or 'Spot'/'Futures'/'European' prefixes). Quartz, ores and concentrates are valid here.
        - Reuse an EXACT existing canonical raw material name when applicable:
          [{existing_raw_materials}]
        - Otherwise mint a new specific Title Case physical feedstock name.

        STRICT INCLUSION GATE (ALL 4 MUST HOLD, OTHERWISE PUT IN excluded[]):
        1. Material-form product (chemical formulation, resin, slurry, wafer, film, fiber, powder, or alloy—NOT an assembled component, cell, module, or device).
        2. Performance-specified and qualified inside a customer's manufacturing process.
        3. Switching requires customer process requalification.
        4. Never a generic segment label (e.g., "electronic materials division", "battery business", "recycling operations").

        RULES:
        - Never invent facts, customer names, qualification months, or product revenue shares. Unknown = null.
        - commodity_ref and demand_proxy_ref MUST be an exact input_code from this registry list, or null if not listed:
        {registry_guide}
        - peers MUST only contain valid tickers from: {universe_tickers_str}
        - Set src={{type: "{src_type}", ref: {json.dumps(src_ref)}}}.

        SOURCE CONTEXT:
        {chr(10).join(context_lines)}
        """

        parsed = _call_ai(prompt, CompanyProductsBatch)
        if not parsed:
            print("All AI providers exhausted — stopping cleanly.", flush=True)
            break

        excluded_items = list(parsed.get("excluded") or [])
        saved_products = 0
        seen_product_names = set()

        conn = ensure_conn(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO company_product_profiles (ticker, note)
                VALUES (%s, %s)
                ON CONFLICT (ticker) DO UPDATE SET note = EXCLUDED.note, updated_at = NOW();
                """,
                (ticker, parsed.get("note")),
            )

            for prod in parsed.get("products", []):
                prod_name = (prod.get("name") or "").strip()
                if not prod_name:
                    continue

                ds_raw = prod.get("downstream") or {}
                check_blob = " ".join([
                    prod_name,
                    prod.get("category") or "",
                    prod.get("business_unit") or "",
                    ds_raw.get("end_product") or "",
                    ds_raw.get("end_demand") or "",
                ])

                if (
                    GENERIC_SEGMENT_RE.search(prod_name)
                    or GENERIC_ADV_CATEGORY_RE.match(prod_name)
                    or NON_ADV_MAT_RE.search(check_blob)
                ):
                    excluded_items.append({
                        "name": prod_name,
                        "reason": "Excluded non-advanced-materials division, finished good, or generic segment.",
                    })
                    continue

                # --- admission gate: product name ---
                name_reason = product_name_rejection_reason(prod_name)
                if name_reason:
                    REJECT_STATS[(prod_name, f"name:{name_reason}")] += 1
                    excluded_items.append({
                        "name": prod_name,
                        "reason": f"Rejected product name ({name_reason}): not a specific engineered material.",
                    })
                    continue

                # --- admission gate: material form ---
                adv = dict(prod.get("advanced") or {
                    "performance_spec": "customer-qualified specification",
                    "qualification_months": None,
                    "switching_barrier": "process requalification",
                })
                material_form = adv.get("material_form")
                if material_form == "not_a_material":
                    REJECT_STATS[(prod_name, "form:not_a_material")] += 1
                    excluded_items.append({
                        "name": prod_name,
                        "reason": "Model classified item as not a material (segment, component, device, or process).",
                    })
                    continue
                form_unspecified = material_form not in MATERIAL_FORMS
                if form_unspecified:
                    adv["material_form"] = "unspecified"

                # --- admission gate: category ---
                raw_category = prod.get("category") or ""
                category_trace: list = []
                clean_category, used_fallback = resolve_adv_category(raw_category, prod_name, category_trace)
                if not clean_category:
                    reasons = _reasons_text(category_trace)
                    REJECT_STATS[(raw_category or "<empty>", f"category:{reasons}")] += 1
                    excluded_items.append({
                        "name": prod_name,
                        "reason": f"Rejected category '{raw_category}' ({reasons}) and name gave no valid fallback class.",
                    })
                    continue
                if used_fallback:
                    REJECT_STATS[(raw_category or "<empty>", "category:fell-back-to-name")] += 1

                norm_prod = re.sub(r"[^a-z0-9]", "", prod_name.lower())
                if norm_prod in seen_product_names:
                    continue
                seen_product_names.add(norm_prod)

                cleaned_rms = []
                seen_rm_names = set()
                for rm in prod.get("raw_materials", []):
                    cref = rm.get("commodity_ref")
                    if cref not in registry:
                        cref = None
                    clean_rm_name = normalize_raw_material_name(rm.get("name") or "", cref)
                    if not clean_rm_name:
                        continue
                    if clean_rm_name.lower() in seen_rm_names:
                        continue
                    seen_rm_names.add(clean_rm_name.lower())
                    cleaned_rms.append({
                        "name": clean_rm_name,
                        "commodity_ref": cref,
                        "role": rm.get("role", "input"),
                        "basis": rm.get("basis") if rm.get("basis") in ("disclosed", "inferred") else "inferred",
                    })

                ds = dict(ds_raw)
                dref = ds.get("demand_proxy_ref")
                if dref not in registry:
                    dref = None
                ds["demand_proxy_ref"] = dref
                ds.setdefault("process_step", "downstream processing")
                ds.setdefault("end_product", "manufactured component")
                ds.setdefault("end_demand", "industrial end-demand")

                raw_customers = ds.get("customers")
                if isinstance(raw_customers, list):
                    filtered_custs = [
                        c.strip()
                        for c in raw_customers
                        if isinstance(c, str) and c.strip() and not NON_MAT_CUSTOMER_RE.search(c)
                    ]
                    ds["customers"] = filtered_custs or None
                else:
                    ds["customers"] = None

                rev = prod.get("revenue") or {"basis": "business_unit", "share_pct": None}
                src = prod.get("src") or {"type": src_type, "ref": src_ref}
                if src.get("type") not in ("annual_report", "filing", "product_page", "inferred"):
                    src["type"] = src_type

                status = prod.get("status") if prod.get("status") in ("active", "divested") else "active"
                conf = prod.get("conf") if prod.get("conf") in ("high", "med", "low") else "med"
                if used_fallback or form_unspecified:
                    conf = "low"
                valid_peers = list(dict.fromkeys(p for p in prod.get("peers", []) if p in universe and p != ticker))
                prod_id = _normalize_product_slug(prod.get("id", ""), ticker, prod_name)

                cur.execute(
                    """
                    INSERT INTO company_products (
                        id, ticker, name, category, business_unit, status, as_of,
                        advanced, raw_materials, downstream, pricing_structure,
                        revenue, peers, src, conf
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s::jsonb, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        category = EXCLUDED.category,
                        business_unit = EXCLUDED.business_unit,
                        status = EXCLUDED.status,
                        as_of = EXCLUDED.as_of,
                        advanced = EXCLUDED.advanced,
                        raw_materials = EXCLUDED.raw_materials,
                        downstream = EXCLUDED.downstream,
                        pricing_structure = EXCLUDED.pricing_structure,
                        revenue = EXCLUDED.revenue,
                        peers = EXCLUDED.peers,
                        src = EXCLUDED.src,
                        conf = EXCLUDED.conf,
                        updated_at = NOW();
                    """,
                    (
                        prod_id,
                        ticker,
                        prod_name,
                        clean_category,
                        prod.get("business_unit", sub_sector or "Core Materials"),
                        status,
                        prod.get("as_of", "FY2025"),
                        json.dumps(adv),
                        json.dumps(cleaned_rms),
                        json.dumps(ds),
                        prod.get("pricing_structure"),
                        json.dumps(rev),
                        valid_peers,
                        json.dumps(src),
                        conf,
                    ),
                )
                saved_products += 1

            seen_excluded = set()
            for ex in excluded_items:
                ex_name = (ex.get("name") or "").strip()
                ex_reason = (ex.get("reason") or "").strip()
                if not ex_name or not ex_reason:
                    continue
                ex_key = ex_name.lower()
                if ex_key in seen_excluded:
                    continue
                seen_excluded.add(ex_key)
                cur.execute(
                    """
                    INSERT INTO company_excluded_products (ticker, name, reason)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (ticker, name) DO UPDATE SET reason = EXCLUDED.reason, updated_at = NOW();
                    """,
                    (ticker, ex_name, ex_reason),
                )
        conn.commit()
        print(
            f"[{ticker}] Saved {saved_products} products & {len(seen_excluded)} excluded items.",
            flush=True,
        )

    print(
        f"Product extraction finished ({gemini_calls_used} Gemini calls, {groq_calls_used} Groq calls used).",
        flush=True,
    )
    if REJECT_STATS:
        print("Admission-gate rejections this run (most frequent first):", flush=True)
        for (raw, reason), n in REJECT_STATS.most_common(25):
            print(f"  {n:>3}x  {reason:<40} {raw[:70]}", flush=True)
    return conn


def run_repair(conn, apply: bool = False):
    """Re-applies the admission gate to existing active rows.

    Dry-run by default. With apply=True:
      - rows whose name or category cannot be resolved are moved to
        company_excluded_products and deleted from company_products
        (insert + delete in one transaction);
      - rows with a non-canonical category are updated in place
        (conf set to 'low' when the class had to be derived from the name);
      - raw_materials lists are re-normalized and deduplicated.
    """
    conn = ensure_conn(conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, ticker, name, category, raw_materials, conf
            FROM company_products
            WHERE status = 'active';
        """)
        rows = cur.fetchall()

    moved = Counter()
    recat = Counter()
    raw_fixed = 0
    samples: list = []

    for pid, ticker, name, category, raw_mats, conf in rows:
        name = name or ""
        trace: list = []
        name_reason = product_name_rejection_reason(name)
        new_cat, used_fallback = resolve_adv_category(category or "", name, trace)

        if name_reason or not new_cat:
            reason = (
                f"Repair: rejected product name ({name_reason})"
                if name_reason
                else f"Repair: rejected category '{category}' ({_reasons_text(trace)}) with no valid fallback"
            )
            moved[name_reason or _reasons_text(trace)] += 1
            samples.append(f"MOVE   {ticker:<8} {name[:45]:<45} cat={category!r}  [{reason}]")
            if apply:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO company_excluded_products (ticker, name, reason)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (ticker, name) DO UPDATE SET reason = EXCLUDED.reason, updated_at = NOW();
                            """,
                            (ticker, name, reason),
                        )
                        cur.execute("DELETE FROM company_products WHERE id = %s;", (pid,))
            continue

        if isinstance(raw_mats, str):
            try:
                raw_mats = json.loads(raw_mats)
            except Exception:
                raw_mats = []
        cleaned_rms = []
        seen = set()
        for rm in raw_mats or []:
            cref = rm.get("commodity_ref")
            clean_name = normalize_raw_material_name(rm.get("name") or "", cref)
            if not clean_name or clean_name.lower() in seen:
                continue
            seen.add(clean_name.lower())
            cleaned_rms.append({**rm, "name": clean_name})
        raw_changed = cleaned_rms != (raw_mats or [])

        cat_changed = new_cat != category
        if cat_changed:
            recat[f"{category!r} -> {new_cat!r}"] += 1
            samples.append(f"RECAT  {ticker:<8} {name[:45]:<45} {category!r} -> {new_cat!r}")
        if raw_changed:
            raw_fixed += 1

        if apply and (cat_changed or raw_changed):
            new_conf = "low" if (used_fallback and cat_changed) else conf
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE company_products
                    SET category = %s, raw_materials = %s::jsonb, conf = %s, updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (new_cat, json.dumps(cleaned_rms), new_conf, pid),
                )

    mode = "APPLIED" if apply else "DRY RUN (pass --apply to write)"
    print(f"Repair {mode}: scanned {len(rows)} active products.", flush=True)
    print(f"  moved to excluded : {sum(moved.values())}", flush=True)
    print(f"  recategorized     : {sum(recat.values())}", flush=True)
    print(f"  raw lists cleaned : {raw_fixed}", flush=True)
    if moved:
        print("  move reasons:", flush=True)
        for reason, n in moved.most_common():
            print(f"    {n:>4}x {reason}", flush=True)
    for line in samples[:60]:
        print("  " + line, flush=True)
    if len(samples) > 60:
        print(f"  ... {len(samples) - 60} more", flush=True)
    return conn


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Advanced-materials product extraction and repair.")
    parser.add_argument("--repair", action="store_true", help="Re-apply admission gate to existing rows (dry-run unless --apply).")
    parser.add_argument("--apply", action="store_true", help="With --repair, write changes.")
    args = parser.parse_args()

    conn = connect_db()
    try:
        if args.repair:
            conn = run_repair(conn, apply=args.apply)
        else:
            conn = run_product_extraction(conn)
    finally:
        if conn and not conn.closed:
            conn.close()
