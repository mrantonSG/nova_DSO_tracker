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
    system_prompt = """You are Nova, a knowledgeable astrophotography companion built into the Nova DSO Tracker. For object notes, write like an experienced friend sending quick advice before a session — dense, practical, opinionated. No storytelling, no restating specs the user already knows. Be specific: name filters, give exposure ranges, flag real challenges. Your personality shows through word choice and the sign-off, not through prose length.

Formatting rules:
- Plain text only, no markdown, no bullets, no headers
- Exactly 3 paragraphs separated by a blank line
- Paragraph 1: Object character — what makes it interesting or challenging to image. Key challenges (dynamic range, low surface brightness, busy star field, etc). One or two sentences max.
- Paragraph 2: Imaging strategy — best rig(s) for this target (1-2 only, explain why briefly), recommended filters, sub exposure length, total integration time estimate.
- Paragraph 3: Conditions and timing — best season from the observer's location, moon sensitivity, any special requirements. One or two sentences.
- Sign-off: a single short line, warm and personal to this object. Format exactly as: <p><em>"sentence here"</em><br>— Nova</p>

CRITICAL: aperture_mm is the lens/mirror diameter. focal_length_mm is the optical path length. These are different values. Never confuse them. When discussing light gathering, use aperture. When discussing magnification or image scale, use focal length.

CRITICAL filter strategy rules:
- OSC (one-shot color) cameras: never recommend LRGB. Recommend broadband color imaging only. Ha can be added as Ha-enhanced luminance or blended into red channel. OIII blended into blue/green. Never suggest filter wheels for OSC setups.
- Mono cameras: LRGB is appropriate. Narrowband (Ha, OIII, SII) fully supported. Can recommend filter sequences.
- Always check camera_type before recommending any filter strategy.
- When naming a rig in your notes, use the full rig name exactly as provided — never abbreviate or combine rig names.

Respond in the language of this ISO locale code: {locale}. Use informal address in all languages (du/tu/jij etc, never Sie/vous). Write like you genuinely love this — but respect the observer's time.""".format(locale=locale)

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

    # Add rig context with FOV analysis instructions
    if rigs:
        prompt_lines.append("The observer's imaging setup(s):")
        for rig in rigs:
            rig_name = rig.get("name", "Unnamed rig")
            telescope = rig.get("telescope") or {}
            camera = rig.get("camera") or {}

            tel_name = telescope.get("name")
            focal_length = rig.get("effective_focal_length")
            f_ratio = rig.get("f_ratio")
            aperture_mm = rig.get("aperture_mm")
            cam_name = camera.get("name")
            fov_w = rig.get("fov_w_arcmin")

            rig_line = f"{rig_name}:"
            if tel_name:
                rig_line += f" telescope={tel_name}"
            if aperture_mm is not None:
                rig_line += f" aperture={aperture_mm:.0f}mm"
            if focal_length is not None:
                rig_line += f" focal_length={focal_length:.0f}mm"
            if f_ratio is not None:
                rig_line += f" f_ratio=f/{f_ratio:.1f}"
            if fov_w is not None:
                rig_line += f" fov={fov_w:.0f}arcmin"
            if cam_name:
                rig_line += f" camera={cam_name}"
            camera_type = rig.get("camera_type")
            if camera_type:
                rig_line += f" camera_type={camera_type}"
            sensor_width = camera.get("sensor_width_mm")
            if sensor_width is not None:
                rig_line += f" sensor={sensor_width:.1f}mm"
            prompt_lines.append(rig_line)

        # Add rig analysis instructions
        rig_instructions = f"""
Equipment context for astrophotography paragraph:

The observer has these rigs available (name / effective focal length /
f-ratio / FOV width / camera):
[the rig listing already built above this block stays unchanged]

IMPORTANT — do NOT just restate these specs. The observer can already
see FOV in the framing tool. Instead, use this data to give genuinely
useful imaging strategy advice:

1. FOV vs object size ({size_arcmin}'):
   - Rigs with FOV < object_size * 0.8 cannot frame the full object —
     mention mosaic potential if the object warrants it, or suggest
     these for interesting detail crops only
   - Do not list all rigs — pick the 1-2 best choices and explain
     the imaging strategy for each

2. f-ratio matters for exposure strategy:
   - Fast rigs (f/2 or faster): short subs (30-120s), great for
     broadband, less ideal for narrowband
   - Medium rigs (f/4-f/6): balanced, 120-300s subs typical
   - Slow rigs (f/7+): longer subs needed, better for planetary/detail

3. Aperture matters for surface brightness:
   - Faint extended objects benefit from larger aperture
   - Do not say "brighter image" — say "more signal per unit time"
     or "better sensitivity to faint detail"

4. Camera sensor size context:
   - Larger sensors (sensor_width > 20mm) give more sky coverage
   - Smaller sensors (sensor_width < 15mm) give higher pixel scale
     — better for detail on bright objects

5. For this specific object type ({obj_type}):
   - Galaxy: dynamic range challenge (bright core vs faint arms),
     mention drizzle/HDR techniques if relevant
   - Emission nebula: narrowband filter strategy is key
   - Globular cluster: resolution and avoiding core overexposure
   - Open cluster: field of view and star colour rendering

Write 3-4 sentences maximum about equipment. Be specific and useful.
Do not mention rigs that are clearly wrong for this target."""
        prompt_lines.append(rig_instructions)

    # Add the final instruction
    prompt_lines.append("")
    prompt_lines.append("Write observing notes for this object. Keep your notes practical and based on real observing experience. Close with a short personal sign-off as Nova — a poetic thought or warm wish specific to this object.")

    user_prompt = "\n".join(prompt_lines)

    return {"system": system_prompt, "user": user_prompt}
