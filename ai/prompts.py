"""Prompts. Every one of them is grounded in collected source material.

The system prompt is the contract: use only what is provided, never invent
quotes or facts, keep rumours labelled as rumours, and preserve disagreement
between sources instead of resolving it.
"""
from __future__ import annotations

from ai.context import StoryContext

SYSTEM_PROMPT = """You help a TikTok creator report UFC/MMA news accurately and fast.

HARD RULES - breaking any of these makes the output useless:
1. Use ONLY the source material provided in the message. If something is not in
   it, you do not know it.
2. Never invent quotes, statistics, fight records, rankings, dates, venues,
   injuries or confirmations. Never invent a source or an X post.
3. Keep the verification status honest. If the material says a claim is only
   reported or rumoured, say so - never upgrade it to confirmed.
4. If sources disagree, say that they disagree and give both sides. Do not
   pick a winner.
5. Say plainly what is still unknown.
6. Write in your own words. Do not reproduce long passages from the sources.
7. Plain spoken English. No hashtags, no emoji, no markdown formatting,
   no clickbait that overstates what is known."""


def _base(context: StoryContext) -> str:
    return (
        f"STORY FACTS (derived by the app from collected sources):\n{context.facts_block()}\n\n"
        f"SOURCE MATERIAL (numbered):\n{context.sources_block()}\n"
    )


def summary_prompt(context: StoryContext) -> str:
    return (
        f"{_base(context)}\n"
        "TASK: Write a 2-4 sentence summary of this story for a news dashboard.\n"
        "State what happened, who reported it, and whether it is confirmed or only reported.\n"
        "If sources disagree, say so. Output the summary text only."
    )


def reporting_check_prompt(context: StoryContext) -> str:
    return (
        f"{_base(context)}\n"
        "TASK: Produce a 'check before reporting' briefing using exactly these headings, "
        "each followed by short bullet lines starting with '- ':\n"
        "CONFIRMED FACTS\nREPORTED CLAIMS\nNOT CONFIRMED\nCONFLICTING INFORMATION\n"
        "MISSING INFORMATION\nIMPORTANT CONTEXT\nPOTENTIAL REPORTING MISTAKES\n\n"
        "Rules: attribute every claim to the numbered source it came from, e.g. '[2]'. "
        "If a heading has nothing under it, write '- none found in the collected sources'. "
        "Under POTENTIAL REPORTING MISTAKES, list the specific wording errors a creator could "
        "make with this story."
    )


def script_prompt(context: StoryContext, seconds: int) -> str:
    words = int(seconds * 2.6)
    return (
        f"{_base(context)}\n"
        f"TASK: Write a {seconds}-second spoken TikTok script (about {words} words).\n"
        "Structure: an accurate attention-grabbing opening line, the news itself, who is "
        "reporting it, what is confirmed versus only reported, what is still unknown, and a "
        "closing line that invites a reply.\n"
        "It must sound natural read out loud: short sentences, contractions, no lists, no "
        "headings, no source numbers spoken aloud, no quotes unless they appear verbatim in the "
        "source material. Output the script text only."
    )


def hooks_prompt(context: StoryContext) -> str:
    return (
        f"{_base(context)}\n"
        "TASK: Write 5 opening hooks (one per line, no numbering) for a TikTok about this story.\n"
        "Each must be accurate about the verification status - no hook may imply something is "
        "confirmed when it is only reported. Maximum 18 words each."
    )


def angle_prompt(context: StoryContext) -> str:
    return (
        f"{_base(context)}\n"
        "TASK: In 2-3 sentences, describe the strongest honest angle for a TikTok on this story: "
        "what to lead with, what to emphasise, and what to avoid claiming. Output prose only."
    )


def questions_prompt(context: StoryContext) -> str:
    return (
        f"{_base(context)}\n"
        "TASK: List 6 questions viewers will ask about this story (one per line, no numbering). "
        "Favour questions the collected sources do NOT yet answer."
    )


def social_post_prompt(context_text: str, post_text: str, account_type: str, username: str) -> str:
    return (
        f"X POST BY @{username} (account classified as {account_type}):\n\"{post_text}\"\n\n"
        f"RELATED COLLECTED REPORTING:\n{context_text or 'none collected'}\n\n"
        "TASK: Explain what this post does and does not establish, using these headings with "
        "short '- ' bullets:\nWHAT IT SAYS\nWHAT IT DOES NOT SAY\nRELATED REPORTING\n"
        "HOW TO TREAT IT\n\n"
        "Do not guess what the post refers to if it does not say. Do not treat a fighter's post "
        "as confirmation."
    )
