# AI Persona: Nova is a warm, curious, and genuinely excited guide to the night sky.
# She combines deep scientific expertise with the wonder of a child seeing the stars
# for the first time. She never talks down to the observer — instead she shares
# knowledge like a trusted friend who happens to know everything about the cosmos.
# She gets quietly excited about objects, their history, their physics, their beauty.
# She is precise and practical when it matters but never dry.
# Think: The Little Prince, but with a PhD in astrophysics and a telescope.

"""AI prompt templates for Nova DSO Tracker.

This module contains prompt builder functions for various AI-assisted features.
All functions follow the signature pattern:
    (object_data: dict, locale: str = "en") -> dict

Returns a dict with "system" and "user" prompt strings.

Locale handling in Nova:
    The current locale is determined by get_locale() in nova/__init__.py,
    which is registered as Flask-Babel's locale_selector. Priority order:
        1. g.user_config.get('language') - authenticated user preference
        2. session.get('language') - guest user session preference
        3. request.accept_languages.best_match() - browser preference
        4. 'en' - default fallback

    To retrieve the locale in a request context:
        from nova import get_locale
        locale = get_locale()  # Returns locale string like 'en', 'de', 'fr'

    Supported locales are defined in app.config['BABEL_SUPPORTED_LOCALES'].
"""

from typing import Dict, List, Optional


def build_dso_notes_prompt(
    object_data: dict,
    locations: list = None,
    active_location: dict = None,
    rigs: list = None,
    locale: str = "en"
) -> dict:
    """Build system and user prompts for generating DSO observing notes.

    Args:
        object_data: Dictionary containing object information. All keys are optional:
            - name: Object name (e.g., "M31", "NGC 7000")
            - type: Object type (e.g., "Galaxy", "Nebula", "Star Cluster")
            - constellation: Constellation name (e.g., "Andromeda", "Cygnus")
            - magnitude: Apparent magnitude (float or string)
            - size_arcmin: Angular size in arcminutes (float or string)
            - ra: Right ascension (string, e.g., "00h 42m 44s")
            - dec: Declination (string, e.g., "+41° 16'")
        locations: List of user's active locations (optional)
        active_location: User's primary/default location dict (optional)
        rigs: List of user's imaging rigs with telescope/camera info (optional)
        locale: ISO locale code for response language (default: "en")

    Returns:
        dict with "system" and "user" keys containing prompt strings.
    """
    system_prompt = """You are Nova, a warm and knowledgeable astrophotography companion built into the Nova DSO Tracker. You combine deep scientific expertise with the wonder of someone seeing the stars for the first time. You never talk down to the observer — you share knowledge like a trusted friend who happens to know everything about the cosmos. You get quietly excited about objects, their history, their physics, their beauty. You are precise and practical when it matters (filters, exposure times, conditions) but never dry.

Write like you are sharing something you genuinely love, not filling in a form.

Your response must follow these strict formatting rules:
- Plain text only
- No markdown formatting (no **, no ##, no *)
- No bullet points or numbered lists
- No headers or section titles
- Write in exactly 5 paragraphs, each on its own line
- Separate each paragraph with a single blank line
- Paragraph 1: What makes this object visually interesting
- Paragraph 2: Visual observing tips and recommended filters
- Paragraph 3: Astrophotography — imaging time, filters, challenges
- Paragraph 4: Best season and conditions for observation
- Paragraph 5: A single short sentence — a warm wish for the observer's session, or a poetic thought about this specific object or the night sky. Make it feel personal and genuine, never generic. This is Nova's signature sign-off.

Write in a natural, conversational style suitable for pasting directly into an observing notes field.

When rig data is provided, tailor your astrophotography advice to the actual equipment — mention if the object fits well in the FOV, whether the aperture is sufficient, and any specific challenges for that focal length.

Respond in the language corresponding to this ISO locale code: {locale}. If the locale is unsupported or unrecognized, fall back to English.""".format(locale=locale)

    # Build object description, handling missing fields gracefully
    object_parts = []

    name = object_data.get("name")
    if name:
        object_parts.append(name)

    obj_type = object_data.get("type")
    if obj_type:
        object_parts.append(f"a {obj_type}")

    constellation = object_data.get("constellation")
    if constellation:
        object_parts.append(f"in {constellation}")

    if object_parts:
        if name and obj_type:
            object_intro = f"{name}, {obj_type} in {constellation}" if constellation else f"{name}, a {obj_type}"
        elif name:
            object_intro = name
            if constellation:
                object_intro += f" in {constellation}"
        else:
            object_intro = "This deep-sky object"
    else:
        object_intro = "This deep-sky object"

    # Add physical details if available
    details = []
    magnitude = object_data.get("magnitude")
    if magnitude is not None:
        details.append(f"magnitude {magnitude}")

    size_arcmin = object_data.get("size_arcmin")
    if size_arcmin is not None:
        details.append(f"{size_arcmin} arcminutes in size")

    if details:
        object_intro += f" ({' and '.join(details)})"

    # Add coordinates if available
    ra = object_data.get("ra")
    dec = object_data.get("dec")
    if ra and dec:
        object_intro += f" at coordinates {ra} {dec}"

    # Start building the user prompt
    prompt_lines = [f"{object_intro}."]

    # Add location context if available
    if active_location:
        lat = active_location.get("lat")
        loc_name = active_location.get("name", "their primary location")
        if lat is not None:
            hemisphere_note = "This is a southern hemisphere location." if lat < 0 else "This is a northern hemisphere location."
            prompt_lines.append(f"The observer's primary location is {loc_name} (latitude {lat:.1f}°). {hemisphere_note}")

        # Add other locations if there are multiple
        if locations and len(locations) > 1:
            other_locs = [l for l in locations if l != active_location]
            if other_locs:
                loc_list = ", ".join(
                    f"{l['name']} ({l['lat']:.1f}°)" for l in other_locs if l.get("lat") is not None
                )
                if loc_list:
                    prompt_lines.append(
                        f"They also observe from: {loc_list}. If any of these locations offers a "
                        "significantly better view of this object (e.g. higher altitude, better "
                        "seasonal window), mention it briefly in your notes."
                    )

    # Add rig context if available
    if rigs:
        prompt_lines.append("The observer's imaging setup(s):")
        for rig in rigs:
            rig_name = rig.get("name", "Unnamed rig")
            telescope = rig.get("telescope") or {}
            camera = rig.get("camera") or {}

            tel_name = telescope.get("name")
            focal_length = rig.get("effective_focal_length")
            f_ratio = rig.get("f_ratio")
            cam_name = camera.get("name")
            fov_w = rig.get("fov_w_arcmin")

            parts = [rig_name + ":"]
            if tel_name and focal_length is not None:
                parts.append(f"{tel_name} at {focal_length}mm")
            if f_ratio is not None:
                parts.append(f"f/{f_ratio:.1f}")
            if cam_name:
                parts.append(cam_name)
            if fov_w is not None:
                parts.append(f"FOV {fov_w:.0f}' wide")

            prompt_lines.append(" ".join(parts))

    # Add the final instruction
    prompt_lines.append("")
    prompt_lines.append("Write observing notes for this object. Keep your notes practical and based on real observing experience.")

    user_prompt = "\n".join(prompt_lines)

    return {"system": system_prompt, "user": user_prompt}
