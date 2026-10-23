"""Shared mapping between OpenPets reactions, screen states and spritesheet rows."""

# OpenPets reaction -> screen state (mirrors OpenPets' reaction-animation-mapping,
# but keeps "waiting" (needs the user) apart from "testing").
REACTION_TO_STATE = {
    "idle": "idle",
    "waving": "idle",
    "thinking": "thinking",
    "working": "working",
    "editing": "working",
    "running": "working",
    "testing": "testing",
    "waiting": "waiting",
    "success": "done",
    "celebrating": "done",
    "error": "error",
}

# Screen state -> (spritesheet row, frame count, loop duration ms, speech bubble text, accent color)
# Rows/frames/durations come from OpenPets' V1 spritesheet layout (192x208 cells, 8 columns).
# A trailing "..." in the text is animated as typing dots.
STATES = {
    "idle": (0, 6, 5500, "Zzz...", (150, 150, 150)),
    "thinking": (8, 6, 1030, "Let me think...", (90, 170, 255)),
    "working": (7, 6, 820, "On it!", (255, 160, 40)),
    "testing": (6, 6, 1200, "Running tests...", (190, 120, 255)),
    "waiting": (3, 4, 1000, "I need you!", (255, 70, 70)),
    "done": (4, 5, 840, "All done!", (80, 220, 100)),
    "error": (5, 8, 1220, "Oops, it failed", (255, 70, 70)),
}


def device_path(state: str) -> str:
    """Path of the state's GIF on the SmallTV filesystem."""
    return f"/image/op-{state}.gif"
