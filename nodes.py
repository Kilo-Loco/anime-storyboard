import numpy as np
import torch
from PIL import Image

from .planner import ANIMA_NEGATIVE, LENGTH_CHOICES, LLM_CHOICES, PACING_CHOICES, parse_plan, plan_storyboard, shot_prompt
from .sheet import render_pages


def _to_pil(t):
    return Image.fromarray(np.clip(t.cpu().numpy() * 255.0, 0, 255).astype(np.uint8))


def _to_tensor(img):
    return torch.from_numpy(np.asarray(img.convert("RGB")).astype(np.float32) / 255.0)


class StoryboardPlanner:
    """One-line premise -> shot list (local open-weight LLM via Ollama). Emits one prompt per shot as a
    list, so downstream CLIPTextEncode / KSampler run once per cut."""

    CATEGORY = "anime storyboard"
    FUNCTION = "plan"
    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("plan_json", "panel_prompts", "negative", "cut_count")
    OUTPUT_IS_LIST = (False, True, False, False)
    # An output node so it can run on its own: the demo page queues a planner-only prompt,
    # reads the plan from the UI output, then queues one small render prompt per cut.
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "premise": ("STRING", {"multiline": True, "default": "two rivals meet again at a train station in the rain"}),
                "pacing": (PACING_CHOICES, {"default": "auto"}),
                "length": (LENGTH_CHOICES, {"default": "standard", "tooltip": "Where in the pacing preset's cut range to land"}),
                "shots": ("INT", {"default": 0, "min": 0, "max": 24, "tooltip": "Exact cut count; 0 = use length + pacing preset"}),
                "llm": (LLM_CHOICES, {"default": LLM_CHOICES[0], "tooltip": "Open-weight planner model served by Ollama on the pod"}),
                "reroll": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF, "control_after_generate": "fixed",
                                   "tooltip": "LLM sampling seed; change it for a fresh plan"}),
            },
            "optional": {
                "plan_override": ("STRING", {"multiline": True, "default": "", "tooltip": "Paste a plan_json here to skip the LLM"}),
            },
        }

    def plan(self, premise, pacing, length, shots, llm, reroll, plan_override=""):
        if plan_override.strip():
            plan = parse_plan(plan_override)
        else:
            plan = plan_storyboard(premise, pacing, shots, llm, reroll, length)
        plan_json = plan.model_dump_json(indent=1)
        prompts = [shot_prompt(plan, s) for s in plan.shots]
        return {"ui": {"plan": [plan_json], "prompts": prompts, "negative": [ANIMA_NEGATIVE]},
                "result": (plan_json, prompts, ANIMA_NEGATIVE, len(plan.shots))}


class StoryboardSheet:
    """Lays the rendered panels out as e-konte pages with cut numbers, camera notes, dialogue and timing."""

    CATEGORY = "anime storyboard"
    FUNCTION = "render"
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("pages",)
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "panels": ("IMAGE",),
                "plan_json": ("STRING", {"forceInput": True}),
                "cuts_per_page": ("INT", {"default": 5, "min": 3, "max": 6}),
            }
        }

    def render(self, panels, plan_json, cuts_per_page):
        plan = parse_plan(plan_json[0])
        pil_panels = [_to_pil(img) for batch in panels for img in batch]
        pages = render_pages(plan, pil_panels, cuts_per_page[0])
        return (torch.stack([_to_tensor(p) for p in pages]),)


NODE_CLASS_MAPPINGS = {
    "AnimeStoryboardPlanner": StoryboardPlanner,
    "AnimeStoryboardSheet": StoryboardSheet,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "AnimeStoryboardPlanner": "Storyboard Planner (Ollama)",
    "AnimeStoryboardSheet": "E-Konte Storyboard Sheet",
}
