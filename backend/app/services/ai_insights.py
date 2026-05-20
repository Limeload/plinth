import os
from functools import lru_cache

COST_HEAD_CATEGORIES = [
    "Civil Structure", "MEP", "Finishing",
    "External Development", "Labour", "Equipment", "Misc",
]

_RULES = """
- Civil Structure: foundation, slab, columns, beams, brickwork, RCC, concrete, cement, steel, shuttering, sand, aggregate, formwork
- MEP: electrical, plumbing, HVAC, fire fighting, elevator, wiring, conduit, pipes, sanitary fittings, switchgear
- Finishing: tiles, flooring, painting, plastering, doors, windows, woodwork, false ceiling, aluminium, glass, granite, marble
- External Development: landscaping, road, parking, compound wall, boundary wall, drainage, STP, rain water harvesting
- Labour: wages, labour charges, manpower, workforce, skilled labour, unskilled labour, contract labour
- Equipment: JCB, crane, scaffolding, machinery hire, tools, transit mixer, tipper, excavator
- Misc: anything that doesn't clearly fit the above
"""


@lru_cache(maxsize=512)
def normalise_cost_head(raw_line_item: str) -> str:
    """
    Map a free-text invoice description to a standard cost head category.
    Falls back to "Misc" if ANTHROPIC_API_KEY is not set or the API call fails.
    Results are cached in-process — same text always returns the same category.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "Misc"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": (
                    f"Classify this construction invoice line item into exactly one category.\n"
                    f"Categories: {', '.join(COST_HEAD_CATEGORIES)}\n\n"
                    f"Classification rules:{_RULES}\n"
                    f'Line item: "{raw_line_item}"\n'
                    "Return only the category name. Nothing else."
                ),
            }],
        )
        result = response.content[0].text.strip()
        return result if result in COST_HEAD_CATEGORIES else "Misc"
    except Exception:
        return "Misc"


def generate_health_summary(data: dict) -> str:
    """Generate a 2-3 sentence plain-language project health summary."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ""

    try:
        import anthropic
        import json
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    "You are a construction finance analyst. Given this project data, write a 2-3 sentence "
                    "plain-language status summary for a builder (not a software user). "
                    "Use INR amounts. Be specific. Flag the most important risk if any exists.\n"
                    f"Data: {json.dumps(data)}\n"
                    "Return only the summary text. No bullet points. No headers."
                ),
            }],
        )
        return response.content[0].text.strip()
    except Exception:
        return ""
