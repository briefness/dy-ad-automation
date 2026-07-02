"""
镜头语言库 (Cinematic Language Library)

为视频 Prompt 生成提供结构化的电影级镜头元素，
包括景别、运镜、角度、光影、构图、景深、胶片质感等。
"""

# ============================================================
# 景别 Shot Size
# ============================================================

SHOT_SIZES = {
    "extreme_close_up": "extreme close-up, macro detail shot",
    "close_up": "close-up shot",
    "medium_close_up": "medium close-up",
    "medium": "medium shot",
    "medium_long": "medium long shot",
    "long": "long shot, wide angle",
    "extreme_long": "extreme long shot, vast landscape",
}

# ============================================================
# 运镜 Camera Movement
# ============================================================

CAMERA_MOVEMENTS = {
    "push_in": "slow push-in, camera dollying forward",
    "pull_out": "slow pull-out, camera dollying backward",
    "pan_left": "slow pan to the left",
    "pan_right": "slow pan to the right",
    "tilt_up": "slow tilt upward",
    "tilt_down": "slow tilt downward",
    "orbit_left": "slow orbit around subject from left",
    "handheld": "subtle handheld camera, natural shake",
    "static": "locked-off static shot",
    "zoom_in": "slow zoom in",
    "tracking": "tracking shot, following subject",
}

# ============================================================
# 拍摄角度 Camera Angle
# ============================================================

CAMERA_ANGLES = {
    "eye_level": "eye-level angle",
    "low_angle": "low angle shot, looking up",
    "high_angle": "high angle shot, looking down",
    "bird_eye": "bird's eye view, top-down shot",
    "dutch": "dutch angle, tilted frame",
    "over_shoulder": "over-the-shoulder shot",
}

# ============================================================
# 光影 Lighting
# ============================================================

LIGHTING_STYLES = {
    "golden_hour": "golden hour lighting, warm soft sunlight, long shadows",
    "studio_soft": "soft studio lighting, even illumination, no harsh shadows",
    "rembrandt": "Rembrandt lighting, dramatic side light, triangle highlight on cheek",
    "backlit": "backlit, rim light, glowing edges, atmospheric haze",
    "chiaroscuro": "chiaroscuro lighting, strong light-dark contrast",
    "neon_night": "neon night lighting, vibrant color casts, moody",
    "natural_window": "soft natural window light, gentle diffusion",
    "moody_dark": "moody low-key lighting, deep shadows, dramatic",
}

# ============================================================
# 构图 Composition
# ============================================================

COMPOSITIONS = {
    "rule_of_thirds": "rule of thirds composition",
    "center": "centered composition, symmetrical",
    "leading_lines": "leading lines composition, depth",
    "frame_within_frame": "frame within frame composition",
    "negative_space": "negative space, minimalist composition",
    "foreground_blur": "foreground element blur, layered depth composition",
}

# ============================================================
# 景深 Depth of Field
# ============================================================

DEPTH_OF_FIELD = {
    "shallow": "shallow depth of field, creamy bokeh, f/1.4",
    "medium": "medium depth of field, subject in focus, soft background",
    "deep": "deep focus, everything sharp, f/8",
}

# ============================================================
# 胶片质感 Film Look
# ============================================================

FILM_LOOKS = {
    "none": "",
    "35mm": "shot on 35mm film, grain texture, cinematic color grading",
    "16mm": "shot on 16mm film, visible grain, vintage look",
    "anamorphic": "anamorphic lens, lens flares, oval bokeh, cinematic",
    "arri": "ARRI Alexa look, cinematic color science, natural skin tones",
}

# ============================================================
# 分镜节奏曲线（按叙事功能分配视觉强度）
# ============================================================
# 每个叙事段落的默认镜头语言配置
# narrative -> {shot_size, camera_movement, camera_angle, lighting, composition, dof, intensity}

SEGMENT_CINEMATIC_PROFILE = {
    "hook": {
        "description": "Hook 段：视觉冲击力最强，抓注意力",
        "shot_size": "close_up",
        "camera_movement": "push_in",
        "camera_angle": "eye_level",
        "lighting": "chiaroscuro",
        "composition": "rule_of_thirds",
        "dof": "shallow",
        "intensity": 10,
    },
    "turning_point": {
        "description": "痛点段：相对平实，真实感",
        "shot_size": "medium",
        "camera_movement": "static",
        "camera_angle": "eye_level",
        "lighting": "natural_window",
        "composition": "rule_of_thirds",
        "dof": "medium",
        "intensity": 5,
    },
    "showcase": {
        "description": "展示段：最精致，产品质感",
        "shot_size": "medium_close_up",
        "camera_movement": "orbit_left",
        "camera_angle": "eye_level",
        "lighting": "studio_soft",
        "composition": "center",
        "dof": "shallow",
        "intensity": 8,
    },
    "result": {
        "description": "效果段：情绪最强，情绪高潮",
        "shot_size": "close_up",
        "camera_movement": "push_in",
        "camera_angle": "eye_level",
        "lighting": "backlit",
        "composition": "rule_of_thirds",
        "dof": "shallow",
        "intensity": 9,
    },
    "cta": {
        "description": "CTA 段：清晰稳定，行动号召",
        "shot_size": "medium",
        "camera_movement": "static",
        "camera_angle": "eye_level",
        "lighting": "studio_soft",
        "composition": "center",
        "dof": "medium",
        "intensity": 6,
    },
}

# 兼容旧的 narrative 命名映射
NARRATIVE_ALIASES = {
    "pain_point": "turning_point",
    "product_show": "showcase",
    "effect": "result",
    "call_to_action": "cta",
}


def get_segment_profile(narrative: str) -> dict:
    """获取指定叙事段落的镜头语言配置

    Args:
        narrative: 叙事功能名称（hook/turning_point/showcase/result/cta 等）

    Returns:
        段落镜头语言配置字典
    """
    # 先尝试别名映射
    normalized = NARRATIVE_ALIASES.get(narrative, narrative)
    return SEGMENT_CINEMATIC_PROFILE.get(
        normalized,
        SEGMENT_CINEMATIC_PROFILE["turning_point"],  # 默认用中段配置
    )


# ============================================================
# 升级后的导演风格库（10 种深度风格）
# ============================================================
# 每种风格包含：
#   - name / name_en / description（向后兼容）
#   - visual_key: 整体视觉基调（英文）
#   - lighting: 光影风格 key（对应 LIGHTING_STYLES）
#   - color_grade: 色彩调性描述
#   - film_look: 胶片质感 key（对应 FILM_LOOKS）
#   - shot_size_preference: 偏好景别列表（按优先级）
#   - movement_preference: 偏好运镜列表（按优先级）
#   - angle_preference: 偏好角度列表（按优先级）
#   - composition_preference: 偏好构图列表
#   - dof_preference: 偏好景深
#   - rhythm: 节奏特点描述
#   - camera_push / camera_pull / camera_orbit（向后兼容，保留）
#   - transition_match / transition_light（向后兼容，保留）
#   - bgm_keywords / mood（向后兼容，保留）

DEEP_CINEMATIC_STYLES = {
    "hitchcock": {
        "name": "希区柯克",
        "name_en": "Hitchcock",
        "description": "心理悬疑大师，推轨变焦制造焦虑",
        "visual_key": "suspenseful, high contrast, psychological tension, voyeuristic framing",
        "lighting": "chiaroscuro",
        "color_grade": "desaturated with green tint, high contrast, cold gray tones",
        "film_look": "35mm",
        "shot_size_preference": ["close_up", "medium_close_up", "medium"],
        "movement_preference": ["push_in", "static", "zoom_in"],
        "angle_preference": ["eye_level", "dutch", "high_angle"],
        "composition_preference": ["frame_within_frame", "rule_of_thirds"],
        "dof_preference": "medium",
        "rhythm": "slow building tension, deliberate pacing, sudden reveals",
        # 向后兼容字段
        "camera_push": "Hitchcock dolly zoom: camera pushes in slowly while background visually stretches, Vertigo effect, psychological tension",
        "camera_pull": "Hitchcock pull back: camera pulls back to reveal isolation, subject looks small and vulnerable, voyeuristic tension",
        "camera_orbit": "Hitchcock orbit: camera circles subject at distance, paranoid surveillance, voyeuristic tension, 1960s thriller style",
        "transition_match": "Spiral focus transition: camera spirals into subject's face, Vertigo-style, obsession theme",
        "transition_light": "Lens flare transition: camera moves toward bright light, lens flare washes out, mystery and discovery",
        "lighting_desc": "cold gray tones, high contrast, chiaroscuro, side-backlight creating deep shadows on face",
        "color": "desaturated colors, green tint, high contrast",
        "bgm_keywords": ["suspense", "thriller", "mysterious", "tension", "dark ambient"],
        "mood": "psychological tension, dread, voyeuristic unease",
    },
    "kubrick": {
        "name": "库布里克",
        "name_en": "Kubrick",
        "description": "对称构图大师，单点透视，仪式感",
        "visual_key": "symmetrical composition, one-point perspective, clinical precision, cosmic dread",
        "lighting": "moody_dark",
        "color_grade": "high saturation red/blue/green, or warm candlelight yellow, precise color blocking",
        "film_look": "35mm",
        "shot_size_preference": ["medium", "long", "medium_long"],
        "movement_preference": ["push_in", "static", "tracking"],
        "angle_preference": ["eye_level", "low_angle", "high_angle"],
        "composition_preference": ["center", "leading_lines", "frame_within_frame"],
        "dof_preference": "deep",
        "rhythm": "slow deliberate pace, long takes, formal symmetry, ritualistic timing",
        # 向后兼容字段
        "camera_push": "Kubrick one-point perspective dolly: camera pushes straight down center of symmetrical corridor, everything aligns to vanishing point, 2001 style, cosmic dread",
        "camera_pull": "Kubrick dolly out: camera pulls back from subject to reveal vast symmetrical space, Barry Lyndon style, isolation and scale",
        "camera_orbit": "Kubrick symmetrical orbit: camera circles subject maintaining perfect center composition, 2001-style, cosmic grandeur",
        "transition_match": "Match cut from 2001: A Space Odyssey: cut from object flying up to spaceship, matching shape and movement, Kubrick style, evolution theme",
        "transition_light": "Kubrick time jump: sudden cut from bright daylight to dark night, same location, same composition, The Shining style",
        "lighting_desc": "strong frontal key light, deep shadows on sides, dramatic chiaroscuro, Barry Lyndon candlelight style",
        "color": "high saturation red/blue/green, or warm candlelight yellow",
        "bgm_keywords": ["classical", "epic", "dramatic", "orchestral", "cinematic"],
        "mood": "cosmic dread, meticulous precision, fatalistic grandeur",
    },
    "spielberg": {
        "name": "斯皮尔伯格",
        "name_en": "Spielberg",
        "description": "娱乐与情感大师，拉轨揭示，平民英雄",
        "visual_key": "warm wonder, emotional catharsis, rim light halos, sweeping camera",
        "lighting": "golden_hour",
        "color_grade": "warm yellow + teal blue contrast, soft natural light, magical glow",
        "film_look": "35mm",
        "shot_size_preference": ["medium", "medium_close_up", "close_up"],
        "movement_preference": ["push_in", "pull_out", "tracking"],
        "angle_preference": ["eye_level", "low_angle"],
        "composition_preference": ["rule_of_thirds", "negative_space"],
        "dof_preference": "medium",
        "rhythm": "building wonder, emotional crescendos, sweeping reveals, crowd-pleasing pacing",
        # 向后兼容字段
        "camera_push": "Spielberg push in: slow push toward subject, wonder building, Jaws style, anticipation and dread",
        "camera_pull": "Spielberg dolly out reveal: camera slowly pulls back from subject's face to reveal vast isolation, Jaws style, awe and wonder, subject small against environment",
        "camera_orbit": "Spielberg orbit: camera circles subject with rim light, E.T. style, magical halo, wonder and discovery",
        "transition_match": "Spielberg lens flare transition: camera moves toward bright light source, lens flare washes out image, Jaws/Close Encounters style",
        "transition_light": "Wide reveal: camera pulls back from extreme close-up to show full context, Jurassic Park style, awe and spectacle",
        "lighting_desc": "rim light creating halo around subject, side-backlight, magical glow, warm golden hour",
        "color": "warm yellow + teal blue contrast, or soft natural light",
        "bgm_keywords": ["adventure", "magical", "orchestral", "wonder", "cinematic"],
        "mood": "wonder, awe, emotional catharsis, Spielberg magic",
    },
    "wong-kar-wai": {
        "name": "王家卫",
        "name_en": "Wong Kar-wai",
        "description": "霓虹美学大师，慢快门，都市孤独与浪漫",
        "visual_key": "neon-lit urban melancholy, step-printing motion, rain reflections, unspoken desire",
        "lighting": "neon_night",
        "color_grade": "saturated reds and greens mixed with cool blues, high contrast, teal and orange",
        "film_look": "anamorphic",
        "shot_size_preference": ["close_up", "medium_close_up", "extreme_close_up"],
        "movement_preference": ["push_in", "handheld", "static"],
        "angle_preference": ["eye_level", "dutch"],
        "composition_preference": ["foreground_blur", "frame_within_frame", "rule_of_thirds"],
        "dof_preference": "shallow",
        "rhythm": "dreamlike slow motion, step-printing staccato, lingering close-ups, temporal disconnection",
        # 向后兼容字段
        "camera_push": "Wong Kar-wai push in: slow push with step-printing effect, Chungking Express style, neon lights blur, urban loneliness and romance",
        "camera_pull": "Wong Kar-wai pull back: slow pull back from extreme close-up, rain-soaked streets, In the Mood for Love style, unspoken desire",
        "camera_orbit": "Wong Kar-wai orbit: camera circles through narrow hallway, Fallen Angels style, neon reflections, chaotic intimacy",
        "transition_match": "Wong Kar-wai match cut: cut from one character's face to another's matching expression, In the Mood for Love style, parallel longing",
        "transition_light": "Wong Kar-wai light leak: lens flare and light leak transition, 2001: A Space Odyssey style, temporal distortion",
        "lighting_desc": "neon signs, rain reflections, low-key lighting, warm tungsten mixed with cool neon",
        "color": "saturated reds, greens, blues, high contrast, teal and orange",
        "bgm_keywords": ["jazz", "blues", "moody", "atmospheric", "noir"],
        "mood": "urban loneliness, unspoken desire, nostalgic romance, temporal dislocation",
    },
    "anderson": {
        "name": "韦斯·安德森",
        "name_en": "Wes Anderson",
        "description": "对称构图女王， pastel 色彩，复古布景",
        "visual_key": "symmetrical tableaux, pastel color palette, whimsical nostalgia, meticulous design",
        "lighting": "studio_soft",
        "color_grade": "pastel pinks, yellows, blues, symmetrical color blocking, vintage faded look",
        "film_look": "35mm",
        "shot_size_preference": ["medium", "medium_long", "long"],
        "movement_preference": ["static", "pan_right", "tracking"],
        "angle_preference": ["eye_level", "high_angle"],
        "composition_preference": ["center", "frame_within_frame"],
        "dof_preference": "medium",
        "rhythm": "measured symmetrical movements, whip-pans, deadpan timing, chapter-based structure",
        # 向后兼容字段
        "camera_push": "Wes Anderson push in: symmetrical push in on centered subject, Grand Budapest Hotel style, meticulous composition, pastel colors",
        "camera_pull": "Wes Anderson pull back: symmetrical pull back to reveal perfect tableau, Royal Tenenbaums style, emotional distance and beauty",
        "camera_orbit": "Wes Anderson orbit: camera circles subject maintaining perfect symmetry, Moonrise Kingdom style, nostalgic whimsy",
        "transition_match": "Wes Anderson wipe: iris wipe or horizontal wipe to next scene, Fantastic Mr. Fox style, playful transition",
        "transition_light": "Wes Anderson chapter title: text card with symmetrical framing, Grand Budapest style, narrative whimsy",
        "lighting_desc": "soft even lighting, no harsh shadows, pastel color palette, vintage aesthetic",
        "color": "pastel pinks, yellows, blues, symmetrical color blocking",
        "bgm_keywords": ["whimsical", "vintage", "acoustic", "indie folk", "quirky"],
        "mood": "nostalgic whimsy, emotional distance, meticulous beauty, bittersweet",
    },
    "nolan": {
        "name": "诺兰",
        "name_en": "Nolan",
        "description": "实景特效大师， IMAX 比例，时间 Manipulation",
        "visual_key": "IMAX-scale practical effects, temporal manipulation, desaturated realism, intellectual grandeur",
        "lighting": "natural_window",
        "color_grade": "desaturated cool tones with warm practical light accents, IMAX film stock quality, high dynamic range",
        "film_look": "arri",
        "shot_size_preference": ["long", "medium_long", "medium"],
        "movement_preference": ["push_in", "pull_out", "tracking"],
        "angle_preference": ["low_angle", "high_angle", "eye_level"],
        "composition_preference": ["leading_lines", "rule_of_thirds", "center"],
        "dof_preference": "deep",
        "rhythm": "cross-cut parallel action, building intensity, temporal layering, IMAX-sized reveals",
        # 向后兼容字段
        "camera_push": "Nolan IMAX push in: massive IMAX push in on subject's face, practical effects, Inception style, overwhelming scale",
        "camera_pull": "Nolan pull back: camera pulls back from small subject to reveal massive practical set, Inception dream fold style, scale and disorientation",
        "camera_orbit": "Nolan IMAX orbit: camera circles subject on massive practical set, Interstellar style, cosmic scale, IMAX grandeur",
        "transition_match": "Nolan cross-cut: cut between two simultaneous actions, Inception style, parallel narrative tension",
        "transition_light": "Nolan practical effect transition: cut from dream to reality through practical effect, Inception kick style, temporal distortion",
        "lighting_desc": "practical lights only, naturalistic, IMAX quality, high dynamic range, Dunkirk natural light",
        "color": "desaturated cool tones or warm practical lights, IMAX film stock",
        "bgm_keywords": ["epic", "dramatic", "orchestral", "cinematic", "powerful"],
        "mood": "grand scale, temporal disorientation, practical awe, intellectual tension",
    },
    "scorsese": {
        "name": "斯科塞斯",
        "name_en": "Scorsese",
        "description": "跟踪镜头大师， steadicam，街头叙事",
        "visual_key": "energetic tracking shots, urban realism, practical lighting, raw street energy",
        "lighting": "neon_night",
        "color_grade": "warm golden tones or cool blue urban night, vintage film stock look, high contrast",
        "film_look": "35mm",
        "shot_size_preference": ["medium", "medium_close_up", "long"],
        "movement_preference": ["tracking", "push_in", "handheld"],
        "angle_preference": ["eye_level", "low_angle"],
        "composition_preference": ["rule_of_thirds", "leading_lines"],
        "dof_preference": "medium",
        "rhythm": "propulsive tracking, quick cuts, freeze frames, narrative energy, rock-and-roll pacing",
        # 向后兼容字段
        "camera_push": "Scorsese push in: slow push through crowded scene, Goodfellas style, tracking through space, narrative momentum",
        "camera_pull": "Scorsese pull back: camera pulls back from subject in crowded environment, Taxi Driver style, urban isolation",
        "camera_orbit": "Scorsese tracking orbit: Steadicam tracking shot circling subject through environment, Goodfellas Copacabana style, smooth narrative flow",
        "transition_match": "Scorsese freeze frame: sudden freeze frame on subject's face, then cut to next scene, Goodfellas style, narrative punctuation",
        "transition_light": "Scorsese iris out: iris closes on subject, then opens on next scene, vintage cinema style, Taxi Driver style",
        "lighting_desc": "practical lights from environment, neon signs, street lamps, naturalistic urban lighting, Raging Bull style",
        "color": "warm golden tones or cool blue urban night, vintage film stock look",
        "bgm_keywords": ["rock", "classic rock", "blues", "urban", "street"],
        "mood": "urban energy, narrative momentum, violent beauty, Catholic guilt",
    },
    "denis-villeneuve": {
        "name": "维伦纽瓦",
        "name_en": "Denis Villeneuve",
        "description": "宏大尺度，静谧恐惧，宇宙诗意",
        "visual_key": "monumental scale, quiet dread, architectural composition, cosmic poetry",
        "lighting": "moody_dark",
        "color_grade": "desaturated oranges and blues, muted cosmic tones, sand and shadow palette",
        "film_look": "arri",
        "shot_size_preference": ["long", "extreme_long", "medium"],
        "movement_preference": ["push_in", "static", "pull_out"],
        "angle_preference": ["low_angle", "high_angle", "bird_eye"],
        "composition_preference": ["negative_space", "leading_lines", "center"],
        "dof_preference": "deep",
        "rhythm": "slow meditative pace, massive reveals, silence and tension, geometric precision",
        # 向后兼容字段
        "camera_push": "Villeneuve push in: massive push in on subject's face, Arrival style, linguistic alienation, cosmic scale",
        "camera_pull": "Villeneuve pull back: camera pulls back from small human to massive alien craft, Arrival style, overwhelming scale",
        "camera_orbit": "Villeneuve orbit: camera circles subject in vast desert, Dune style, sandworm scale, epic silence",
        "transition_match": "Villeneuve match cut: cut from human to alien perspective, Arrival style, non-linear time",
        "transition_light": "Villeneuve light burst: sudden light burst transition, Dune style, spice revelation, cosmic awe",
        "lighting_desc": "natural harsh light, desert sun, soft interior light, chiaroscuro, IMAX scale",
        "color": "desaturated oranges and blues, Dune desert palette, muted cosmic tones",
        "bgm_keywords": ["epic", "ambient", "dramatic", "cinematic", "powerful"],
        "mood": "cosmic awe, linguistic mystery, political tension, quiet dread, epic scale",
    },
    "koreeda": {
        "name": "是枝裕和",
        "name_en": "Koreeda",
        "description": "日常诗意，家庭纽带，克制温情",
        "visual_key": "quiet domestic observation, soft natural light, understated emotion, everyday poetry",
        "lighting": "natural_window",
        "color_grade": "muted earth tones, soft pastels, desaturated blues and greens, Japanese aesthetic",
        "film_look": "35mm",
        "shot_size_preference": ["medium", "medium_long", "medium_close_up"],
        "movement_preference": ["static", "tracking", "pan_left"],  # Bug 3 fix: "slow pan" 不是有效 key，改为 pan_left
        "angle_preference": ["eye_level", "low_angle"],
        "composition_preference": ["rule_of_thirds", "negative_space"],
        "dof_preference": "medium",
        "rhythm": "slow observational pace, long static takes, gentle movement, quiet emotional buildup",
        # 向后兼容字段
        "camera_push": "Koreeda push in: slow push in on mundane detail, After the Storm style, domestic poetry, quiet observation",
        "camera_pull": "Koreeda pull back: pull back from family moment to reveal context, Shoplifters style, chosen family bonds",
        "camera_orbit": "Koreeda orbit: camera circles family at table, Our Little Sister style, seaside town, gentle rhythm",
        "transition_match": "Koreeda match cut: cut from one family member to another, After Life style, memory and loss",
        "transition_light": "Koreeda window light: soft window light transition, Nobody Knows style, childhood resilience",
        "lighting_desc": "soft natural window light, overcast sky, indoor practical lights, gentle and diffused",
        "color": "muted earth tones, soft pastels, desaturated blues and greens, Japanese aesthetic",
        "bgm_keywords": ["piano", "gentle", "acoustic", "peaceful", "emotional"],
        "mood": "quiet observation, familial bonds, gentle melancholy, everyday heroism",
    },
    "tarantino": {
        "name": "昆汀",
        "name_en": "Tarantino",
        "description": "类型拼贴，暴力美学，流行文化引用",
        "visual_key": "pulp aesthetic, bold composition, genre pastiche, pop culture bravado",
        "lighting": "chiaroscuro",
        "color_grade": "saturated primary colors, high contrast black and white interludes, retro Technicolor look",
        "film_look": "16mm",
        "shot_size_preference": ["close_up", "extreme_close_up", "medium"],
        "movement_preference": ["push_in", "tracking", "static"],
        "angle_preference": ["low_angle", "dutch", "eye_level"],
        "composition_preference": ["rule_of_thirds", "frame_within_frame"],
        "dof_preference": "medium",
        "rhythm": "snappy dialogue-driven scenes, sudden violence, non-linear chapter structure, punchy editing",
        # 向后兼容字段
        "camera_push": "Tarantino push in: steady push in on tense face, Pulp Fiction style, extreme close-up, pop culture reference",
        "camera_pull": "Tarantino pull back: pull back from trunk reveal, Pulp Fiction style, surprise and dark humor",
        "camera_orbit": "Tarantino orbit: camera circles with steadycam, Reservoir Dogs style, warehouse tension, pop soundtrack",
        "transition_match": "Tarantino match cut: cut from one violent act to another, Kill Bill style, genre pastiche",
        "transition_light": "Tarantino title card: bold text card transition, Pulp Fiction style, chapter break, irreverence",
        "lighting_desc": "practical lights, neon signs, car headlights, high contrast, noir influences",
        "color": "saturated primary colors, black and white interludes, retro film stock",
        "bgm_keywords": ["rock", "funk", "soul", "retro", "classic"],
        "mood": "dark humor, genre pastiche, pop culture bravado, stylized violence",
    },
}

# ============================================================
# 辅助函数
# ============================================================


def build_cinematic_prompt_elements(
    style_key: str,
    narrative: str,
) -> dict:
    """根据风格和叙事功能，构建完整的电影感 Prompt 元素

    融合导演风格偏好 + 段落节奏曲线，生成结构化的镜头语言元素。

    Args:
        style_key: 导演风格 key（如 hitchcock / spielberg）
        narrative: 叙事功能（hook / turning_point / showcase / result / cta）

    Returns:
        包含各镜头元素英文字符串的字典：
        {
            "shot_size": "...",
            "camera_movement": "...",
            "camera_angle": "...",
            "lighting": "...",
            "composition": "...",
            "dof": "...",
            "film_look": "...",
            "color_grade": "...",
            "visual_key": "...",
        }
    """
    style = DEEP_CINEMATIC_STYLES.get(style_key)
    seg_profile = get_segment_profile(narrative)

    if not style:
        # 无风格时，使用段落默认配置
        return {
            "shot_size": SHOT_SIZES.get(seg_profile["shot_size"], ""),
            "camera_movement": CAMERA_MOVEMENTS.get(seg_profile["camera_movement"], ""),
            "camera_angle": CAMERA_ANGLES.get(seg_profile["camera_angle"], ""),
            "lighting": LIGHTING_STYLES.get(seg_profile["lighting"], ""),
            "composition": COMPOSITIONS.get(seg_profile["composition"], ""),
            "dof": DEPTH_OF_FIELD.get(seg_profile["dof"], ""),
            "film_look": "",
            "color_grade": "",
            "visual_key": "",
        }

    # P1 修复：4 个视觉元素（角度/光影/构图/景深）现在按段落 profile 与风格偏好共同决策
    # 策略：若段落值在风格偏好列表中则优先用段落值，否则用风格偏好中的第一项
    intensity = seg_profile.get("intensity", 5)  # P1 修复：使用 intensity 字段

    # - 景别：段落景别优先，不在风格偏好中则用风格第一偏好
    shot_size_key = seg_profile["shot_size"]
    if shot_size_key not in style.get("shot_size_preference", []):
        shot_size_key = style["shot_size_preference"][0]

    # - 运镜：段落运镜优先，不在风格偏好中则用风格第一偏好
    movement_key = seg_profile["camera_movement"]
    if movement_key not in style.get("movement_preference", []):
        movement_key = style["movement_preference"][0]

    # - 角度：P1 修复：段落角度优先，若在风格偏好中则用段落值，否则用风格第一偏好
    seg_angle = seg_profile["camera_angle"]
    angle_prefs = style.get("angle_preference", [])
    if seg_angle in angle_prefs:
        angle_key = seg_angle
    elif angle_prefs:
        angle_key = angle_prefs[0]
    else:
        angle_key = seg_angle

    # - 光影：P1 修复：高强度段落（hook/result, intensity >= 8）允许段落覆盖风格光影
    style_lighting = style.get("lighting", seg_profile["lighting"])
    if intensity >= 8:
        # 高强度段落：优先用段落光影，增强戏剧性
        lighting_key = seg_profile["lighting"]
    else:
        lighting_key = style_lighting

    # - 构图：P1 修复：段落构图优先，若在风格偏好中则用段落值，否则用风格第一偏好
    seg_composition = seg_profile["composition"]
    comp_prefs = style.get("composition_preference", [])
    if seg_composition in comp_prefs:
        composition_key = seg_composition
    elif comp_prefs:
        composition_key = comp_prefs[0]
    else:
        composition_key = seg_composition

    # - 景深：P1 修复：intensity 字段驱动景深选择
    # intensity >= 8 -> shallow（强冲击，拉焦感）
    # intensity <= 4 -> medium（平实段落）
    # 其余 -> 沿用风格偏好
    style_dof = style.get("dof_preference", seg_profile["dof"])
    if intensity >= 8:
        dof_key = "shallow"
    elif intensity <= 4:
        dof_key = "medium"
    else:
        dof_key = style_dof

    # - 胶片质感
    film_look_key = style.get("film_look", "none")

    return {
        "shot_size": SHOT_SIZES.get(shot_size_key, ""),
        "camera_movement": CAMERA_MOVEMENTS.get(movement_key, ""),
        "camera_angle": CAMERA_ANGLES.get(angle_key, ""),
        "lighting": LIGHTING_STYLES.get(lighting_key, ""),
        "composition": COMPOSITIONS.get(composition_key, ""),
        "dof": DEPTH_OF_FIELD.get(dof_key, ""),
        "film_look": FILM_LOOKS.get(film_look_key, ""),
        "color_grade": style.get("color_grade", ""),
        "visual_key": style.get("visual_key", ""),
        "intensity": intensity,  # 透传给调用方，可用于转场强度等后续决策
    }
