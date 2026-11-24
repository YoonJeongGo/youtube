"""Render schema-v2 Shorts from one measured timeline; never infer missing audio.

Output is a review candidate. Technical checks do not constitute visual or
listening approval. Review stills are decoded from the encoded MP4.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from fractions import Fraction
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont, ImageOps
from longform_visuals import VisualShotPlan, timeline_picture_shots

from episode_core import (DETAIL_PROFILE, atomic_json, duration_limits, load_json, object_hash, safe_child,
                          script_hash, sha256_file, speech_segments, is_longform, video_spec, image_names)

W, H, FPS = 1080, 1920, 30
COL_A, COL_B = (255, 107, 53), (72, 192, 255)
WHITE, BLACK, YELLOW = (248, 248, 250), (10, 13, 21), (255, 215, 83)
# The only source of the common spoken and displayed CTA.
CTA_SAY = "여러분의 생각은 어떠신지 댓글로 남겨 주세요. 좋아요와 구독도 부탁드려요."
CTA_COMMENT_SAY = "여러분의 생각을 댓글로 남겨 주세요."
CTA_SHOW = ["여러분의 생각을 댓글로 남겨 주세요", "좋아요와 구독도 부탁드려요"]
SETTINGS_PATH = Path(__file__).with_name("render_settings.json")
DEFAULT_SETTINGS = {
    "schema_version": 2,
    "safe_margins": {"left": 72, "right": 132, "top": 96, "bottom": 220},
    "art_top": 300, "art_bottom": 1520,
    "caption_top": 1555, "caption_bottom": 1700,
    "zoom_amount": 0, "shake_pixels": 0,
    "image_transition": "crossfade", "image_transition_ms": 200,
    "attack_reaction_transition": "cut",
    "subtitle_size": 61, "subtitle_min_size": 47,
    "header_size": 70, "border_width": 14,
    "crf": 20, "preset": "fast", "bgm_volume": 0.10,
}
FONT_CANDIDATES = [
    "C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=32)
def font(size):
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, int(size))
    raise RuntimeError("한글 글꼴을 찾지 못했어요. FONT_CANDIDATES에 실제 글꼴을 지정하세요.")


LONGFORM_SETTINGS = {
    **DEFAULT_SETTINGS,
    "format": "longform",
    "safe_margins": {"left": 96, "right": 96, "top": 36, "bottom": 64},
    "art_top": 0, "art_bottom": 1080,
    "caption_top": 856, "caption_bottom": 1016,
    "subtitle_size": 56, "subtitle_min_size": 42, "header_size": 48,
    "border_width": 8,
}


def settings_for(path=SETTINGS_PATH, cfg=None):
    if isinstance(path, dict):
        cfg, path = path, SETTINGS_PATH
    longform = is_longform(cfg or {})
    spec = video_spec(cfg or {})
    width, height = spec['width'], spec['height']
    settings = dict(LONGFORM_SETTINGS if longform else DEFAULT_SETTINGS)
    if Path(path).exists():
        supplied = load_json(path)
        if longform:
            # Keep portrait coordinates out of the horizontal render branch.
            shared = ('crf', 'preset', 'bgm_volume', 'zoom_amount', 'shake_pixels',
                      'image_transition', 'image_transition_ms', 'attack_reaction_transition')
            settings.update({key: supplied[key] for key in shared if key in supplied})
            settings.update(supplied.get('longform', {}))
        else:
            settings.update(supplied)
    margins = settings["safe_margins"]
    if any(not isinstance(margins.get(k), int) or margins[k] < 0
           for k in ("left", "right", "top", "bottom")):
        raise ValueError("safe_margins에는 0 이상의 정수 픽셀이 필요해요.")
    if margins["left"] + margins["right"] >= width - 400:
        raise ValueError("자막 안전영역의 폭이 너무 좁아요.")
    if settings["zoom_amount"] != 0 or settings["shake_pixels"] != 0:
        raise ValueError("사진 고정 방식에서는 확대와 흔들림 설정이 0이어야 해요.")
    if (settings["image_transition"] != "crossfade"
            or settings["attack_reaction_transition"] != "cut"
            or isinstance(settings["image_transition_ms"], bool)
            or not isinstance(settings["image_transition_ms"], (int, float))
            or not 180 <= settings["image_transition_ms"] <= 220):
        raise ValueError("사진 전환은 180~220ms 교차전환, 공격→반응은 하드컷이어야 해요.")
    if longform:
        if not (0 <= settings['art_top'] < settings['art_bottom'] <= height
                and margins['top'] + 100 < settings['caption_top']
                < settings['caption_bottom'] <= height - margins['bottom']):
            raise ValueError('롱폼 가로 화면의 제목·자막 안전영역을 확인하세요.')
    elif not (margins["top"] < settings["art_top"] < settings["art_bottom"]
              < settings["caption_top"] < settings["caption_bottom"] <= height - margins["bottom"]):
        raise ValueError("상단 제목·그림·자막·하단 안전영역이 겹쳐요.")
    if longform:
        for key in ("show_header", "show_overlay_bands", "white_captions"):
            if key in settings and type(settings[key]) is not bool:
                raise ValueError(f"{key} must be a boolean")
        outline = settings.get("text_outline_width", 4)
        if type(outline) is not int or not 0 <= outline <= 4:
            raise ValueError("text_outline_width must be an integer from 0 to 4")
        overrides = settings.get("header_image_overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError("header_image_overrides must map episode images to title and panel boxes")
        allowed_images = set(image_names(cfg or {}))
        for name, override in overrides.items():
            if name not in allowed_images or not isinstance(override, dict):
                raise ValueError("Header override requires an image declared by this episode")
            for key in ("title_box", "panel_box"):
                box = override.get(key)
                if (not isinstance(box, (list, tuple)) or len(box) != 4
                        or any(type(x) is not int for x in box)
                        or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
                    raise ValueError("Header override boxes must lie within the video frame")
            title, panel = override["title_box"], override["panel_box"]
            if not (panel[0] <= title[0] and panel[1] <= title[1]
                    and title[2] <= panel[2] and title[3] <= panel[3]):
                raise ValueError("Header title box must fit inside its panel")
    return settings


def wrap_pixels(draw, text, size, max_width, stroke=4):
    """Keep whitespace-delimited words intact; split only an oversized word."""
    def fits(value):
        bounds = draw.textbbox((0, 0), value, font=font(size), stroke_width=stroke, anchor="lt")
        return bounds[2] - bounds[0] <= max_width

    lines = []
    for paragraph in str(text).split("\n"):
        line = ""
        for word in paragraph.split():
            trial = (line + " " + word) if line else word
            if fits(trial):
                line = trial
                continue
            if line:
                lines.append(line)
                line = ""
            if fits(word):
                line = word
                continue
            # Long strings with no spaces still need a safe fallback. Ordinary
            # Korean endings remain attached to their word above.
            for char in word:
                trial = line + char
                if line and not fits(trial):
                    lines.append(line)
                    line = char
                else:
                    line = trial
        lines.append(line)
    return lines


def layout_text(draw, text, box, preferred_size=61, min_size=47, stroke=4, gap=16, max_lines=None):
    left, top, right, bottom = box
    for size in range(preferred_size, min_size - 1, -1):
        lines = wrap_pixels(draw, text, size, right - left, stroke)
        if max_lines is not None and len(lines) > max_lines:
            continue
        metrics = [draw.textbbox((0, 0), line or " ", font=font(size),
                                 stroke_width=stroke, anchor="lt") for line in lines]
        heights = [max(1, b[3] - b[1]) for b in metrics]
        height = sum(heights) + gap * (len(lines) - 1)
        if height > bottom - top or any(b[2] - b[0] > right - left for b in metrics):
            continue
        y = top + (bottom - top - height) // 2
        result = []
        for line, bounds, line_height in zip(lines, metrics, heights):
            width = bounds[2] - bounds[0]
            x = left + (right - left - width) // 2
            result.append({"text": line, "xy": [x - bounds[0], y - bounds[1]],
                           "bounds": [x, y, x + width, y + line_height], "size": size})
            y += line_height + gap
        return result
    raise ValueError(f"읽을 수 있는 최소 글자 크기로 자막이 안전영역에 들어가지 않아요: {text}")


def draw_layout(draw, layout, fill=WHITE, stroke=4):
    for line in layout:
        draw.text(tuple(line["xy"]), line["text"], font=font(line["size"]), fill=fill,
                  stroke_width=stroke, stroke_fill=BLACK, anchor="lt")


def validate_timeline(cfg, timeline, base, silent=False):
    """Errors only; independent from visual/listening approval."""
    errors = []
    base = Path(base)
    if cfg.get("schema_version") != 2 or timeline.get("schema_version") != 2:
        return ["새 제작은 schema_version=2가 필요해요. 구버전을 자동 대체하지 않아요."]
    if not is_longform(cfg) and cfg.get("winner") != "none":
        errors.append("winner는 none이어야 해요.")
    if timeline.get("fps") != FPS:
        errors.append("timeline의 fps는 30이어야 해요.")
    mode = "silent" if silent else "voice"
    if timeline.get("audio_mode") != mode:
        errors.append("timeline 음성 모드와 실행 모드가 달라요. 무음은 --silent로 명시해야 해요.")
    minimum_duration, maximum_duration = duration_limits(cfg)
    total_frames = timeline.get("total_frames")
    if not isinstance(total_frames, int) or not minimum_duration * FPS <= total_frames <= maximum_duration * FPS:
        errors.append(f"현재 제작 프로필의 영상 길이는 {minimum_duration}~{maximum_duration}초여야 해요. 대본과 음성을 조정하세요.")
    elif abs(timeline.get("duration", -1) - total_frames / FPS) > 1e-6:
        errors.append("timeline의 재생 시간과 전체 프레임 수가 달라요.")
    if timeline.get("script_hash") != script_hash(cfg):
        errors.append("대본이 바뀌었어요. 음성과 timeline을 다시 생성하세요.")
    expected = speech_segments(cfg)
    segments = timeline.get("segments", [])
    if len(segments) != len(expected):
        errors.append("timeline에 필요한 대사/장면이 빠졌거나 추가됐어요.")
    cursor = 0
    for expected_segment, actual in zip(expected, segments):
        sid = expected_segment["id"]
        if any(actual.get(key) != value for key, value in expected_segment.items()):
            errors.append(f"{sid}: 대본·이미지·자막과 timeline 연결이 달라요.")
        start, end = actual.get("start_frame"), actual.get("end_frame")
        if not isinstance(start, int) or not isinstance(end, int) or start != cursor or end <= start:
            errors.append(f"{sid}: 프레임 간격이 겹치거나 비어 있어요.")
            continue
        cursor = end
        if abs(actual.get("start", -1) - start / FPS) > 1e-6 or abs(actual.get("end", -1) - end / FPS) > 1e-6:
            errors.append(f"{sid}: 초와 프레임 시간표가 달라요.")
        speech_start, speech_end = actual.get("speech_start"), actual.get("speech_end")
        if silent:
            if speech_start is not None or speech_end is not None:
                errors.append(f"{sid}: 무음 시간표에는 실제 발화 경계를 기록하지 않아요.")
        elif not isinstance(speech_start, (int, float)) or not isinstance(speech_end, (int, float)):
            errors.append(f"{sid}: 실제 발화 경계가 없어요.")
        elif not start / FPS <= speech_start <= speech_end <= end / FPS + 1e-6:
            errors.append(f"{sid}: 발화가 장면 시간을 벗어나요.")
        image = expected_segment.get("image")
        if image:
            try:
                if not safe_child(base, image).is_file():
                    errors.append(f"{sid}: {image}가 없어요. 대표 그림으로 대체하지 않아요.")
            except (ValueError, TypeError):
                errors.append(f"{sid}: 안전하지 않은 그림 경로예요.")
    if cursor != total_frames:
        errors.append("마지막 장면과 전체 프레임 수가 달라요.")
    for who in ("a", "b"):
        name = cfg[who].get("image", who + '.png') if is_longform(cfg) else cfg[who].get('image')
        if not name or not safe_child(base, name).is_file():
            errors.append(f"{who}: 인물 카드 그림이 없어요.")
    if not silent:
        pcm = timeline.get("pcm_path")
        if not pcm or not safe_child(base, pcm).is_file():
            errors.append("현재 음성 PCM 파일이 없어요. 무음으로 완성하지 않아요.")
        elif sha256_file(safe_child(base, pcm)) != timeline.get("pcm_sha256"):
            errors.append("음성 PCM 해시가 달라요. timeline을 다시 생성하세요.")
    return errors


def caption_start(segment):
    return segment["start"] if segment.get("speech_start") is None else segment["speech_start"]


def caption_end(segment):
    return segment["end"] if segment.get("speech_end") is None else segment["speech_end"]


def photo_transition_frames(previous, segment, settings):
    """Only a changed photo dissolves; attack/reaction and card cuts stay crisp."""
    before = previous.get("image") if previous else None
    after = segment.get("image")
    if not before or not after or before == after:
        return 0
    attack_reaction = (str(before).endswith("_atk.png") and str(after).endswith("_react.png"))
    same_edge = (previous.get("kind") == segment.get("kind") == "edge"
                 and previous.get("condition") == segment.get("condition"))
    action_cut = (settings.get('format') == 'longform' and
                  (segment.get('transition') in ('cut', 'hardcut')
                   or (previous.get('scene_id') == segment.get('scene_id')
                       and (previous.get('kind') in ('action', 'edge', 'clash')
                            or segment.get('kind') in ('action', 'edge', 'clash')))))
    if attack_reaction or same_edge or action_cut:
        return 0
    # Quantize inside the requested 180–220ms band. At 30fps only six
    # frames fit; rounding 180/220ms independently would give 167/233ms.
    frames = max(math.ceil(.18 * FPS), min(round(settings["image_transition_ms"] * FPS / 1000),
                                          math.floor(.22 * FPS)))
    return frames if segment["end_frame"] - segment["start_frame"] >= frames else 0


class Episode:
    """Static contained photos; only the photo layer can crossfade at a cut."""
    def __init__(self, cfg, base, settings=None):
        self.cfg, self.base = cfg, Path(base)
        self.settings = settings or settings_for(cfg=cfg)
        self.longform = is_longform(cfg)
        spec = video_spec(cfg)
        self.width, self.height = spec['width'], spec['height']
        self._last_frame_key, self._last_frame = None, None
        self.images = {}
        self._image_cache = OrderedDict()
        self.visual_plan = VisualShotPlan(self.settings.get("visual_shots", []), set(image_names(cfg))) if self.longform else None
        self.shot_caption_layouts = {}
        names = ({cfg[who].get('image', who + '.png') for who in ('a', 'b')} if self.longform
                 else {cfg['a']['image'], cfg['b']['image']})
        names.update(s["image"] for s in speech_segments(cfg) if s["image"])
        for name in names:
            if self.longform:
                self.images[name] = None
            else:
                with Image.open(safe_child(self.base, name)) as image:
                    self.images[name] = ImageOps.exif_transpose(image).convert("RGB")
        m = self.settings["safe_margins"]
        self.left, self.right = m["left"], self.width - m["right"]
        self.art_box = ((0, self.settings['art_top'], self.width, self.settings['art_bottom'])
                        if self.longform else (30, self.settings['art_top'], W - 30, self.settings['art_bottom']))
        self.caption_box = (self.left, self.settings["caption_top"], self.right, self.settings["caption_bottom"])
        self.background = Image.new("RGB", (self.width, self.height), BLACK)
        self.layouts = {}
        self._art_cache = OrderedDict()
        self._photo_cache = OrderedDict()
        draw = ImageDraw.Draw(self.background)
        for segment in speech_segments(cfg):
            self.layouts[segment["id"]] = layout_text(
                draw, segment["display_text"], self.caption_box,
                self.settings["subtitle_size"], self.settings["subtitle_min_size"], max_lines=2)

    def _image_for(self, name):
        if not self.longform:
            return self.images[name]
        if self.images[name] is None:
            with Image.open(safe_child(self.base, name)) as image:
                self.images[name] = ImageOps.exif_transpose(image).convert('RGB')
        self._image_cache[name] = True
        self._image_cache.move_to_end(name)
        while len(self._image_cache) > 4:
            evicted, _ = self._image_cache.popitem(last=False)
            self.images[evicted] = None
        return self.images[name]

    def _remember(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        if self.longform:
            while len(cache) > 4:
                cache.popitem(last=False)
        return value

    def contained_art(self, name, progress=0.0, moving=False, shake=0.0, box=None):
        # Keep the former call signature for preview callers. Motion arguments
        # deliberately cannot restart a zoom or move artwork outside its box.
        box = box or self.art_box
        left, top, right, bottom = box
        margin = 2
        key = (name, tuple(box))
        if key not in self._art_cache:
            max_size = (right - left - 2 * margin, bottom - top - 2 * margin)
            self._remember(self._art_cache, key, ImageOps.contain(self._image_for(name), max_size, Image.Resampling.LANCZOS))
        self._art_cache.move_to_end(key)
        art = self._art_cache[key]
        x = left + (right - left - art.width) // 2
        y = top + (bottom - top - art.height) // 2
        return art, (x, y)

    def card(self, frame):
        for who, box in (("a", (150, 300, 930, 865)), ("b", (150, 920, 930, 1485))):
            art, pos = self.contained_art(self.cfg[who]["image"], 1.0, False, box=box)
            frame.paste(art, pos)
        draw = ImageDraw.Draw(frame)
        draw_layout(draw, layout_text(draw, "VS", (440, 865, 640, 920), 45, 40), YELLOW)

    def header_content(self, segment):
        kind = segment["kind"]
        color = WHITE
        who = segment.get("condition")
        if who not in ("a", "b"):
            who = next((side for side in ("a", "b")
                        if segment["id"].startswith(side + ".intro.")), None)
        if kind == "intro" and who in ("a", "b"):
            title = self.cfg[who]["name"]
            color = COL_A if who == "a" else COL_B
        elif kind == "edge" and segment.get("condition") in ("a", "b"):
            who = segment["condition"]
            # V5 keeps the A/B palette for the exchange initiator, while the
            # title describes an exchange rather than an alternate advantage.
            fallback = (("첫 공방" if who == "a" else "두 번째 공방")
                        if self.cfg.get("production_profile") == DETAIL_PROFILE
                        else self.cfg[who]["name"] + "의 조건")
            title = self.cfg[who].get("edge", {}).get("title", fallback)
            color = COL_A if who == "a" else COL_B
        else:
            title = self.cfg["a"]["name"] + " vs " + self.cfg["b"]["name"]
        if segment.get("header"):
            title = segment["header"]
        # Only the scene title is shown. Source/fiction notes belong in the
        # description and relevant narration, not a repeated header banner.
        return title, "", color

    def header(self, draw, segment):
        if self.longform and not self.settings.get("show_header", True):
            return
        title, sub, color = self.header_content(segment)
        override = (self.settings.get("header_image_overrides", {}).get(segment.get("image"))
                    if self.longform else None)
        top = self.settings["safe_margins"]["top"]
        box = tuple(override["title_box"]) if override else (self.left, top, self.right, top + 95)
        draw_layout(draw, layout_text(draw, title, box, self.settings["header_size"], 44,
                                      max_lines=1 if override else None), color)

    def photo_layer(self, name, box=None):
        box = box or self.art_box
        key = (name, tuple(box))
        if key in self._photo_cache:
            self._photo_cache.move_to_end(key)
            return self._photo_cache[key]
        left, top, right, bottom = box
        layer = Image.new("RGB", (right - left, bottom - top), BLACK)
        art, (x, y) = self.contained_art(name, box=box)
        layer.paste(art, (x - left, y - top))
        self._remember(self._photo_cache, key, layer)
        return layer

    def planned_photo(self, shot):
        crop = shot.get("crop")
        if crop is None:
            return self.photo_layer(shot["image"])
        key = ("planned_crop", shot["image"], tuple(crop))
        if key not in self._photo_cache:
            source = self._image_for(shot["image"])
            if crop[2] > source.width or crop[3] > source.height:
                raise ValueError("Visual crop exceeds the actual source image")
            fitted = ImageOps.fit(source.crop(tuple(crop)),
                (self.art_box[2] - self.art_box[0], self.art_box[3] - self.art_box[1]),
                method=Image.Resampling.LANCZOS)
            self._remember(self._photo_cache, key, fitted)
        self._photo_cache.move_to_end(key)
        return self._photo_cache[key]

    def longform_frame(self, segment, index, previous=None):
        if not segment.get('image'):
            raise ValueError('롱폼의 첫 장면과 각 컷에는 실제 연결 이미지가 필요해요.')
        shot_index, shot = self.visual_plan.at(index)
        transition = (self.visual_plan.dissolve_frames(shot_index) if shot is not None
                      else photo_transition_frames(previous, segment, self.settings))
        offset = index - (shot['start_frame'] if shot is not None else segment['start_frame'])
        caption_visible = index / FPS + 1e-9 >= caption_start(segment)
        key = (segment['id'], shot_index, offset if 0 <= offset < transition else -1, caption_visible)
        if key == self._last_frame_key:
            return self._last_frame
        frame = self.background.copy()
        art = self.planned_photo(shot) if shot is not None else self.photo_layer(segment['image'])
        if transition > 1 and 0 <= offset < transition:
            previous_art = (self.planned_photo(self.visual_plan.shots[shot_index - 1])
                            if shot is not None else self.photo_layer(previous['image']))
            art = Image.blend(previous_art, art, offset / (transition - 1))
        frame.paste(art, self.art_box[:2])
        # Only artwork dissolves; horizontal text overlays remain stable.
        bands = ImageDraw.Draw(frame, 'RGBA')
        header_override = self.settings.get("header_image_overrides", {}).get(segment.get("image"))
        header_panel = tuple(header_override["panel_box"]) if header_override else (0, 0, self.width, 136)
        show_bands = self.settings.get("show_overlay_bands", True)
        if show_bands:
            if self.settings.get("show_header", True):
                bands.rectangle(header_panel, fill=(*BLACK, 190))
            bands.rectangle((0, self.settings['caption_top'] - 22, self.width, self.height), fill=(*BLACK, 210))
        draw = ImageDraw.Draw(frame)
        self.header(draw, segment)
        outline = self.settings.get("text_outline_width", 4)
        if segment.get('cta_overlay'):
            box = (self.left, 698, self.right, 820)
            if show_bands:
                bands.rectangle((0, 684, self.width, 834), fill=(*BLACK, 210))
            draw_layout(draw, layout_text(draw, segment['cta_overlay'], box, 42, 38, max_lines=2),
                        WHITE, stroke=outline)
        if not segment.get('caption_in_overlay') and caption_visible:
            color = (WHITE if self.settings.get("white_captions", False)
                     else YELLOW if segment['kind'] in ('hook', 'question') else WHITE)
            layout = self.layouts[segment['id']]
            if shot is not None and shot.get('caption_box'):
                layout_key = segment['id'] + ':' + str(shot_index)
                if layout_key not in self.shot_caption_layouts:
                    self.shot_caption_layouts[layout_key] = layout_text(draw, segment['display_text'],
                        tuple(shot['caption_box']), self.settings['subtitle_size'],
                        self.settings['subtitle_min_size'], max_lines=2)
                layout = self.shot_caption_layouts[layout_key]
            draw_layout(draw, layout, color, stroke=outline)
        self._last_frame_key, self._last_frame = key, frame
        return frame

    def frame(self, segment, index, previous=None):
        if self.longform:
            return self.longform_frame(segment, index, previous)
        frame = self.background.copy()
        overlay = segment.get('cta_overlay')
        art_box = (30, self.settings['art_top'], W - 30, 1300) if overlay else self.art_box
        if segment["image"]:
            art = self.photo_layer(segment["image"], art_box)
            transition = photo_transition_frames(previous, segment, self.settings)
            offset = index - segment["start_frame"]
            if transition > 1 and 0 <= offset < transition:
                art = Image.blend(self.photo_layer(previous["image"], art_box), art, offset / (transition - 1))
            frame.paste(art, art_box[:2])
        else:
            self.card(frame)
            if overlay:
                # Keep the full cards visible above the separate text-only CTA.
                cards = ImageOps.contain(frame.crop((0, 300, W, 1500)), (W, 1000), Image.Resampling.LANCZOS)
                frame.paste(BLACK, (0, 300, W, 1520))
                frame.paste(cards, ((W - cards.width) // 2, 300))
        draw = ImageDraw.Draw(frame)
        self.header(draw, segment)
        if overlay:
            draw_layout(draw, layout_text(draw, overlay, (self.left, 1340, self.right, 1495),
                                          45, 38, max_lines=2), WHITE)
        if segment["kind"] in ("edge", "story") and segment.get("condition") in ("a", "b"):
            color = COL_A if segment["condition"] == "a" else COL_B
            draw.rectangle((15, 282, W - 16, self.settings["art_bottom"] + 10), outline=color,
                           width=self.settings["border_width"])
        if not segment.get('caption_in_overlay') and index / FPS + 1e-9 >= caption_start(segment):
            draw_layout(draw, self.layouts[segment["id"]], YELLOW if segment["kind"] in ("hook", "question") else WHITE)
        return frame


def review_samples(timeline, settings=None):
    """Every caption and cut, plus each photo dissolve's start/middle/end."""
    settings = settings or settings_for()
    samples = {}
    def add(n, label):
        n = max(0, min(timeline["total_frames"] - 1, int(n)))
        samples.setdefault(n, []).append(label)
    add(0, "시작")
    add(timeline["total_frames"] - 1, "끝")
    previous = None
    for segment in timeline["segments"]:
        start, end, sid = segment["start_frame"], segment["end_frame"], segment["id"]
        add(start - 1, sid + " 전환 직전")
        add(start, sid + " 전환 직후")
        add(max(start, round((caption_start(segment) + caption_end(segment)) * FPS / 2)), sid + " 자막")
        add(end - 1, sid + " 고정 사진 끝")
        transition = photo_transition_frames(previous, segment, settings)
        if transition > 1:
            add(start, sid + " 사진 교차전환 시작")
            add(start + transition // 2, sid + " 사진 교차전환 중간")
            add(start + transition - 1, sid + " 사진 교차전환 끝")
        add(min(end - 1, start + max(1, transition)), sid + " 전환 후 고정")
        previous = segment
    if settings.get('format') == 'longform' and settings.get('visual_shots'):
        for i, shot in enumerate(settings['visual_shots']):
            start, end = shot['start_frame'], shot['end_frame']
            for n, suffix in ((start, '시작'), ((start + end - 1)//2, '중간'), (end-1, '끝')):
                add(n, f"화면숏 {i+1} {suffix}")
            if i and shot.get('transition') == 'crossfade':
                add(start + 3, f"화면숏 {i+1} 교차전환 중간")
    return [{"frame": n, "time": n / FPS, "labels": labels} for n, labels in sorted(samples.items())]


def balanced_frame_selection(frames):
    """Keep FFmpeg expression recursion logarithmic for longform review samples."""
    terms = [f"eq(n\\,{int(frame)})" for frame in frames]
    if not terms:
        raise ValueError("At least one review frame is required")
    def combine(start, end):
        if end - start == 1:
            return terms[start]
        middle = (start + end) // 2
        return "(" + combine(start, middle) + "+" + combine(middle, end) + ")"
    return combine(0, len(terms))


def extract_reviews(video, timeline, out, video_hash, settings=None, cfg=None):
    review_dir = out / ("review_" + video_hash[:12])
    review_dir.mkdir(exist_ok=True)
    samples = review_samples(timeline, settings)
    expression = balanced_frame_selection(s["frame"] for s in samples)
    filters = ['-vf', 'select=' + expression]
    if is_longform(cfg or {}):
        # Many longform cues can exceed Windows' command-line length limit.
        selection = review_dir / 'selection.ffscript'
        selection.write_text('select=' + expression, encoding='utf-8')
        filters = ['-filter_script:v', str(selection)]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), *filters,
                    "-fps_mode", "vfr", "-threads", "2", str(review_dir / "frame_%04d.png")], check=True)
    spec = video_spec(cfg or {})
    cols, tw, th, label_h = (6, 320, 180, 66) if is_longform(cfg or {}) else (5, 216, 384, 66)
    sheet = Image.new("RGB", (cols * tw, math.ceil(len(samples) / cols) * (th + label_h)), BLACK)
    draw = ImageDraw.Draw(sheet)
    for i, item in enumerate(samples):
        path = review_dir / f"frame_{i + 1:04d}.png"
        if not path.is_file():
            raise RuntimeError("MP4에서 필요한 검수 프레임을 추출하지 못했어요.")
        with Image.open(path) as image:
            if image.size != (spec['width'], spec['height']):
                raise RuntimeError("검수 프레임이 원본 해상도가 아니에요.")
            small = image.resize((tw, th), Image.Resampling.LANCZOS)
        x, y = (i % cols) * tw, (i // cols) * (th + label_h)
        sheet.paste(small, (x, y))
        label = f"{item['time']:.3f}s / f{item['frame']}\n" + item["labels"][0]
        layout = layout_text(draw, label, (x + 4, y + th + 3, x + tw - 4, y + th + label_h - 2), 18, 12, 1, 2)
        draw_layout(draw, layout, WHITE, 1)
        item.update(file=path.relative_to(out).as_posix(), sha256=sha256_file(path))
    sheet.save(out / "_review.png")
    atomic_json(out / "review_frames.json", {"video_sha256": video_hash, "frames": samples,
                                            "contact_sheet_sha256": sha256_file(out / "_review.png"),
                                            "status": "UNREVIEWED"})
    return samples


def technical_checks(video, timeline, silent=False, cfg=None):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    metadata = json.loads(result.stdout)
    streams = metadata.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    v = videos[0] if len(videos) == 1 else {}
    try:
        rate = float(Fraction(v.get("avg_frame_rate", "0")))
    except (ValueError, ZeroDivisionError):
        rate = 0
    actual_duration = float(metadata.get("format", {}).get("duration", 0))
    minimum_duration, maximum_duration = duration_limits(cfg or {})
    spec = video_spec(cfg or {})
    checks = {
        "one_video_stream": len(videos) == 1,
        "resolution": (v.get("width"), v.get("height")) == (spec['width'], spec['height']),
        "fps": abs(rate - FPS) < .001,
        "codec_h264": v.get("codec_name") == "h264",
        "pixel_format_yuv420p": v.get("pix_fmt") == "yuv420p",
        "duration_range": minimum_duration <= actual_duration <= maximum_duration,
        "duration_matches_timeline": abs(actual_duration - timeline["duration"]) <= .08,
        "frames_match_timeline": int(v.get("nb_frames", -1)) == timeline["total_frames"],
        "audio_mode": not audios if silent else len(audios) == 1 and audios[0].get("codec_name") == "aac",
        "audio_not_truncated": not audios if silent else len(audios) == 1 and
            abs(float(audios[0].get("duration", 0)) - timeline["duration"]) <= .08,
    }
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
    checks["full_decode"] = decoded.returncode == 0 and not decoded.stderr.strip()
    audio_identity = {}
    if is_longform(cfg or {}) and not silent:
        from check_episode import final_audio_sha256, audio_timing_hash
        audio_identity = {'final_audio_sha256': final_audio_sha256(video),
                          'audio_timing_hash': audio_timing_hash(timeline)}
    return {"checks": checks, "metadata": metadata, "decode_errors": decoded.stderr.strip(), **audio_identity,
            "duration": actual_duration, "passed": all(checks.values()),
            "production_profile": (cfg or {}).get("production_profile"),
            "duration_limits_seconds": [minimum_duration, maximum_duration]}


def write_subtitles(timeline, out):
    def clock(seconds):
        ms = round(seconds * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    blocks = []
    for i, segment in enumerate(timeline["segments"], 1):
        blocks.append(f"{i}\n{clock(caption_start(segment))} --> {clock(segment['end'])}\n{segment['display_text']}\n")
    (out / "captions.srt").write_text("\n".join(blocks), encoding="utf-8")


@contextmanager
def render_lock(out):
    path = out / ".render.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("이 편은 렌더링 중이거나 종료되지 않은 잠금이 있어요. 이력을 확인한 뒤 재개하세요.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started_at": utc_now()}, f)
        yield
    finally:
        path.unlink(missing_ok=True)


def render_episode(json_path, silent=False, settings_path=SETTINGS_PATH, *, authorized_extra_renders=0):
    if type(authorized_extra_renders) is not int or authorized_extra_renders < 0:
        raise ValueError("authorized_extra_renders must be a nonnegative integer")
    json_path = Path(json_path).resolve()
    base, cfg = json_path.parent, load_json(json_path)
    if is_longform(cfg):
        if silent:
            raise ValueError('롱폼은 전체 낭독과 최종 MP4 청취 검수가 필요하며 --silent를 지원하지 않아요.')
        from longform_profile import longform_production_errors
        approval_errors = longform_production_errors(cfg, base)
        if approval_errors:
            raise ValueError('\n'.join(approval_errors))
    timeline_path = base / "timeline.json"
    if not timeline_path.is_file():
        raise ValueError("timeline.json이 없어요. 현재 대본의 음성을 먼저 생성하세요.")
    timeline = load_json(timeline_path)
    errors = validate_timeline(cfg, timeline, base, silent)
    from make_voice import validate_voice
    errors.extend(validate_voice(cfg, base))
    if errors:
        raise ValueError("\n".join(errors))
    settings = settings_for(settings_path, cfg=cfg)
    if is_longform(cfg):
        if (settings.get('visual_shots') and
                settings.get('visual_shots_timeline_sha256') != sha256_file(timeline_path)):
            raise ValueError('Visual shots must be resolved from the current measured timeline hash')
        VisualShotPlan(settings.get('visual_shots') or timeline_picture_shots(timeline),
                       set(image_names(cfg)), timeline['total_frames'])
    ep = Episode(cfg, base, settings)
    declared_images = image_names(cfg) if is_longform(cfg) else ep.images
    images = {name: sha256_file(safe_child(base, name)) for name in declared_images}
    inputs = {"script_hash": script_hash(cfg), "timeline_sha256": sha256_file(timeline_path),
              "image_hashes": images, "settings_hash": object_hash(settings),
              "renderer_sha256": sha256_file(Path(__file__)),
              "visual_planner_sha256": sha256_file(Path(__file__).with_name("longform_visuals.py")),
              "audio_mode": timeline["audio_mode"],
              "pcm_sha256": timeline.get("pcm_sha256"), "voice_input_hash": timeline.get("input_hash")}
    bgm = None
    if cfg.get("bgm") and not silent:
        rights = cfg.get("bgm_rights", {})
        if rights.get("verified") is not True or not rights.get("source") or not rights.get("license"):
            raise ValueError("BGM 사용 권한의 확인 기록이 없어요. 기본 BGM은 비워 두세요.")
        bgm = safe_child(base, cfg["bgm"])
        inputs["bgm_sha256"] = sha256_file(bgm)
    render_hash = object_hash(inputs)
    out = base / "out"
    out.mkdir(exist_ok=True)
    video = out / "video.mp4"
    with render_lock(out):
        history_path = out / "render_history.json"
        history = load_json(history_path) if history_path.exists() else {"attempts": []}
        attempts = history["attempts"]
        prior = next((entry for entry in attempts if entry["input_hash"] == render_hash), None)
        if prior and prior["status"] == "SUCCEEDED" and video.is_file() and sha256_file(video) == prior.get("video_sha256"):
            print("현재 입력과 일치하는 렌더가 있어요: " + str(video))
            return video
        executions = sum(entry.get("executions", 1) for entry in attempts)
        if executions >= 3 + authorized_extra_renders:
            raise RuntimeError("렌더 실행 한도(최초 + 재시도/수정 2회)에 도달했어요. NEEDS_REVIEW로 남기세요.")
        if prior is None:
            prior = {"input_hash": render_hash, "started_at": utc_now(), "status": "STARTED", "executions": 0}
            attempts.append(prior)
        prior.update(status="STARTED", last_attempt_at=utc_now(), executions=prior.get("executions", 1) + 1,
                     authorized_extra_renders=authorized_extra_renders)
        atomic_json(history_path, history)
        partial = out / ".video.rendering.mp4"
        command = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pixel_format", "rgb24",
                   "-video_size", f"{ep.width}x{ep.height}", "-framerate", str(FPS), "-i", "pipe:0"]
        if not silent:
            command += ["-i", str(safe_child(base, timeline["pcm_path"]))]
            if bgm:
                command += ["-stream_loop", "-1", "-i", str(bgm), "-filter_complex",
                            f"[2:a]volume={settings['bgm_volume']}[bg];[1:a][bg]amix=inputs=2:duration=first:normalize=0[aout]",
                            "-map", "0:v:0", "-map", "[aout]"]
            else:
                command += ["-map", "0:v:0", "-map", "1:a:0"]
            command += ["-c:a", "aac", "-b:a", "160k"]
        else:
            command += ["-an"]
        command += ["-c:v", "libx264", "-preset", str(settings["preset"]), "-crf", str(settings["crf"]),
                    "-pix_fmt", "yuv420p", "-frames:v", str(timeline["total_frames"]),
                    "-movflags", "+faststart", str(partial)]
        process = None
        try:
            with (out / "render_stderr.log").open("wb") as stderr:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=stderr)
                try:
                    previous = None
                    for segment in timeline["segments"]:
                        for index in range(segment["start_frame"], segment["end_frame"]):
                            process.stdin.write(ep.frame(segment, index, previous).tobytes())
                        previous = segment
                    process.stdin.close()
                    if process.wait() != 0:
                        raise RuntimeError("ffmpeg 렌더 실패: out/render_stderr.log를 확인하세요.")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            technical = technical_checks(partial, timeline, silent, cfg=cfg)
            if not technical["passed"]:
                atomic_json(out / "failed_technical_qa.json", {**inputs, **technical})
                raise RuntimeError("MP4 기술 검사 실패: out/failed_technical_qa.json을 확인하세요.")
            if video.exists():
                revisions = base / "revisions"
                revisions.mkdir(exist_ok=True)
                archive = revisions / ("video_" + sha256_file(video)[:16] + ".mp4")
                if not archive.exists():
                    shutil.copy2(video, archive)
            os.replace(partial, video)
            video_hash = sha256_file(video)
            extract_reviews(video, timeline, out, video_hash, settings, cfg=cfg)
            write_subtitles(timeline, out)
            atomic_json(out / "layout_boxes.json", {"input_hash": render_hash, "settings": settings,
                                                    "caption_layouts": ep.layouts,
                                                    "shot_caption_layouts": ep.shot_caption_layouts})
            atomic_json(out / "technical_qa.json", {
                **inputs, **technical, "status": "PASS", "input_hash": render_hash, "video_sha256": video_hash,
                "checked_at": utc_now(), "visual_review": "UNREVIEWED",
                "audio_review": "N/A" if silent else "AUDIO_REVIEW_REQUIRED",
                "review_frames_sha256": sha256_file(out / "review_frames.json"), "settings": settings,
            })
            prior.update(status="SUCCEEDED", finished_at=utc_now(), video_sha256=video_hash)
            atomic_json(history_path, history)
        except Exception as exc:
            prior.update(status="FAILED", error=str(exc), finished_at=utc_now())
            atomic_json(history_path, history)
            raise
    print(f"검수용 영상: {video} ({timeline['duration']:.3f}초). 시각·청취 검수는 별도로 필요해요.")
    print("검수 콘택트시트: " + str(out / "_review.png"))
    return video


render = render_episode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episodes", nargs="+")
    parser.add_argument("--silent", action="store_true", help="명시적인 무음 제작; 음성/BGM을 사용하지 않음")
    args = parser.parse_args()
    for path in args.episodes:
        render_episode(path, silent=args.silent)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
