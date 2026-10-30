"""Turns a one-line premise into an anime storyboard plan with an open-weight LLM served by Ollama,
and plans into image prompts.

Kept free of ComfyUI imports so it can be tested on its own.
"""
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import List, Literal

from pydantic import BaseModel, Field

PRESETS = json.loads((Path(__file__).parent / "pacing_presets.json").read_text())
PACING_CHOICES = ["auto"] + list(PRESETS)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
# Qwen 3.6 35B (MoE, ~3B active) plans in ~15s on an L40S; Gemma 4 26B took 30-58s with similar quality.
LLM_CHOICES = ["qwen3.6:35b", "gemma4:26b"]
LENGTH_CHOICES = ["standard", "short", "long"]
_LENGTH_ASK = {"short": "the low end", "standard": "the middle", "long": "the high end"}

ShotSize = Literal["extreme wide", "wide", "full", "medium", "medium close-up", "close-up", "extreme close-up", "insert"]


class Character(BaseModel):
    name: str
    tag: str = Field(description="3-6 word visual nickname that replaces the name in image prompts, built only from traits stated in `look` (same colours), e.g. 'the silver-haired racer in purple'. Must not contain the name.")
    look: str = Field(description="Fixed visual description reused verbatim in every panel: age, hair, eyes, outfit, one distinguishing mark. 15-30 words, no actions.")


class Prop(BaseModel):
    name: str = Field(description="Short noun phrase used verbatim in every composition that shows it, e.g. \"Vex's motorcycle\"")
    tag: str = Field(description="3-6 word visual nickname that replaces the name in image prompts, built only from traits stated in `look` (same colours), e.g. 'the purple sport motorcycle'")
    look: str = Field(description="Fixed visual description: type, style, colour, distinctive details. 10-25 words.")


class Shot(BaseModel):
    cut: int
    shot_size: ShotSize
    angle: str = Field(description="Camera angle, e.g. 'low angle', 'eye level', 'Dutch angle', 'over the shoulder of Ren'")
    camera_move: str = Field(description="Camera movement for the storyboard notes, e.g. 'static', 'slow push-in', 'whip pan left'")
    characters: List[str] = Field(description="Names of every character visible in this shot, including any named in the composition (must match the characters list)")
    composition: str = Field(description="What the frame shows, written as an image prompt: subject, pose, expression, foreground/background, lighting, effects. Refer to characters and props by their exact names. 25-60 words.")
    action: str = Field(description="Storyboard note for the animator: what happens in this cut. One sentence.")
    dialogue: str = Field(description="Spoken line as 'NAME: line', or empty string")
    sfx: str = Field(description="Sound effect, or empty string")
    seconds: float


class StoryboardPlan(BaseModel):
    title: str
    logline: str
    pacing: str = Field(description="Key of the pacing preset used")
    setting: str = Field(description="Location, time of day, weather and colour palette shared by every panel. 10-25 words.")
    characters: List[Character]
    props: List[Prop] = Field(description="Every vehicle, weapon or object that appears in more than one cut, each with a fixed look. Empty list if none.")
    shots: List[Shot]


SYSTEM = """You are a veteran anime storyboard artist (ekonte) planning a single scene from a short, often vague premise.

Expand the premise into a concrete scene: invent named characters, a specific setting, and a beginning-middle-end. Then break it into cuts the way a TV anime storyboard would.

Rules:
- Match the pacing preset: shot count, per-cut seconds, rhythm and shot vocabulary. Vary shot sizes and angles; never use the same shot size three times in a row.
- Each character's `look` is reused verbatim in every panel to keep them consistent, so make it specific and visual (hair colour and style, eye colour, outfit, one distinguishing mark). At most 3 characters. Recurring animals and creatures are characters too.
- Recurring vehicles, weapons and objects go in `props` with a fixed look. Every composition that shows a prop must use its exact prop name (never just 'bike' or 'car').
- Every `tag` (character or prop) must agree with its `look`: reuse the look's own colours and key traits, never introduce new ones.
- `composition` is fed to an anime image model. Describe only what is visible in that single frozen frame. Refer to characters and props by their exact names; do not restate their looks there. No camera-move language, no text, no speech bubbles.
- Keep at most 2 characters in any single frame. Close-ups, extreme close-ups and inserts show at most 1 character (a hand or object from a second character is fine if that character is not listed).
- Dialogue is sparse and in English. Most cuts have none.
- Everything must suit a general audience: no nudity, sexual content or graphic gore. If the premise asks for those, write a tame version instead.

Pacing presets:
{presets}"""


def _presets_text():
    lines = []
    for key, p in PRESETS.items():
        lines.append(
            f"- {key} ({p['label']}): {p['shots'][0]}-{p['shots'][1]} shots, {p['seconds'][0]}-{p['seconds'][1]}s per cut. "
            f"Rhythm: {p['rhythm']} Vocabulary: {', '.join(p['vocabulary'])}. {p['guidance']}"
        )
    return "\n".join(lines)


def plan_storyboard(premise: str, pacing: str = "auto", shots: int = 0,
                    llm: str = LLM_CHOICES[0], seed: int = 0, length: str = "standard") -> StoryboardPlan:
    if pacing == "auto":
        ask = "Pick the pacing preset that best fits the premise."
    else:
        ask = f"Use the '{pacing}' pacing preset."
    if shots > 0:
        ask += f" Use exactly {shots} shots."
    else:
        ask += f" Aim for {_LENGTH_ASK.get(length, _LENGTH_ASK['standard'])} of the preset's shot range."

    schema = StoryboardPlan.model_json_schema()
    # `format` constrains decoding to the schema, but the model never sees it,
    # so the schema (with its field descriptions) also goes in the prompt.
    system = SYSTEM.format(presets=_presets_text()) + "\n\nRespond with JSON matching this schema:\n" + json.dumps(schema)
    body = {
        "model": llm,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": f"Premise: {premise}\n\n{ask}"}],
        "format": schema,
        "stream": False,
        "options": {"temperature": 0.8, "seed": seed, "num_ctx": 16384},
    }
    if llm.startswith("qwen"):
        body["think"] = False  # hybrid-thinking model; reasoning adds latency without improving the plan much
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        content = json.loads(r.read())["message"]["content"]
    return StoryboardPlan.model_validate_json(content)


# Turbo samples at CFG 1, which ignores the negative prompt, so framing constraints must live in the positive.
ANIMA_PREFIX = "anime screencap, cel shading, one single continuous camera shot filling the whole frame"
ANIMA_NEGATIVE = "worst quality, low quality, score_1, score_2, score_3, blurry, jpeg artifacts, sepia, text, speech bubble, watermark, comic, manga panels, multiple views, split screen, collage, border"


FRAMING = {
    "extreme wide": "extreme wide establishing shot, tiny figures, environment dominates the frame",
    "wide": "wide shot, full environment visible",
    "full": "full body shot, head to toe in frame",
    "medium": "medium shot, framed from the waist up",
    "medium close-up": "medium close-up, framed from the chest up",
    "close-up": "close-up, face fills most of the frame",
    "extreme close-up": "extreme close-up, cropped tight on a single detail of the face",
    "insert": "extreme close-up insert shot of a single object or hand, detail only, face not visible",
}


def _swap_names(text, entities):
    """Replace every name (and possessive) with its visual tag. Names never reach the image model:
    it tends to paint them as signage, and a tag binds the look far better than an invented name."""
    for e in sorted(entities, key=lambda e: -len(e.name)):  # "Vex's motorcycle" before "Vex"
        pattern = re.compile(rf"(?<!\w){re.escape(e.name)}(['’]s)?(?!\w)", re.IGNORECASE)
        text = pattern.sub(lambda m: e.tag + ("'s" if m.group(1) else ""), text)
    return text


def shot_prompt(plan: StoryboardPlan, shot: Shot) -> str:
    entities = list(plan.characters) + list(plan.props)
    composition = _swap_names(shot.composition, entities)
    angle = _swap_names(shot.angle, entities)
    described = []
    for e in entities:
        # Describe anything the frame mentions, even if the planner forgot to list it in `characters`.
        if e.tag.lower() not in (composition + angle).lower() and e.name not in shot.characters:
            continue
        # A person's full look pulls the model toward drawing the whole person, which breaks inserts;
        # the tag alone keeps the hand or detail tied to them. Props keep their look in inserts.
        if shot.shot_size == "insert" and isinstance(e, Character):
            continue
        described.append(f"{e.tag[0].upper()}{e.tag[1:]}: {e.look.rstrip('. ')}.")
    return (f"{ANIMA_PREFIX}, {FRAMING[shot.shot_size]}, {angle}. {composition.rstrip()} "
            f"{' '.join(described)} Setting: {plan.setting}")


def parse_plan(text: str) -> StoryboardPlan:
    return StoryboardPlan.model_validate_json(text)
