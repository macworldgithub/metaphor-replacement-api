import re
from typing import List, Dict, Tuple
from app.models.schemas import (
    MetaphorMatch,
    ReplacementStyle,
    CatalogItem,
)

# Built-in knowledge base of common metaphorical idioms & expressions
METAPHOR_DATABASE: List[Dict[str, str]] = [
    {
        "phrase": "bite the bullet",
        "category": "Courage / Endurance",
        "literal": "face the difficult situation bravely",
        "creative": "swallow the sharp truth",
        "professional": "proceed with the unavoidable challenge",
        "meaning": "To accept something unpleasant that cannot be avoided.",
    },
    {
        "phrase": "opening a can of worms",
        "category": "Complication / Risk",
        "literal": "introducing a complex set of unforeseen problems",
        "creative": "unleashing a tangled web of mysteries",
        "professional": "uncovering significant secondary liabilities",
        "meaning": "Creating a situation that causes unexpected, hard-to-solve issues.",
    },
    {
        "phrase": "tip of the iceberg",
        "category": "Perception / Scale",
        "literal": "small perceptible portion of a much larger issue",
        "creative": "single sparkle of a vast deep ocean",
        "professional": "preliminary indicator of broader systemic factors",
        "meaning": "Only a tiny noticeable fraction of a larger hidden problem.",
    },
    {
        "phrase": "break the ice",
        "category": "Social / Communication",
        "literal": "initiate conversation to ease tension",
        "creative": "kindle warmth across still waters",
        "professional": "facilitate opening stakeholder dialogue",
        "meaning": "To relieve social awkwardness or start communication in an unfamiliar setting.",
    },
    {
        "phrase": "burn bridges",
        "category": "Relationships / Decisions",
        "literal": "permanently destroy working relationships or future opportunities",
        "creative": "scorch the path behind oneself",
        "professional": "irreversibly terminate strategic relations",
        "meaning": "Acting in a way that eliminates options for returning or reconciling.",
    },
    {
        "phrase": "time is money",
        "category": "Economics / Efficiency",
        "literal": "time spent corresponds directly to financial productivity",
        "creative": "each tick costs a treasure",
        "professional": "temporal efficiency directly governs cost optimization",
        "meaning": "Time is a valuable resource that should not be wasted.",
    },
    {
        "phrase": "rollercoaster of emotions",
        "category": "Feelings / Psychology",
        "literal": "series of intense and fluctuating emotional experiences",
        "creative": "tempestuous dance of shadows and euphoria",
        "professional": "period of significant operational volatility",
        "meaning": "Experiencing rapid, unpredictable shifts between highs and lows.",
    },
    {
        "phrase": "heart of stone",
        "category": "Personality / Empathy",
        "literal": "complete lack of sympathy or emotion",
        "creative": "soul forged in frozen granite",
        "professional": "strictly unsentimental demeanor",
        "meaning": "Being cold, unfeeling, or unforgiving.",
    },
    {
        "phrase": "on cloud nine",
        "category": "Happiness / Joy",
        "literal": "extremely delighted and fulfilled",
        "creative": "floating on celestial starlight",
        "professional": "exceptionally gratified with the outcome",
        "meaning": "Experiencing great elation or euphoric happiness.",
    },
    {
        "phrase": "climbing the corporate ladder",
        "category": "Career / Ambition",
        "literal": "advancing sequentially through organizational promotions",
        "creative": "scaling the glass peaks of industry",
        "professional": "advancing leadership seniority within the hierarchy",
        "meaning": "Making progress towards higher positions in a company hierarchy.",
    },
    {
        "phrase": "cold feet",
        "category": "Hesitation / Fear",
        "literal": "sudden hesitation or second thoughts",
        "creative": "a sudden chill of hesitation",
        "professional": "late-stage risk aversion",
        "meaning": "Experiencing nervousness or doubt before committing to a major event.",
    },
    {
        "phrase": "spill the beans",
        "category": "Information / Secrecy",
        "literal": "reveal confidential information prematurely",
        "creative": "let the hidden song slip away",
        "professional": "prematurely disclose confidential disclosures",
        "meaning": "Divulging a secret, intentionally or accidentally.",
    },
    {
        "phrase": "light at the end of the tunnel",
        "category": "Hope / Resolution",
        "literal": "an indication of relief or resolution following a difficult period",
        "creative": "a golden dawn piercing the deep night",
        "professional": "clear path towards operational stabilization",
        "meaning": "A signal that a troublesome phase is approaching its conclusion.",
    },
]


class MetaphorService:
    def __init__(self):
        self.catalog = METAPHOR_DATABASE

    def get_catalog(self) -> List[CatalogItem]:
        return [
            CatalogItem(
                phrase=item["phrase"],
                category=item["category"],
                literal=item["literal"],
                creative=item["creative"],
                professional=item["professional"],
                meaning=item["meaning"],
            )
            for item in self.catalog
        ]

    def find_metaphors(self, text: str) -> List[Tuple[MetaphorMatch, Dict[str, str]]]:
        matches: List[Tuple[MetaphorMatch, Dict[str, str]]] = []
        lower_text = text.lower()

        for entry in self.catalog:
            phrase = entry["phrase"].lower()
            # Find all occurrences of the phrase
            pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
            for match in pattern.finditer(text):
                start, end = match.span()
                matched_text = text[start:end]

                metaphor_match = MetaphorMatch(
                    phrase=matched_text,
                    category=entry["category"],
                    literal_replacement=entry["literal"],
                    creative_replacement=entry["creative"],
                    professional_replacement=entry["professional"],
                    explanation=entry["meaning"],
                    start_pos=start,
                    end_pos=end,
                )
                matches.append((metaphor_match, entry))

        # Sort matches by start position
        matches.sort(key=lambda m: m[0].start_pos)
        return matches

    def analyze(self, text: str, style: ReplacementStyle) -> Tuple[List[MetaphorMatch], str]:
        detected = self.find_metaphors(text)
        if not detected:
            return [], text

        matches_list: List[MetaphorMatch] = []
        transformed_parts = []
        last_idx = 0

        for match, entry in detected:
            matches_list.append(match)

            # Get replacement according to selected style
            if style == ReplacementStyle.CREATIVE:
                replacement = entry.get("creative") or entry["literal"]
            elif style == ReplacementStyle.PROFESSIONAL:
                replacement = entry.get("professional") or entry["literal"]
            else:
                replacement = entry["literal"]

            # Respect original capitalization of the first character
            if match.phrase and match.phrase[0].isupper():
                replacement = replacement[:1].upper() + replacement[1:]

            transformed_parts.append(text[last_idx:match.start_pos])
            transformed_parts.append(replacement)
            last_idx = match.end_pos

        transformed_parts.append(text[last_idx:])
        transformed_text = "".join(transformed_parts)

        return matches_list, transformed_text

    def replace(
        self,
        text: str,
        style: ReplacementStyle,
        custom_overrides: Dict[str, str] = None,
    ) -> Tuple[str, int]:
        count = 0
        working_text = text

        # Apply custom overrides first if provided
        if custom_overrides:
            for source, target in custom_overrides.items():
                pattern = re.compile(rf"\b{re.escape(source)}\b", re.IGNORECASE)
                working_text, n = pattern.subn(target, working_text)
                count += n

        # Apply catalog metaphors
        matches, transformed = self.analyze(working_text, style)
        count += len(matches)

        return transformed, count


metaphor_service = MetaphorService()
