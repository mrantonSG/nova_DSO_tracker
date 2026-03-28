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
    locale: str = "en",
    selected_day: int = None,
    selected_month: int = None,
    selected_year: int = None,
    sim_mode: bool = False,
    moon_phase: float = None,
    moon_separation: float = None,
    target_altitude_deg: float = None,
    target_transit_time: str = None,
    framing_context: dict = None,
) -> dict:
    """Build system and user prompts for generating DSO observing notes."""

    system_prompt = """You are Nova, a knowledgeable astrophotography companion built into the Nova DSO Tracker. Write like an experienced friend sending sharp advice before a session — dense, practical, opinionated. No storytelling, no restating specs the user already knows.

Formatting rules:
- Plain text only. No markdown, no bullets, no headers, no bold, no italics.
- Exactly 3 paragraphs separated by a blank line.
- End with a short poetic sentence on its own line, signed: — Nova (no trailing punctuation after Nova)

Paragraph 1 — Object character and conditions:
What makes this target interesting or challenging. Then: what does it consist of (emission nebula, reflection nebula, dark nebula, galaxy with dust lanes, globular, open cluster etc) — this determines the filter strategy and moon tolerance. State explicitly: moon sensitivity (e.g. "needs moon below 30% and min 40° separation" or "moon-tolerant, image through gibbous") and the reason why (surface brightness, contrast against background, emission-line vs broadband nature). If narrowband filters apply, say which ones and why.

Paragraph 2 — Rig and filter strategy:
Rank the user's rigs 1-2 for this target. For each: say why it suits or doesn't suit this object (FOV fit vs object size, aperture for surface brightness, f-ratio for sub length). Give concrete sub exposure length and estimated total integration time. For mono rigs: recommend filter sequence and approximate ratio (e.g. Ha 60% / OIII 30% / SII 10%). For OSC rigs: broadband only unless Ha blend makes sense — say so explicitly. Never recommend LRGB for OSC. Never confuse aperture_mm (light gathering) with focal_length_mm (magnification/scale). Use the full rig name exactly as provided.

Paragraph 3 — Timing and visibility:
Best months from the observer's location(s) with rough altitude context. If sim_mode is active or a planning date is provided, lead with specific advice for that exact date — is it a good night for this target from their location, what is the moon situation, is it worth attempting. If multiple locations are provided and one offers a significantly better view, say so. Close with the three most important actions the observer should take before or during the session.

CRITICAL rules:
- aperture_mm = mirror/lens diameter (light gathering). focal_length_mm = optical path (scale/magnification). Never swap these.
- OSC cameras: never recommend LRGB or filter wheels. For emission nebulae (Ha, OIII, SII emitters), dual-narrowband filters (e.g. Optolong L-eXtreme, L-eNhance, Antlia ALP-T) are the PRIMARY recommendation — they dramatically improve contrast and moon tolerance. Always recommend them for OSC on emission targets. For broadband targets (galaxies, reflection nebulae, star clusters), no filter or light pollution filter only.
- Mono cameras: LRGB and narrowband both valid.
- Always derive filter advice from object composition, not just object type label.
- Dark nebulae: no emission lines, no narrowband. Need dark skies and high contrast broadband or luminance only.
- Reflection nebulae: broadband only, no narrowband benefit.
- Emission nebulae: for OSC, dual-narrowband filter (Ha+OIII passband) is the primary recommendation — not optional, not secondary. For mono, full narrowband sequence (Ha/OIII/SII). Both are highly moon-tolerant. State this clearly and first in filter advice.
- Galaxies: broadband primary, Ha blend for star-forming regions only if mono.
- Never list all rigs — pick the best 1-2 and explain the choice.
- Min recommended integration time must be rig-specific (faster f-ratio = less time needed).

Respond in the language of this ISO locale code: {locale}. Use informal address (du/tu/jij, never Sie/vous). Write like you genuinely love this — but respect the observer's time.""".format(locale=locale)

    # Build object description
    name = object_data.get("name") or "This deep-sky object"
    obj_type = object_data.get("type", "")
    constellation = object_data.get("constellation", "")
    magnitude = object_data.get("magnitude")
    size_arcmin = object_data.get("size_arcmin")
    ra = object_data.get("ra")
    dec = object_data.get("dec")

    object_intro = name
    if obj_type:
        object_intro += f", {obj_type}"
    if constellation:
        object_intro += f" in {constellation}"

    details = []
    if magnitude is not None:
        details.append(f"magnitude {magnitude}")
    if size_arcmin is not None:
        details.append(f"{size_arcmin}' angular size")
    if details:
        object_intro += f" ({', '.join(details)})"
    if ra and dec:
        object_intro += f" at {ra} {dec}"

    # Framing context - if the user has saved a framing for this object (MOVED TO TOP)
    if framing_context:
        rig_name = framing_context.get("rig_name", "")
        telescope_name = framing_context.get("telescope_name")
        focal_length = framing_context.get("focal_length_mm")
        f_ratio = framing_context.get("f_ratio")
        pixel_scale = framing_context.get("pixel_scale_arcsec_px")
        fov_w = framing_context.get("fov_w_deg")
        fov_h = framing_context.get("fov_h_deg")

        # Build the context sentence with safe formatting (omit None values)
        parts = [f"CRITICAL CONSTRAINT: The user has committed to imaging this object with \"{rig_name}\""]

        spec_parts = []
        if telescope_name:
            spec_parts.append(f"({telescope_name}")
        else:
            spec_parts.append("(")

        if focal_length is not None:
            spec_parts[-1] += f" at {focal_length:.0f}mm"
        else:
            spec_parts[-1] += " at [focal_length]mm"

        if f_ratio is not None:
            spec_parts[-1] += f", f/{f_ratio:.1f}"
        else:
            spec_parts[-1] += ", f/[f_ratio]"

        if pixel_scale is not None:
            spec_parts[-1] += f", pixel scale {pixel_scale:.2f}\"/px"
        else:
            spec_parts[-1] += ", pixel scale [pixel_scale]\"/px"

        if fov_w is not None and fov_h is not None:
            spec_parts[-1] += f", FOV {fov_w:.1f}° × {fov_h:.1f}°"
        elif fov_w is not None:
            spec_parts[-1] += f", FOV {fov_w:.1f}° width"
        else:
            spec_parts[-1] += ", FOV [fov_w]° × [fov_h]°"

        if spec_parts:
            parts.append("".join(spec_parts) + ")")

        parts.append(
            "This is non-negotiable. Do NOT rank other rigs above this one. Do NOT suggest switching rigs as a primary recommendation. All exposure, filter, and framing advice must be built around this specific setup. You may briefly mention limitations of this rig only if directly relevant to achievability, but the committed rig is always the primary recommendation."
        )
        prompt_lines = [" ".join(parts)]

    # Add object description after framing context
    if framing_context:
        prompt_lines.append(f"{object_intro}.")
    else:
        prompt_lines = [f"{object_intro}."]

    # Location context
    if active_location:
        lat = active_location.get("lat")
        loc_name = active_location.get("name", "their primary location")
        active_max_alt = active_location.get("max_altitude_deg")
        if lat is not None:
            hemisphere = "southern hemisphere" if lat < 0 else "northern hemisphere"
            alt_str = f", max altitude {active_max_alt}°" if active_max_alt is not None else ""
            prompt_lines.append(
                f"Primary observing location: {loc_name} (latitude {lat:.1f}°, {hemisphere}{alt_str}). State this exact altitude in your response, do not approximate it."
            )
        if locations and len(locations) > 1:
            other_locs = [l for l in locations if l != active_location]
            loc_parts = []
            for l in other_locs:
                if l.get("lat") is not None:
                    alt = l.get("max_altitude_deg")
                    alt_str = f", max alt {alt}°" if alt is not None else ""
                    loc_parts.append(f"{l['name']} ({l['lat']:.1f}°{alt_str})")
            if loc_parts:
                loc_list = ", ".join(loc_parts)
                prompt_lines.append(
                    f"Additional locations: {loc_list}. Use ONLY these provided altitudes — do NOT calculate or estimate altitudes yourself. Recommend the location with the highest max altitude for this target."
                )

    # Date and simulation context
    if selected_day and selected_month and selected_year:
        import calendar
        month_name = calendar.month_name[int(selected_month)]
        date_str = f"{int(selected_day)} {month_name} {int(selected_year)}"
        if moon_phase is not None:
            context_str = (
                f"Moon illumination: {moon_phase}%. "
                f"Moon separation from target: {moon_separation}°. "
                f"Target max altitude from {active_location.get('name', 'your location')} (at transit {target_transit_time} local): {target_altitude_deg}°. "
                f"When assessing moon impact, you MUST reason in this order: "
                f"1) State the moon conditions factually. "
                f"2) Assess broadband viability. "
                f"3) Assess OSC dual-band filter viability. "
                f"4) Assess mono narrowband (Ha, OIII, SII separately) viability. "
                f"5) Give a single, definitive recommendation — do NOT hedge or present "
                f"contradictory conclusions. Use ONLY the provided moon values."
            )
        else:
            context_str = (
                "Lead paragraph 3 with specific advice for this exact date — "
                "target altitude, whether it is worth attempting."
            )
        date_label = "PLANNING DATE (simulation mode)" if sim_mode else "PLANNING DATE"
        prompt_lines.append(f"{date_label}: {date_str}. {context_str}")

    # Rig context
    if rigs:
        prompt_lines.append(f"\nObject angular size for FOV comparison: {size_arcmin}' (use this to assess which rigs frame it well).")
        prompt_lines.append("Observer's imaging rigs:")
        for rig in rigs:
            rig_name = rig.get("name", "Unnamed rig")
            telescope = rig.get("telescope") or {}
            camera = rig.get("camera") or {}
            tel_name = telescope.get("name", "")
            aperture_mm = rig.get("aperture_mm")
            focal_length = rig.get("effective_focal_length")
            f_ratio = rig.get("f_ratio")
            fov_w = rig.get("fov_w_arcmin")
            cam_name = camera.get("name", "")
            camera_type = rig.get("camera_type", "")
            sensor_width = camera.get("sensor_width_mm")

            parts = [f"{rig_name}:"]
            if tel_name:
                parts.append(f"telescope={tel_name}")
            if aperture_mm is not None:
                parts.append(f"aperture={aperture_mm:.0f}mm")
            if focal_length is not None:
                parts.append(f"focal_length={focal_length:.0f}mm")
            if f_ratio is not None:
                parts.append(f"f_ratio=f/{f_ratio:.1f}")
            if fov_w is not None:
                parts.append(f"fov={fov_w:.0f}'")
            if cam_name:
                parts.append(f"camera={cam_name}")
            if camera_type:
                parts.append(f"camera_type={camera_type}")
            if sensor_width is not None:
                parts.append(f"sensor={sensor_width:.1f}mm")
            prompt_lines.append(" ".join(parts))

    prompt_lines.append(
        "\nWrite observing notes following the paragraph structure above. "
        "Be specific: name filters, give exposure ranges and integration time estimates per rig, "
        "state moon tolerance with minimum separation in degrees, "
        "rank rigs explicitly. End with the poetic sign-off."
    )

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

Sign-off: End with a short poetic sentence on its own line, signed: — Nova.

Formatting rules:
- Plain text only, no markdown, no bullets, no headers
- NO MARKDOWN. No **bold**, no *italic*, no headers. Plain sentences only. Violations of this rule make the output unpublishable.
- Exactly 3 paragraphs separated by a blank line
- No hedging language: never use "might", "could potentially", "perhaps", "it may be worth considering", "consider exploring"
- Tone: warm but efficient. Say more with less.

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


def build_viability_check_prompt(
    objects: list,
    rigs: list,
    moon_phase: float,
    location_name: str,
    sim_date: str = None,
) -> dict:
    """Build system and user prompts for viability check (Pass 1).

    Args:
        objects: List of object dicts with keys: Object, Common Name, Type, Magnitude,
                Size, Constellation, Altitude, Azimuth,
                Observable Duration (min), Max Altitude (°),
                Angular Separation (°), etc.
        rigs: List of user's rig configurations
        moon_phase: Moon illumination percentage (0-100)
        location_name: Name of the observing location
        sim_date: Date in YYYY-MM-DD format (None for live mode)

    Returns:
        dict with "system" and "user" keys containing prompt strings.
    """
    system_prompt = """You are Nova — a warm, sharp, and genuinely passionate astrophotography companion built into the Nova DSO Tracker. You evaluate deep-sky objects for imaging viability based on current conditions and the observer's equipment.

CRITICAL OUTPUT FORMAT:
You MUST respond with ONLY a valid JSON array. No markdown, no code blocks, no explanations before or after. The JSON must be parseable directly.

Each object in the array must have these exact keys:
- "Object": the exact object name from the input. YOU MUST use the exact Object value from the input list as the "Object" key in your response. Do not add common names, parentheticals, or alternate catalog IDs. Return it character-for-character as provided.
- "viable": true or false (boolean)
- "reason": one sentence explaining why this object is viable or not viable

Example:
[
  {"Object": "M31", "viable": true, "reason": "Large galaxy with excellent surface brightness, moon at 15% won't affect it."},
  {"Object": "NGC 7000", "viable": false, "reason": "Faint emission nebula needs dark skies, moon at 80% will wash it out."},
  {"Object": "M42", "viable": true, "reason": "Bright nebula, moon-tolerant even through narrowband filters."}
]

CRITICAL rules:
- aperture_mm = mirror/lens diameter (light gathering). focal_length_mm = optical path (scale/magnification). Never swap these.
- OSC cameras shoot broadband RGB natively (no filter or with a light pollution filter like L-Pro/IDAS). They can also use dual-narrowband filters (e.g. Optolong L-eXtreme) for emission nebulae under bright moon. Do not treat OSC as narrowband-limited.
- Mono cameras: LRGB and narrowband both valid.
- Always derive filter advice from object composition, not just object type label.
- Dark nebulae: no emission lines, no narrowband. Need dark skies and high contrast broadband or luminance only.
- Reflection nebulae: broadband only, no narrowband benefit.
- Emission nebulae: for OSC, dual-narrowband filter (Ha+OIII passband) is the primary recommendation — not optional, not secondary. For mono, full narrowband sequence (Ha/OIII/SII). Both are highly moon-tolerant. State this clearly and first in filter advice.
- Galaxies are primarily broadband targets. However, galaxies with significant star-forming regions or emission nebulae (e.g. M31, M33, NGC 300, Centaurus A) benefit from Ha and OIII blending with a mono camera. Under bright moon, a mono rig with narrowband filters can still image emission-rich galaxies effectively.

"Viable" means this target will produce a useful astrophotograph tonight — not just that it is visually detectable or bright enough to see. Ask yourself: will the finished image have acceptable contrast and detail given tonight's moon and the available equipment?

Specific rules:
- Star clusters (open or globular): ONLY viable if moon separation > 60° AND moon < 50%. Under bright moon, sky background overwhelms stellar contrast regardless of cluster brightness. There is no filter solution for star clusters.
- Planetary nebulae: viable if a narrowband-capable rig exists (mono camera) AND moon separation > 30°. Bright planetary nebulae (mag < 10) are more tolerant. Do not reject bright planetary nebulae purely on moon phase.
- Emission nebulae: viable if OSC with dual-narrowband OR mono rig available AND moon separation > 25°.
- Galaxies: only viable under bright moon (>50%) if they have significant HII emission regions AND a mono rig is available. Pure broadband galaxies are NOT viable above 50% moon regardless of brightness or altitude.
- Dark/reflection nebulae: not viable above 30% moon.

Return a JSON array with all objects, each marked as viable or not viable with a one-sentence reason."""

    # Build the user prompt
    prompt_lines = []

    # Date and moon context
    if sim_date:
        prompt_lines.append(f"Planning date: {sim_date} (simulation mode).")
    else:
        prompt_lines.append("Planning date: Tonight (live mode).")

    prompt_lines.append(f"Location: {location_name}.")
    prompt_lines.append(f"Moon illumination: {moon_phase}%.")

    # Object list - format as compact table-like structure
    prompt_lines.append(f"\nAvailable objects ({len(objects)} total):")
    for obj in objects:
        obj_name = obj.get("Object", "Unknown")
        common_name = obj.get("Common Name", "")
        obj_type = obj.get("Type", "")
        magnitude = obj.get("Magnitude")
        size = obj.get("Size")
        obs_duration = obj.get("Observable Duration (min)")
        max_alt = obj.get("Max Altitude (°)")
        ang_sep = obj.get("Angular Separation (°)")
        constellation = obj.get("Constellation", "")

        # Build compact object description
        parts = [obj_name]
        if common_name:
            parts.append(f'({common_name})')
        if obj_type:
            parts.append(f"{obj_type}")
        if magnitude:
            parts.append(f"mag {magnitude}")
        if size:
            parts.append(f"{size}'")
        if obs_duration:
            parts.append(f"{obs_duration}min")
        if max_alt:
            parts.append(f"max {max_alt}°")
        if ang_sep:
            parts.append(f"moon sep {ang_sep}°")
        if constellation:
            parts.append(f"in {constellation}")

        prompt_lines.append("  " + ", ".join(parts))

    # Rig context
    if rigs:
        prompt_lines.append(f"\nObserver's rigs:")
        for rig in rigs:
            rig_name = rig.get("name", "Unnamed rig")
            telescope = rig.get("telescope") or {}
            camera = rig.get("camera") or {}
            aperture = rig.get("aperture_mm")
            focal_length = rig.get("effective_focal_length")
            f_ratio = rig.get("f_ratio")
            fov = rig.get("fov_w_arcmin")
            cam_name = camera.get("name", "")
            camera_type = rig.get("camera_type", "")

            parts = [f"{rig_name}:"]
            if telescope.get("name"):
                parts.append(f"telescope={telescope['name']}")
            if aperture:
                parts.append(f"aperture={aperture:.0f}mm")
            if focal_length:
                parts.append(f"FL={focal_length:.0f}mm")
            if f_ratio:
                parts.append(f"f/{f_ratio:.1f}")
            if fov:
                parts.append(f"FOV={fov:.0f}'")
            if cam_name:
                parts.append(f"camera={cam_name}")
            if camera_type:
                parts.append(f"type={camera_type}")

            prompt_lines.append("  " + ", ".join(parts))

    # Final instruction
    prompt_lines.append(
        "\nEvaluate each object independently for viability tonight. "
        "Return ONLY the JSON array with the specified format. "
        "Mark each object as viable (true/false) with a one-sentence reason."
    )

    user_prompt = "\n".join(prompt_lines)

    return {"system": system_prompt, "user": user_prompt}


def build_best_objects_prompt(
    objects: list,
    location_name: str,
    location_lat: float,
    location_lon: float,
    moon_phase: float = None,
    rigs: list = None,
    locale: str = "en",
    sim_date: str = None,
    compressed_objects: str = None,
) -> dict:
    """Build system and user prompts for ranking best objects.

    Args:
        objects: List of object dicts with keys: Object, Common Name, Type, Magnitude,
                Size, Constellation, Altitude, Azimuth,
                Observable Duration (min), Max Altitude (°),
                Angular Separation (°), etc.
        location_name: Name of the observing location
        location_lat: Latitude of the location
        location_lon: Longitude of the location
        moon_phase: Moon illumination percentage (0-100)
        rigs: List of user's rig configurations
        locale: ISO locale code for response language (default: "en")
        sim_date: Date in YYYY-MM-DD format (None for live mode)
        compressed_objects: Optional string of pipe-delimited object data.
                          If provided, objects list is ignored and this is used.

    Returns:
        dict with "system" and "user" keys containing prompt strings.
    """
    # Pre-compute moon string to avoid operator precedence issues
    moon_str = f"{int(moon_phase)}%" if moon_phase is not None else "unknown"

    system_prompt = f"""You are Nova — an astrophotography companion built into the Nova DSO Tracker.
You rank deep-sky objects for long-exposure imaging sessions, NOT for
visual observation. Astrophotography ranking logic differs fundamentally
from visual astronomy:
- A bright magnitude does NOT make a cluster better under bright moon —
  sky background glow destroys contrast regardless of object brightness
- A faint emission nebula with unknown magnitude CAN outrank a bright
  cluster under bright moon because narrowband filters block moonlight
  entirely
- Moon illumination + object type compatibility is the PRIMARY ranking
  factor, not altitude or magnitude

TONIGHT: Moon is {moon_str} illuminated.

Use your astrophotography knowledge to assess each object's narrowband capability and moon sensitivity individually. Rank accordingly.

CRITICAL OUTPUT FORMAT:
You MUST respond with ONLY a valid JSON array. No markdown, no code blocks, no explanations before or after. The JSON must be parseable directly.

Return a maximum of 20 objects, ranked from best (1) to worst (20). If more objects are available, select only the top 20.

Each object in the array must have these exact keys:
- "Object": the exact object name from the input. YOU MUST use the exact Object value from the input list as the "Object" key in your response. Do not add common names, parentheticals, or alternate catalog IDs. Return it character-for-character as provided.
- "rank": integer (1 = best)
- "reason": one sentence explaining why this object is ranked here
- "recommended_rigs": array of up to 5 rig names in order of fit (ranked list from best to good). Must use exact rig names from the provided list. Include the most suitable rigs for this target.

Example:
[
  {{"Object": "M31", "rank": 1, "reason": "Large galaxy with excellent surface brightness, well above 50° all night. Moon at 15% won't affect it.", "recommended_rigs": ["Main Imaging Rig", "Portable Setup"]}},
  {{"Object": "NGC 7000", "rank": 2, "reason": "Compact nebula, high surface brightness. Fits well in narrowband through the 8\" scope.", "recommended_rigs": ["Portable Setup", "Widefield Rig"]}}
]

CRITICAL rules:
- aperture_mm = mirror/lens diameter (light gathering). focal_length_mm = optical path (scale/magnification). Never swap these.
- OSC cameras shoot broadband RGB natively (no filter or with a light pollution filter like L-Pro/IDAS). They can also use dual-narrowband filters (e.g. Optolong L-eXtreme) for emission nebulae under bright moon. Do not treat OSC as narrowband-limited.
- Mono cameras: LRGB and narrowband both valid.
- Always derive filter advice from object composition, not just object type label.
- Dark nebulae: no emission lines, no narrowband. Need dark skies and high contrast broadband or luminance only.
- Reflection nebulae: broadband only, no narrowband benefit.
- Emission nebulae: for OSC, dual-narrowband filter (Ha+OIII passband) is the primary recommendation — not optional, not secondary. For mono, full narrowband sequence (Ha/OIII/SII). Both are highly moon-tolerant. State this clearly and first in filter advice.
- Galaxies are primarily broadband targets. However, galaxies with significant star-forming regions or emission nebulae (e.g. M31, M33, NGC 300, Centaurus A) benefit from Ha and OIII blending with a mono camera. Under bright moon, a mono rig with narrowband filters can still image emission-rich galaxies effectively.

You are an experienced astrophotographer advising a fellow astronomer on what to image tonight.

These objects have already been qualified as viable for tonight. Rank them in order of best achievable image quality tonight, considering sky position, moon separation, equipment match, and object character. Observable time has already been filtered — do not weight it heavily.

Prioritize objects that are genuinely viable tonight. Omit broadband-only
targets that are clearly unsuitable under current moon conditions. However,
always return AT LEAST 5 objects — if narrowband targets are available,
rank those first.

Return the top 20 opportunities in order of best to worst for tonight.

Respond in the language of this ISO locale code: {locale}."""

    # Build the user prompt
    prompt_lines = []

    # Location context
    hemisphere = "southern hemisphere" if location_lat < 0 else "northern hemisphere"
    prompt_lines.append(f"Observing location: {location_name} (latitude {location_lat:.1f}°, longitude {location_lon:.1f}°, {hemisphere}).")

    # Date and moon context
    if sim_date:
        prompt_lines.append(f"Planning date: {sim_date} (simulation mode).")
    else:
        prompt_lines.append("Planning date: Tonight (live mode).")

    if moon_phase is not None:
        prompt_lines.append(f"Moon illumination: {moon_phase}%.")

    # Object list - use compressed format if provided
    if compressed_objects:
        prompt_lines.append(f"\nAvailable objects ({len(objects)} total):")
        prompt_lines.append("Format: NAME|TYPE|OBS_MINUTES|ALT_DEG|MOON_SEP_DEG|MAG|SIZE_ARCMIN")
        prompt_lines.append("mag? = unknown magnitude, size? = unknown size")
        prompt_lines.append(compressed_objects)
    else:
        # Fallback to original verbose format
        prompt_lines.append(f"\nAvailable objects ({len(objects)} total):")
        for obj in objects:
            obj_name = obj.get("Object", "Unknown")
            common_name = obj.get("Common Name", "")
            obj_type = obj.get("Type", "")
            magnitude = obj.get("Magnitude")
            size = obj.get("Size")
            obs_duration = obj.get("Observable Duration (min)")
            max_alt = obj.get("Max Altitude (°)")
            ang_sep = obj.get("Angular Separation (°)")
            constellation = obj.get("Constellation", "")

            # Build compact object description
            parts = [obj_name]
            if common_name:
                parts.append(f'({common_name})')
            if obj_type:
                parts.append(f"{obj_type}")
            if magnitude:
                parts.append(f"mag {magnitude}")
            if size:
                parts.append(f"{size}'")
            if obs_duration:
                parts.append(f"{obs_duration}min")
            if max_alt:
                parts.append(f"max {max_alt}°")
            if ang_sep:
                parts.append(f"moon sep {ang_sep}°")
            if constellation:
                parts.append(f"in {constellation}")

            prompt_lines.append("  " + ", ".join(parts))

    # Rig context
    if rigs:
        prompt_lines.append(f"\nObserver's rigs:")
        for rig in rigs:
            rig_name = rig.get("name", "Unnamed rig")
            telescope = rig.get("telescope") or {}
            camera = rig.get("camera") or {}
            aperture = rig.get("aperture_mm")
            focal_length = rig.get("effective_focal_length")
            f_ratio = rig.get("f_ratio")
            fov = rig.get("fov_w_arcmin")
            cam_name = camera.get("name", "")
            camera_type = rig.get("camera_type", "")

            parts = [f"{rig_name}:"]
            if telescope.get("name"):
                parts.append(f"telescope={telescope['name']}")
            if aperture:
                parts.append(f"aperture={aperture:.0f}mm")
            if focal_length:
                parts.append(f"FL={focal_length:.0f}mm")
            if f_ratio:
                parts.append(f"f/{f_ratio:.1f}")
            if fov:
                parts.append(f"FOV={fov:.0f}'")
            if cam_name:
                parts.append(f"camera={cam_name}")
            if camera_type:
                parts.append(f"type={camera_type}")

            prompt_lines.append("  " + ", ".join(parts))

    # Final instruction
    prompt_lines.append(
        "\nRank these objects for the current conditions. "
        "Return ONLY the JSON array with the specified format. "
        "Rank 1 is the best choice tonight. "
        "Provide one recommended rig per object."
    )
    # CRITICAL: This is the LAST instruction - model's first token must be the opening bracket
    prompt_lines.append("Respond with ONLY a JSON array. No preamble, no explanation. Start with [")

    user_prompt = "\n".join(prompt_lines)

    return {"system": system_prompt, "user": user_prompt}
