"""
Canonical cost-head normaliser for Plinth.

Converts any free-text cost description — from paper registers, Excel sheets,
Tally ledger exports, or WhatsApp messages — to one of the 7 standard categories.

Resolution order (first match wins):
  1. Exact lookup on normalised text              O(1)
  2. Substring scan (handles prefixes/suffixes)
  3. Token + bigram match (handles long descriptions)
  4. Fuzzy match via difflib (handles typos)
  5. Claude API — last resort, only when all else fails
"""
import difflib
import re
from functools import lru_cache
from typing import Optional

CANONICAL = [
    "Civil Structure",
    "MEP",
    "Finishing",
    "External Development",
    "Labour",
    "Equipment",
    "Misc",
]

# ── Keyword map ───────────────────────────────────────────────────────────────
# Keys: lowercase, stripped.  Covers standard names, abbreviations,
# Tally ledger prefixes, paper-record shorthand, and common Hindi transliterations.

KEYWORD_MAP: dict[str, str] = {

    # ── Civil Structure ───────────────────────────────────────────────────────
    "civil": "Civil Structure",
    "civil structure": "Civil Structure",
    "civil work": "Civil Structure",
    "civil works": "Civil Structure",
    "civil construction": "Civil Structure",
    "civil & structure": "Civil Structure",
    # Concrete / concrete mix
    "cc work": "Civil Structure",
    "cc works": "Civil Structure",
    "rcc": "Civil Structure",
    "rcc work": "Civil Structure",
    "rcc works": "Civil Structure",
    "r.c.c": "Civil Structure",
    "r.c.c.": "Civil Structure",
    "pcc": "Civil Structure",
    "pcc work": "Civil Structure",
    "p.c.c": "Civil Structure",
    "p.c.c.": "Civil Structure",
    "plain cement concrete": "Civil Structure",
    "reinforced cement concrete": "Civil Structure",
    "concrete": "Civil Structure",
    "concrete work": "Civil Structure",
    "rmc": "Civil Structure",
    "ready mix": "Civil Structure",
    "ready mix concrete": "Civil Structure",
    "ready mixed concrete": "Civil Structure",
    # Materials — cement, steel, sand
    "cement": "Civil Structure",
    "cement purchase": "Civil Structure",
    "cement supply": "Civil Structure",
    "cement kaam": "Civil Structure",
    "opc": "Civil Structure",
    "opc cement": "Civil Structure",
    "ppc cement": "Civil Structure",
    "steel": "Civil Structure",
    "steel purchase": "Civil Structure",
    "steel supply": "Civil Structure",
    "tmt": "Civil Structure",
    "tmt bars": "Civil Structure",
    "tmt steel": "Civil Structure",
    "ms steel": "Civil Structure",
    "ms bars": "Civil Structure",
    "ms rod": "Civil Structure",
    "tor steel": "Civil Structure",
    "sand": "Civil Structure",
    "sand purchase": "Civil Structure",
    "river sand": "Civil Structure",
    "m-sand": "Civil Structure",
    "msand": "Civil Structure",
    "manufactured sand": "Civil Structure",
    "aggregate": "Civil Structure",
    "aggregate purchase": "Civil Structure",
    "coarse aggregate": "Civil Structure",
    "fine aggregate": "Civil Structure",
    "grit": "Civil Structure",
    "gravel": "Civil Structure",
    "jelly": "Civil Structure",
    "metal": "Civil Structure",            # "metal" = aggregate in South India
    # Structure elements
    "structure": "Civil Structure",
    "structural": "Civil Structure",
    "structural work": "Civil Structure",
    "foundation": "Civil Structure",
    "neev": "Civil Structure",             # Hindi: foundation
    "footing": "Civil Structure",
    "footings": "Civil Structure",
    "raft": "Civil Structure",
    "raft foundation": "Civil Structure",
    "pile": "Civil Structure",
    "piling": "Civil Structure",
    "plinth beam": "Civil Structure",
    "plinth": "Civil Structure",
    "lintel": "Civil Structure",
    "parapet": "Civil Structure",
    "column": "Civil Structure",
    "columns": "Civil Structure",
    "beam": "Civil Structure",
    "beams": "Civil Structure",
    "slab": "Civil Structure",
    "slabs": "Civil Structure",
    "staircase": "Civil Structure",
    "stair": "Civil Structure",
    "retaining wall": "Civil Structure",
    # Masonry & blockwork
    "brickwork": "Civil Structure",
    "brick work": "Civil Structure",
    "masonry": "Civil Structure",
    "block work": "Civil Structure",
    "blockwork": "Civil Structure",
    "aac": "Civil Structure",
    "aac block": "Civil Structure",
    "aac blocks": "Civil Structure",
    "fly ash brick": "Civil Structure",
    "fly ash bricks": "Civil Structure",
    # Shuttering / formwork
    "shuttering": "Civil Structure",
    "formwork": "Civil Structure",
    "centering": "Civil Structure",
    "centering & shuttering": "Civil Structure",
    "deshuttering": "Civil Structure",
    # Earthwork
    "excavation": "Civil Structure",
    "earth work": "Civil Structure",
    "earthwork": "Civil Structure",
    "backfilling": "Civil Structure",
    "back filling": "Civil Structure",
    "filling": "Civil Structure",
    "hardcore": "Civil Structure",
    "dpc": "Civil Structure",              # Damp Proof Course
    "waterproofing": "Civil Structure",
    "water proofing": "Civil Structure",
    "terrace waterproofing": "Civil Structure",
    "purchase - civil": "Civil Structure",
    "purchase - structure": "Civil Structure",

    # ── MEP ───────────────────────────────────────────────────────────────────
    "mep": "MEP",
    "m&e": "MEP",
    "m & e": "MEP",
    "mechanical & electrical": "MEP",
    "mechanical electrical plumbing": "MEP",
    # Electrical
    "electrical": "MEP",
    "electricals": "MEP",
    "electrical work": "MEP",
    "electrical works": "MEP",
    "electrical installation": "MEP",
    "electrical fitting": "MEP",
    "electrification": "MEP",
    "e/w": "MEP",
    "elec": "MEP",
    "elect": "MEP",
    "bijli": "MEP",                        # Hindi: electricity
    "bijli kaam": "MEP",
    "wiring": "MEP",
    "conduit": "MEP",
    "conduit & wiring": "MEP",
    "switchgear": "MEP",
    "mcb": "MEP",
    "db box": "MEP",
    "distribution board": "MEP",
    "earthing": "MEP",
    "lightning arrester": "MEP",
    "solar": "MEP",
    "solar panel": "MEP",
    "dg set": "MEP",
    "diesel generator": "MEP",
    "generator": "MEP",
    # Plumbing
    "plumbing": "MEP",
    "plumbing work": "MEP",
    "plumbing works": "MEP",
    "plumbing & sanitary": "MEP",
    "p/w": "MEP",
    "plmb": "MEP",
    "plbg": "MEP",
    "paani kaam": "MEP",                   # Hindi: water work
    "piping": "MEP",
    "pipes": "MEP",
    "pipes & fittings": "MEP",
    "cpvc": "MEP",
    "upvc pipes": "MEP",
    "pvc pipes": "MEP",
    "gi pipes": "MEP",
    "ms pipes": "MEP",
    "hdpe pipes": "MEP",
    "sanitary": "MEP",
    "sanitary fittings": "MEP",
    "sanitary ware": "MEP",
    "cp fittings": "MEP",
    "cp fitting": "MEP",
    # HVAC / Fire / Lift
    "hvac": "MEP",
    "air conditioning": "MEP",
    "ac work": "MEP",
    "ducting": "MEP",
    "fire fighting": "MEP",
    "fire safety": "MEP",
    "fire protection": "MEP",
    "sprinkler": "MEP",
    "lift": "MEP",
    "elevator": "MEP",
    "purchase - mep": "MEP",

    # ── Finishing ─────────────────────────────────────────────────────────────
    "finishing": "Finishing",
    "finishing work": "Finishing",
    "finishing works": "Finishing",
    "finish": "Finishing",
    "interior": "Finishing",
    "interiors": "Finishing",
    "interior work": "Finishing",
    "interior works": "Finishing",
    # Tiles & flooring
    "tiles": "Finishing",
    "tile work": "Finishing",
    "tiling": "Finishing",
    "floor tiles": "Finishing",
    "wall tiles": "Finishing",
    "vitrified tiles": "Finishing",
    "ceramic tiles": "Finishing",
    "mosaic tiles": "Finishing",
    "flooring": "Finishing",
    "flooring work": "Finishing",
    "granite": "Finishing",
    "marble": "Finishing",
    "kota stone": "Finishing",
    "kadappa": "Finishing",
    "wooden flooring": "Finishing",
    "vinyl flooring": "Finishing",
    "epoxy flooring": "Finishing",
    # Painting & plastering
    "painting": "Finishing",
    "paint": "Finishing",
    "painting work": "Finishing",
    "painting works": "Finishing",
    "rang": "Finishing",                   # Hindi: paint/colour
    "rangai": "Finishing",
    "primer": "Finishing",
    "distemper": "Finishing",
    "emulsion": "Finishing",
    "enamel": "Finishing",
    "texture paint": "Finishing",
    "p&p": "Finishing",
    "plastering": "Finishing",
    "plaster": "Finishing",
    "plaster work": "Finishing",
    "plastering work": "Finishing",
    "pop": "Finishing",                    # Plaster of Paris
    "pop work": "Finishing",
    "gypsum": "Finishing",
    "gypsum plaster": "Finishing",
    "false ceiling": "Finishing",
    "false ceiling work": "Finishing",
    "f/c": "Finishing",
    "gypsum ceiling": "Finishing",
    "grid ceiling": "Finishing",
    # Doors, windows, glazing
    "doors": "Finishing",
    "door": "Finishing",
    "windows": "Finishing",
    "window": "Finishing",
    "doors & windows": "Finishing",
    "d&w": "Finishing",
    "door frames": "Finishing",
    "upvc windows": "Finishing",
    "aluminium windows": "Finishing",
    "aluminium": "Finishing",
    "aluminium work": "Finishing",
    "glass": "Finishing",
    "glazing": "Finishing",
    "partition": "Finishing",
    "glass partition": "Finishing",
    # Woodwork & hardware
    "woodwork": "Finishing",
    "wood work": "Finishing",
    "carpentry": "Finishing",
    "joinery": "Finishing",
    "modular kitchen": "Finishing",
    "wardrobes": "Finishing",
    "wardrobe": "Finishing",
    "cabinet": "Finishing",
    "hardware": "Finishing",
    # Railings, cladding
    "railing": "Finishing",
    "handrail": "Finishing",
    "ms railing": "Finishing",
    "ss railing": "Finishing",
    "cladding": "Finishing",
    "stone cladding": "Finishing",
    "purchase - finishing": "Finishing",
    "purchase - interior": "Finishing",

    # ── External Development ──────────────────────────────────────────────────
    "external": "External Development",
    "external development": "External Development",
    "external works": "External Development",
    "ext dev": "External Development",
    "ext. dev": "External Development",
    "ext development": "External Development",
    # Landscaping
    "landscaping": "External Development",
    "landscape": "External Development",
    "lawn": "External Development",
    "garden": "External Development",
    "horticulture": "External Development",
    "plantation": "External Development",
    # Walls & fencing
    "compound wall": "External Development",
    "boundary wall": "External Development",
    "c/w": "External Development",
    "b/w": "External Development",
    "fencing": "External Development",
    "gate": "External Development",
    "main gate": "External Development",
    "entrance gate": "External Development",
    # Roads & paving
    "road": "External Development",
    "road work": "External Development",
    "road works": "External Development",
    "internal road": "External Development",
    "paving": "External Development",
    "interlocking": "External Development",
    "interlocking paver": "External Development",
    "footpath": "External Development",
    "pavement": "External Development",
    "kerb": "External Development",
    "parking": "External Development",
    "parking area": "External Development",
    # Water & drainage
    "drainage": "External Development",
    "storm drainage": "External Development",
    "naali": "External Development",       # Hindi: drain/channel
    "stp": "External Development",
    "sewage treatment": "External Development",
    "sewage treatment plant": "External Development",
    "rainwater harvesting": "External Development",
    "rain water harvesting": "External Development",
    "rwh": "External Development",
    "overhead tank": "External Development",
    "oht": "External Development",
    "sump": "External Development",
    "water tank": "External Development",
    # Street lighting & amenities
    "street light": "External Development",
    "street lighting": "External Development",
    "external lighting": "External Development",
    "swimming pool": "External Development",
    "clubhouse": "External Development",
    "club house": "External Development",
    "amenities": "External Development",
    "common area": "External Development",
    "common area works": "External Development",
    "purchase - external": "External Development",

    # ── Labour ────────────────────────────────────────────────────────────────
    "labour": "Labour",
    "labor": "Labour",
    "labour charges": "Labour",
    "labor charges": "Labour",
    "labour expenses": "Labour",
    "labor expenses": "Labour",
    "labour cost": "Labour",
    "labor cost": "Labour",
    "wages": "Labour",
    "site wages": "Labour",
    "daily wages": "Labour",
    "d/w": "Labour",
    "wage": "Labour",
    "manpower": "Labour",
    "manpower expenses": "Labour",
    "manpower supply": "Labour",
    "contract labour": "Labour",
    "contract labor": "Labour",
    "labour contractor": "Labour",
    "skilled labour": "Labour",
    "unskilled labour": "Labour",
    "skilled labor": "Labour",
    "unskilled labor": "Labour",
    "site labour": "Labour",
    "site labor": "Labour",
    "labour payment": "Labour",
    "labor payment": "Labour",
    "wages payable": "Labour",
    "salary": "Labour",
    "majdoor": "Labour",                   # Hindi: labourer
    "mazdar": "Labour",
    "lab": "Labour",
    "labr": "Labour",
    "labour & material": "Labour",
    "labor & material": "Labour",

    # ── Equipment ─────────────────────────────────────────────────────────────
    "equipment": "Equipment",
    "equipment hire": "Equipment",
    "equipment charges": "Equipment",
    "plant": "Equipment",
    "plant & machinery": "Equipment",
    "plant and machinery": "Equipment",
    "p&m": "Equipment",
    "p & m": "Equipment",
    "machinery": "Equipment",
    "machinery hire": "Equipment",
    "machine hire": "Equipment",
    "m/c": "Equipment",
    "machines": "Equipment",
    "jcb": "Equipment",
    "jcb charges": "Equipment",
    "jcb hire": "Equipment",
    "crane": "Equipment",
    "crane charges": "Equipment",
    "crane hire": "Equipment",
    "excavator": "Equipment",
    "loader": "Equipment",
    "bobcat": "Equipment",
    "dozer": "Equipment",
    "bulldozer": "Equipment",
    "scaffolding": "Equipment",
    "scaffold": "Equipment",
    "scaffolding charges": "Equipment",
    "scaffolding hire": "Equipment",
    "transit mixer": "Equipment",
    "tipper": "Equipment",
    "dumper": "Equipment",
    "concrete pump": "Equipment",
    "pump charges": "Equipment",
    "compactor": "Equipment",
    "roller": "Equipment",
    "vibrator": "Equipment",
    "poker vibrator": "Equipment",
    "mixer": "Equipment",
    "mixer machine": "Equipment",
    "compressor": "Equipment",
    "grinder": "Equipment",
    "tools": "Equipment",
    "tools & tackles": "Equipment",
    "tools and tackles": "Equipment",
    "hire charges": "Equipment",

    # ── Misc ──────────────────────────────────────────────────────────────────
    "misc": "Misc",
    "miscellaneous": "Misc",
    "miscellaneous expenses": "Misc",
    "general": "Misc",
    "general expenses": "Misc",
    "petty cash": "Misc",
    "petty expenses": "Misc",
    "p/c": "Misc",
    "site expenses": "Misc",
    "office expenses": "Misc",
    "admin": "Misc",
    "administration": "Misc",
    "overhead": "Misc",
    "overheads": "Misc",
    "security": "Misc",
    "security charges": "Misc",
    "insurance": "Misc",
    "bank charges": "Misc",
    "bank interest": "Misc",
    "legal": "Misc",
    "legal fees": "Misc",
    "professional fees": "Misc",
    "architect fees": "Misc",
    "consultant fees": "Misc",
    "consultancy": "Misc",
    "noc": "Misc",
    "liaison": "Misc",
    "testing": "Misc",
    "soil testing": "Misc",
    "quality testing": "Misc",
    "lab testing": "Misc",
    "documentation": "Misc",
    "stamp duty": "Misc",
    "registration": "Misc",
    "other": "Misc",
    "others": "Misc",
    "direct expenses": "Misc",
    "indirect expenses": "Misc",
}

# ── Preprocessing ─────────────────────────────────────────────────────────────

# Tally-style prefixes that carry no category meaning
_PREFIX_RE = re.compile(
    r"^(purchase|payment|expenses?|cost of|supply of|supply &|supply and)\s*[-–:]\s*",
    re.IGNORECASE,
)

# Punctuation to collapse
_PUNCT_RE = re.compile(r"[.,;:()&/\\]")


def _normalise(text: str) -> str:
    """Lowercase, strip Tally prefixes, collapse punctuation and whitespace."""
    t = _PREFIX_RE.sub("", text.strip())
    t = _PUNCT_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


# ── Lookup helpers ────────────────────────────────────────────────────────────

def _exact(normalised: str) -> Optional[str]:
    return KEYWORD_MAP.get(normalised)


def _substring(normalised: str) -> Optional[str]:
    """Check if any keyword is contained in the input string."""
    for key, cat in KEYWORD_MAP.items():
        if key in normalised:
            return cat
    return None


def _token_match(normalised: str) -> Optional[str]:
    """
    Split the text into individual words and adjacent word pairs (bigrams),
    then look each up in the keyword map.  Handles free-text descriptions like
    "OPC 53 Cement 200 bags" → token "cement" → Civil Structure.
    """
    tokens = normalised.split()
    # single-token lookup
    for tok in tokens:
        if tok in KEYWORD_MAP:
            return KEYWORD_MAP[tok]
    # bigram lookup
    for i in range(len(tokens) - 1):
        bigram = f"{tokens[i]} {tokens[i+1]}"
        if bigram in KEYWORD_MAP:
            return KEYWORD_MAP[bigram]
    return None


# Build fuzzy candidate list once at import time.
# Only keys ≥ 5 chars to avoid short-string false positives ("lab" ≈ "slab").
_FUZZY_KEYS = [k for k in KEYWORD_MAP if len(k) >= 5]


def _fuzzy(normalised: str) -> Optional[str]:
    """
    Difflib fuzzy match with a high cutoff.
    Good for single-word typos: "labur" → "labour", "concrte" → "concrete".
    """
    if len(normalised) < 4:
        return None
    matches = difflib.get_close_matches(normalised, _FUZZY_KEYS, n=1, cutoff=0.82)
    return KEYWORD_MAP[matches[0]] if matches else None


# ── Public API ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=2048)
def resolve_category(raw_text: str) -> str:
    """
    Resolve any raw cost-head text to a standard Plinth category.

    Accepts: paper-register labels, Excel category names, Tally ledger names,
             invoice descriptions, Hindi transliterations, abbreviations.

    Returns one of: Civil Structure | MEP | Finishing | External Development |
                    Labour | Equipment | Misc
    """
    if not raw_text or not raw_text.strip():
        return "Misc"

    # Pass 1: raw lowercase lookup — preserves /&. so abbreviations like
    # E/W, M&E, P&M, R.C.C., D&W resolve without punct stripping.
    raw_lower = raw_text.strip().lower()
    hit = KEYWORD_MAP.get(raw_lower)
    if hit:
        return hit

    # Pass 2: normalised lookups (strips Tally prefixes, collapses whitespace)
    n = _normalise(raw_text)
    if not n:
        return "Misc"

    return (
        _exact(n)
        or _token_match(n)   # word-boundary match before free substring scan
        or _substring(n)
        or _fuzzy(n)
        or _claude(raw_text.strip())
    )


def resolve_best(raw_category: Optional[str], description: Optional[str]) -> str:
    """
    Two-field resolution for CSV rows that have both a category column and a
    description column.  Tries category first; falls back to description only
    if category resolves to Misc (or is absent).
    """
    if raw_category and raw_category.strip():
        result = resolve_category(raw_category.strip())
        if result != "Misc":
            return result
    if description and description.strip():
        return resolve_category(description.strip())
    return "Misc"


def _claude(raw_text: str) -> str:
    """AI fallback — imported lazily to avoid loading anthropic at module init."""
    from .ai_insights import normalise_cost_head
    return normalise_cost_head(raw_text)
