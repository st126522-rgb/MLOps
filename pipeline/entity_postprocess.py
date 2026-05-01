"""
Entity post-processing helpers.

The base NER model only emits ORG, PER, LOC, and MISC. This layer adds a
domain-specific MODEL type for AI model/product names and normalizes obvious
model mentions before they reach the graph, label queue, and training loop.
"""

import re


MODEL_PATTERNS = [
    r"\bGPT-?\d+(?:\.\d+)?[A-Za-z]?\b",
    r"\bGPT-[45]o\b",
    r"\bChatGPT\b",
    r"\bClaude(?:\s+\d+(?:\.\d+)?)?\b",
    r"\bGemini(?:\s+\d+(?:\.\d+)?)?(?:\s+Pro|\s+Flash|\s+Ultra)?\b",
    r"\bDeepSeek(?:[-\s]?(?:R1|R2|V2|V3|Coder))?\b",
    r"\bLlama\s*\d+(?:\.\d+)?\b",
    r"\bMistral(?:\s+Large|\s+Small)?\b",
    r"\bMixtral(?:\s+\d+x\d+[A-Za-z]?)?\b",
    r"\bGrok(?:\s+\d+(?:\.\d+)?)?\b",
    r"\bQwen(?:\s*\d+(?:\.\d+)?)?(?:\s*VL|\s*Coder)?\b",
    r"\bCommand\s+R\+?\b",
    r"\bSora\b",
    r"\bDALL-?E(?:\s*\d+)?\b",
    r"\bMidjourney(?:\s+v?\d+)?\b",
    r"\bStable\s+Diffusion(?:\s+\d+(?:\.\d+)?)?\b",
]

MODEL_REGEXES = [re.compile(pattern, re.IGNORECASE) for pattern in MODEL_PATTERNS]


def is_model_name(value: str) -> bool:
    return any(regex.fullmatch(value.strip()) for regex in MODEL_REGEXES)


def model_mentions(text: str) -> list[dict]:
    mentions = []
    seen = set()
    for regex in MODEL_REGEXES:
        for match in regex.finditer(text):
            entity = match.group(0).strip()
            key = (entity.lower(), match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            mentions.append(
                {
                    "entity": entity,
                    "type": "MODEL",
                    "confidence": 0.99,
                    "flagged": False,
                    "label_threshold": None,
                    "start": match.start(),
                    "end": match.end(),
                    "source": "rule:model_lexicon",
                }
            )
    return mentions


def normalize_entity_type(entity: dict) -> dict:
    updated = dict(entity)
    if is_model_name(str(updated.get("entity", ""))):
        updated["type"] = "MODEL"
        updated["source"] = updated.get("source", "model_type_override")
    return updated


def merge_model_entities(text: str, entities: list[dict]) -> list[dict]:
    normalized = [normalize_entity_type(entity) for entity in entities]
    occupied = {(item.get("start"), item.get("end"), str(item.get("entity", "")).lower()) for item in normalized}

    for model in model_mentions(text):
        key = (model["start"], model["end"], model["entity"].lower())
        if key not in occupied:
            normalized.append(model)
            occupied.add(key)

    return sorted(normalized, key=lambda item: (item.get("start", 0), item.get("end", 0), item.get("entity", "")))
