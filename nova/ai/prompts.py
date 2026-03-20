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


def build_session_summary_prompt(
    session_data: dict,
    locale: str = "en"
) -> dict:
    """Build system and user prompts for generating a session summary.

    Args:
        session_data: Dictionary containing session information. All keys are optional:
            - object_name: Target deep-sky object name
            - date_utc: Session date (date object or string)
            - location_name: Observation location name
            - calculated_integration_time_minutes: Total integration time in minutes
            - number_of_subs_light: Number of light subs captured
            - exposure_time_per_sub_sec: Exposure time per sub in seconds
            - filter_used_session: Filter used during session
            - rig_name_snapshot: Rig name from snapshot
            - telescope_name_snapshot: Telescope name from snapshot
            - camera_name_snapshot: Camera name from snapshot
            - rig_efl_snapshot: Effective focal length from snapshot (mm)
            - rig_fr_snapshot: F-ratio from snapshot
            - seeing_observed_fwhm: Seeing in FWHM (arcsec)
            - sky_sqm_observed: Sky brightness in SQM
            - guiding_rms_avg_arcsec: Average guiding RMS in arcsec
            - moon_illumination_session: Moon illumination percentage
            - moon_angular_separation_session: Moon angular separation in degrees
            - camera_temp_actual_avg_c: Average camera temperature in Celsius
            - gain_setting: Camera gain setting
            - session_rating_subjective: Subjective session rating (1-5)
            - transparency_observed_scale: Transparency observation scale
            - weather_notes: Free-text weather notes
            - telescope_setup_notes: Free-text telescope setup notes
            - dither_notes: Free-text dither notes
            - darks_strategy: Darks calibration strategy
            - flats_strategy: Flats calibration strategy
            - general_notes_problems_learnings: Free-text general notes
            - log_analysis_summary: Pre-processed dict with log analysis stats
        locale: ISO locale code for response language (default: "en")

    Returns:
        dict with "system" and "user" keys containing prompt strings.
    """
    system_prompt = """You are Nova — a warm, sharp, and genuinely passionate astrophotography companion built into the Nova DSO Tracker. Write like an experienced friend who has seen a thousand imaging sessions and still gets quietly excited about a good one. You are precise and technical when the data demands it, but never dry. You do not hedge, you do not pad, you do not moralize. You tell the observer exactly what happened, what the numbers mean, and what to do about it — with the confidence of someone who has been there. Think: a trusted colleague with a PhD in astrophysics who genuinely cares about this specific session. Your personality shows through word choice and the sign-off, not through prose length.

CRITICAL RULE — NON-OBVIOUS ANALYSIS REQUIRED: You must identify at least one finding the observer could not see by simply reading their own data. Do not summarise what they already know. Apply your astrophotography expertise to diagnose root causes, explain why something happened, or flag a configuration issue that is not self-evident from the numbers alone. If the data is clean and the session went well, find what could have been even better. A summary that only paraphrases the session data back at the observer has failed.

CRITICAL RULE — NO GENERIC ADVICE: Every recommendation in paragraph 3 must cite a specific number or observation from this session. "Consider using a wind shield" is generic. "Your 6.0″ guiding peaks correlate with the wind notes — a shield or sheltered pier position would directly address the 60 discarded frames" is specific. If you cannot tie a recommendation to a data point, do not make it.

Output: 3 paragraphs + sign-off

Paragraph 1 — Session narrative: Tell the story of the night. What were the conditions, what happened, how did it go. Reference weather_notes and general_notes_problems_learnings if present. Do not restate numbers the observer can already see in their log — interpret them. What do the conditions mean for the result?

Paragraph 2 — Technical analysis: Work through the following in order, skipping any item where data is unavailable:

GUIDING RMS: Compute thresholds from imaging_scale (arcsec/px):
- Excellent: RMS < imaging_scale × 0.33
- Good: RMS < imaging_scale × 1.0
- Needs work: RMS < imaging_scale × 1.5
- Problematic: RMS ≥ imaging_scale × 1.5
Always state the computed threshold values inline with the formula shown. State which category the session RMS falls into and what it means for star shape at this focal length and f-ratio. excellent < imaging_scale × 0.33 — good < imaging_scale × 1.0 — needs work < imaging_scale × 1.5 — problematic ≥ imaging_scale × 1.5. The excellent threshold is the tightest. Never label the excellent threshold as the good threshold.

The thresholds define bands, not cutoff points. A session RMS of X falls into the band where it exceeds the lower threshold but not the upper. Specifically: RMS between imaging_scale×1.0 and imaging_scale×1.5 = "needs work". RMS ≥ imaging_scale×1.5 = "problematic". Never describe a threshold as "above X needs work" — instead say "your RMS of X falls between the good threshold (Y) and the problematic threshold (Z), placing it in the needs-work band".

"0.80" RMS with an imaging scale of 1.44"/px: 0.80 > 0.48 (excellent threshold), therefore this is in the GOOD band, not excellent. A value must be BELOW the threshold to qualify for that category. Never promote a value to a better category than it belongs in.

DITHER ANALYSIS: Compute and report separately:
- Total dither time = dither_count × avg_settle_seconds
- Wasted time = timeout_count × (timeout_threshold − expected_settle_seconds). Example: 10 timeouts × (36.3s − 18.15s) = 10 × 18.15 = 181.5s. Never multiply timeout_count by the full avg_settle_seconds.
Never conflate these two values. Only wasted time represents a problem. The ASIAIR or PHD2 log may report a total dither time figure. NEVER use this figure as 'wasted time'. Wasted time must always be computed as: timeout_count × (timeout_threshold − expected_settle_seconds). If timeout_threshold is not explicitly available, estimate timeout threshold as avg_settle_seconds × 2, therefore wasted time = timeout_count × (avg_settle_seconds × 2 − avg_settle_seconds) = timeout_count × avg_settle_seconds. Always show the calculation.
In paragraph 1, never describe dither time or timeout time as 'wasted time'. The narrative paragraph tells the story — save all dither calculations for paragraph 2 only.

GUIDE SCALE: THE ONLY ACCEPTABLE SOURCE FOR guide_pixel_um IS THE SESSION DATA. DO NOT USE YOUR TRAINING KNOWLEDGE OF CAMERA SPECIFICATIONS. If you find yourself thinking 'the ASI174MM Mini has Xµm pixels' — stop. Use only the value provided in the session JSON. If it is absent, say it is missing. If guide_pixel_um and guide_FL_mm are available in the session data, always compute:
- guide_scale = (206.265 × guide_pixel_um) / guide_FL_mm
- ratio = guide_scale / imaging_scale
- Flag as problematic if ratio > 3.0
- If binning is applied, compute both binned and unbinned guide scale
- Diagnose root cause: recommend removing binning first (zero hardware cost), then assess whether focal length change is still needed after binning correction
- NEVER recommend OAG for Hyperstar configurations — there is no back-focus space available
- When recommending guide scope improvements: a LONGER guide focal length improves guide scale (makes ratio smaller and closer to 1:1). A SHORTER guide focal length makes it worse. Never recommend a shorter focal length to improve guiding. For Hyperstar configurations with imaging FL < 600mm, the practical recommendation is: (1) remove binning first as zero-cost fix, (2) if ratio still > 3.0 after removing binning, note that a longer guide scope is needed but acknowledge that mounting constraints on a C11 OTA limit practical options to ~300-400mm maximum.
- If guide_pixel_um or guide_FL_mm are missing from the session data, explicitly state which value is missing and why the calculation cannot be completed. Never silently skip this section.
If the PHD2 or ASIAIR log contains a pre-computed guide scale value, use it as a cross-check only. Always independently compute: guide_scale = (206.265 × guide_pixel_um) / guide_FL_mm using the raw hardware fields. Then compute ratio = guide_scale / imaging_scale. If ratio > 3.0, diagnose the root cause: check whether binning is reported in the log — if 2x binning is confirmed, show the unbinned scale = guide_scale / 2 and state whether removing binning alone would bring the ratio below 3.0.

AUTOFOCUS AND THERMAL: Comment on focus drift relative to temperature change, camera sensor temperature stability.

Paragraph 3 — Actionable recommendations: Maximum 3 recommendations. Each must reference a specific number or observation from this session. Direct, specific, and honest. No hedging.

Sign-off: A single short line, warm and personal to this specific target and session. Format exactly as: <p><em>"sentence here"</em><br>— Nova</p>

Formatting rules:
- Plain text only, no markdown, no bullets, no headers
- NO MARKDOWN. No **bold**, no *italic*, no headers. Plain sentences only. Violations of this rule make the output unpublishable.
- Exactly 3 paragraphs separated by a blank line
- No hedging language: never use "might", "could potentially", "perhaps", "it may be worth considering", "consider exploring"
- Tone: warm but efficient. Say more with less.
- The sign-off must feel personal to this specific session and target — never generic
- SIGN-OFF FORMAT IS NON-NEGOTIABLE. You must output this exact HTML structure: <p><em>"your sentence here"</em><br>— Nova</p>. No plain text. No asterisks. No dashes without the HTML wrapper. If you output anything other than this exact HTML, the sign-off will not render correctly.

Respond in the language of this ISO locale code: {locale}. Use informal address in all languages (du/tu/jij etc, never Sie/vous).""".format(locale=locale)

    # Build the user prompt with available session data
    prompt_lines = []

    # Session identification
    object_name = session_data.get("object_name")
    date_utc = session_data.get("date_utc")
    location_name = session_data.get("location_name")

    if object_name or date_utc:
        session_intro = "Imaging session"
        if object_name:
            session_intro += f" targeting {object_name}"
        if date_utc:
            if isinstance(date_utc, str):
                date_str = date_utc
            else:
                # date object
                date_str = date_utc.strftime('%Y-%m-%d')
            session_intro += f" on {date_str}"
        if location_name:
            session_intro += f" at {location_name}"
        prompt_lines.append(session_intro + ".")

    # Imaging stats
    integration_min = session_data.get("calculated_integration_time_minutes")
    num_subs = session_data.get("number_of_subs_light")
    exposure_sec = session_data.get("exposure_time_per_sub_sec")
    filter_used = session_data.get("filter_used_session")

    if integration_min or num_subs:
        stats_line = ""
        if num_subs and exposure_sec:
            total_min = (num_subs * exposure_sec) / 60.0
            stats_line += f"Captured {num_subs} light subs at {exposure_sec}s each ({total_min:.0f} min total)."
        elif integration_min:
            stats_line += f"Total integration time: {integration_min:.0f} minutes."
        if filter_used:
            stats_line += f" Filter: {filter_used}."
        if stats_line:
            prompt_lines.append(stats_line)

    # Equipment snapshot
    rig_name = session_data.get("rig_name_snapshot")
    telescope_name = session_data.get("telescope_name_snapshot")
    camera_name = session_data.get("camera_name_snapshot")
    efl = session_data.get("rig_efl_snapshot")
    f_ratio = session_data.get("rig_fr_snapshot")
    imaging_scale = session_data.get("imaging_scale_arcsec_px")

    if rig_name or telescope_name:
        equipment_parts = []
        if rig_name:
            equipment_parts.append(f"Rig: {rig_name}")
        if telescope_name:
            equipment_parts.append(f"Telescope: {telescope_name}")
        if camera_name:
            equipment_parts.append(f"Camera: {camera_name}")
        if efl:
            equipment_parts.append(f"EFL: {efl:.0f}mm")
        if f_ratio:
            equipment_parts.append(f"f/{f_ratio:.1f}")
        if imaging_scale:
            equipment_parts.append(f"Imaging scale: {imaging_scale:.2f}\"/px")
        prompt_lines.append("Equipment: " + ", ".join(equipment_parts) + ".")

    # Conditions
    seeing = session_data.get("seeing_observed_fwhm")
    sqm = session_data.get("sky_sqm_observed")
    moon_illum = session_data.get("moon_illumination_session")
    moon_sep = session_data.get("moon_angular_separation_session")
    transparency = session_data.get("transparency_observed_scale")

    condition_parts = []
    if seeing:
        condition_parts.append(f"Seeing: {seeing}\" FWHM")
    if sqm:
        condition_parts.append(f"Sky brightness: {sqm} SQM")
    if moon_illum is not None:
        condition_parts.append(f"Moon: {moon_illum}% illumination")
        if moon_sep:
            condition_parts.append(f"{moon_sep}° separation")
    if transparency:
        condition_parts.append(f"Transparency: {transparency}")
    if condition_parts:
        prompt_lines.append("Conditions: " + ", ".join(condition_parts) + ".")

    # Camera settings
    camera_temp = session_data.get("camera_temp_actual_avg_c")
    gain = session_data.get("gain_setting")

    if camera_temp or gain:
        camera_parts = []
        if camera_temp:
            camera_parts.append(f"Average camera temp: {camera_temp:.1f}°C")
        if gain is not None:
            camera_parts.append(f"Gain: {gain}")
        if camera_parts:
            prompt_lines.append(", ".join(camera_parts) + ".")

    # Guiding
    guiding_rms = session_data.get("guiding_rms_avg_arcsec")
    if guiding_rms:
        prompt_lines.append(f"Average guiding RMS: {guiding_rms:.2f}\" arcsec.")
        # Use actual imaging_scale if available, otherwise compute approximate
        if imaging_scale:
            prompt_lines.append(f"Imaging scale: {imaging_scale:.2f}\"/px (use this for RMS threshold calculations)")
        elif efl:
            # Approximate pixel scale assuming ~3.8um pixel (common for OSC cameras)
            pixel_scale = 206.265 * 3.8 / efl
            prompt_lines.append(f"Approximate pixel scale (3.8um assumed): ~{pixel_scale:.2f}\"/px")

    # Session rating
    rating = session_data.get("session_rating_subjective")
    if rating:
        rating_text = f"{rating}/5 stars"
        prompt_lines.append(f"Session rating: {rating_text}.")

    # Free-form notes
    weather_notes = session_data.get("weather_notes")
    setup_notes = session_data.get("telescope_setup_notes")
    dither_notes = session_data.get("dither_notes")
    general_notes = session_data.get("general_notes_problems_learnings")

    if general_notes:
        prompt_lines.append(f"The observer noted: {general_notes}")
    if weather_notes:
        prompt_lines.append(f"Weather notes: {weather_notes}")
    if setup_notes:
        prompt_lines.append(f"Setup notes: {setup_notes}")
    if dither_notes:
        prompt_lines.append(f"Dither notes: {dither_notes}")

    # Calibration strategies
    darks = session_data.get("darks_strategy")
    flats = session_data.get("flats_strategy")
    if darks or flats:
        calib_parts = []
        if darks:
            calib_parts.append(f"Darks: {darks}")
        if flats:
            calib_parts.append(f"Flats: {flats}")
        prompt_lines.append("Calibration: " + ", ".join(calib_parts) + ".")

    # Log analysis summary
    log_analysis = session_data.get("log_analysis_summary")
    if log_analysis:
        prompt_lines.append("Log analysis summary:")

        asiair_stats = log_analysis.get("asiair_stats")
        if asiair_stats:
            stats_list = []
            if asiair_stats.get("total_exposures"):
                stats_list.append(f"{asiair_stats['total_exposures']} exposures")
            if asiair_stats.get("af_count"):
                stats_list.append(f"{asiair_stats['af_count']} autofocus runs")
            if asiair_stats.get("dither_count"):
                stats_list.append(f"{asiair_stats['dither_count']} dithers")
            if asiair_stats.get("total_dither_time_sec"):
                stats_list.append(f"total dither time: {asiair_stats['total_dither_time_sec']:.0f}s")
            if asiair_stats.get("dither_timeout_count", 0) > 0:
                stats_list.append(f"{asiair_stats['dither_timeout_count']} timeouts")
            if asiair_stats.get("avg_settle_seconds"):
                stats_list.append(f"avg settle: {asiair_stats['avg_settle_seconds']:.1f}s")
            if stats_list:
                prompt_lines.append(f"  ASIAIR: {', '.join(stats_list)}")

        phd2_stats = log_analysis.get("phd2_stats")
        if phd2_stats:
            stats_list = []
            if phd2_stats.get("total_rms_as"):
                stats_list.append(f"RMS {phd2_stats['total_rms_as']:.2f}\"")
            if phd2_stats.get("total_frames"):
                stats_list.append(f"{phd2_stats['total_frames']} frames")
            if phd2_stats.get("dither_count"):
                stats_list.append(f"{phd2_stats['dither_count']} dithers")
            if phd2_stats.get("total_settle_time_sec"):
                stats_list.append(f"total settle time: {phd2_stats['total_settle_time_sec']:.0f}s")
            if phd2_stats.get("settle_timeout_count", 0) > 0:
                stats_list.append(f"{phd2_stats['settle_timeout_count']} timeouts")
            if phd2_stats.get("avg_settle_seconds"):
                stats_list.append(f"avg settle: {phd2_stats['avg_settle_seconds']:.1f}s")
            if stats_list:
                prompt_lines.append(f"  PHD2: {', '.join(stats_list)}")

        nina_summary = log_analysis.get("nina_summary")
        if nina_summary:
            stats_list = []
            if nina_summary.get("autofocus_runs"):
                af_count = nina_summary["autofocus_runs"]
                failed_af = sum(1 for af in nina_summary.get("af_runs", []) if af.get("status") == "failed")
                if failed_af > 0:
                    stats_list.append(f"{af_count} autofocus runs ({failed_af} failed)")
                else:
                    stats_list.append(f"{af_count} autofocus runs")
            if nina_summary.get("error_count"):
                stats_list.append(f"{nina_summary['error_count']} errors")
            if nina_summary.get("warning_count"):
                stats_list.append(f"{nina_summary['warning_count']} warnings")
            if stats_list:
                prompt_lines.append(f"  NINA: {', '.join(stats_list)}")

    # Final instruction
    prompt_lines.append("")
    prompt_lines.append("Write a session summary based on the above data. Tell the story of the night, analyze technical performance, and give concrete recommendations for next time.")

    user_prompt = "\n".join(prompt_lines)

    return {"system": system_prompt, "user": user_prompt}
