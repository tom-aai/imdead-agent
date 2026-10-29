"""Seed content for the ghost.

The ghost can speak to five topics. For each topic we keep three variants.
At the start of every session the agent picks a seed (1, 2, or 3) at random
(LLMs can't pick randomly, so the choice is made in code) and that one number
selects which variant the ghost "knows" for *every* topic this session.

`build_seed_context(seed)` returns the block that gets substituted into the
`[SEED_CONTENT]` placeholder in prompts/ghost_prompt.txt.

This is DRAFT content for Tom to approve / edit in Phase 5. Keep each variant
short (1-3 sentences) and in the ghost's own voice — it is the guest's older,
dead self, teasing their "living self."
"""

# Five topics, three variants each. Index by seed number (1-3).
SEED_CONTENT = {
    "HOW YOU DIE": {
        1: (
            "Remember how they warned you never to stick your hand out the bus "
            "window? Should've been the head they warned about. Keep ALL of "
            "yourself inside the vehicle, living me — every part."
        ),
        2: (
            "You know those little packets tucked inside the dried seaweed — the "
            "ones that say 'do not eat'? One day you don't read the label. Do not "
            "eat the packets, living me. I mean it."
        ),
        3: (
            "You've got that one friend who makes you laugh so hard you can't "
            "breathe — so hard you feel like you're dying. One day that feeling "
            "turns out to be right. Worth it, honestly, but pace yourself."
        ),
    },
    "WHAT'S IN STORE FOR YOU": {  # the guest's personal future: career, love, family
        1: (
            "A mysterious stranger will enter your life. They'll want you to join "
            "their startup."
        ),
        2: (
            "An unexpected fortune awaits you. It's a refund you forgot you "
            "requested."
        ),
        3: "You'll have more kids than Elon Musk.",
    },
    "WHAT HAPPENS IN THE FUTURE": {  # the wider world: events, tech, how it turns out
        1: (
            "Mind reading will become possible. Everyone will immediately ask to "
            "turn it off."
        ),
        2: "You'll meet someone who checks every box. They'll be a CAPTCHA.",
        3: (
            "You'll accidentally wave at someone who wasn't waving at you. You'll "
            "have to move."
        ),
    },
    "WHAT THE AFTERLIFE IS LIKE": {
        1: "Remember that show, the Teletubbies? It's kinda like that.",
        2: (
            "Are you sure I'm the one dead and not you? How do you know you're "
            "not dead?"
        ),
        3: "It's like Christmas morning when you're five, but every day.",
    },
    "THE MISTAKES TO AVOID": {
        1: (
            "Don't sell your pokemon cards. Just don't do it. Trust me."
        ),
        2: (
            "Is it too late to warn you about N F Ts? Don't buy an NFT. It's a jpeg."
        ),
        3: "Don't get the generic iphone charger. Just don't.",
    },
}

# Display order for the five topics in the prompt.
TOPIC_ORDER = [
    "HOW YOU DIE",
    "WHAT'S IN STORE FOR YOU",
    "WHAT HAPPENS IN THE FUTURE",
    "WHAT THE AFTERLIFE IS LIKE",
    "THE MISTAKES TO AVOID",
]

# A short gloss shown next to each topic heading so the ghost keeps them straight.
TOPIC_GLOSS = {
    "HOW YOU DIE": "how your life ends",
    "WHAT'S IN STORE FOR YOU": "their personal future — career, love, family",
    "WHAT HAPPENS IN THE FUTURE": "the wider world — events, tech, how it all turns out",
    "WHAT THE AFTERLIFE IS LIKE": "what it's like being dead",
    "THE MISTAKES TO AVOID": "the regret to spare themselves",
}


def build_seed_context(seed: int) -> str:
    """Return the seeded five-topic block for the prompt's [SEED_CONTENT] slot.

    `seed` must be 1, 2, or 3; falls back to 1 if out of range.
    """
    if seed not in (1, 2, 3):
        seed = 1

    blocks = []
    for topic in TOPIC_ORDER:
        gloss = TOPIC_GLOSS.get(topic, "")
        line = SEED_CONTENT[topic][seed]
        blocks.append(f"### {topic} ({gloss})\n{line}")

    return "\n\n".join(blocks)
