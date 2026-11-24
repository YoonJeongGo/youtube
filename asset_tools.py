"""Save generated assets without distortion and prepare honest visual-review views.

This utility does not generate images, locate hands/faces automatically, or grant
visual PASS. A Codex session must inspect the source and choose actual regions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from PIL import Image, ImageDraw, ImageOps

from image_review_contract import generation_metadata, prepare_prompt, review_template

from episode_core import (ADDITIONAL_EDGE_IMAGE_NAMES, IMAGE_NAMES, SETUP_IMAGE_NAMES, atomic_json,
                          load_json, sha256_file, image_names, is_longform, safe_child, video_spec)


def now():
    return datetime.now(timezone.utc).isoformat()


def image_target(name, cfg=None):
    if is_longform(cfg or {}):
        if name not in image_names(cfg):
            raise ValueError('Longform assets must be declared in the current episode: ' + name)
        if name in {cfg[who].get('image', who + '.png') for who in ('a', 'b')}:
            return 1024, 1024
        spec = video_spec(cfg)
        return spec['width'], spec['height']
    supported = tuple(dict.fromkeys((*IMAGE_NAMES, *SETUP_IMAGE_NAMES, *ADDITIONAL_EDGE_IMAGE_NAMES,
                                    'cliffhanger.png', 'sword_clash.png')))
    if name not in supported:
        raise ValueError("Use one of the fixed episode image names: " + ", ".join(supported))
    return (1024, 1024) if name in ("a.png", "b.png") else (1024, 1536)


def logical_asset_ids(name, cfg):
    """Stable storyboard identities keep rename operations inside retry history."""
    if not is_longform(cfg or {}):
        return []
    identities = []
    if name == cfg.get('thumb', 'thumb.png'):
        identities.append('thumbnail')
    identities.extend('portrait:' + who for who in ('a', 'b')
                      if name == cfg[who].get('image', who + '.png'))
    identities.extend('cut:' + cut['id'] for cut in cfg['cuts'] if cut['image'] == name)
    return sorted(set(identities))


def normalize_asset(source, destination, *, prompt, references=(), generation_version=1,
                    generator="unrecorded", replace=False, episode_cfg=None, episode_base=None):
    """Proportional contain + padding only. Source and replaced versions are kept.

    Calling this function documents a transform; it never certifies that the
    transform preserves the intended composition. Review remains UNREVIEWED.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    records_root = Path(episode_base).resolve() if episode_base else destination.parent
    asset_name = destination.relative_to(records_root).as_posix()
    if is_longform(episode_cfg or {}):
        from longform_profile import longform_production_errors
        errors = longform_production_errors(episode_cfg, records_root)
        if errors:
            raise ValueError('\n'.join(errors))
        if safe_child(records_root, asset_name).resolve() != destination:
            raise ValueError('Asset destination must stay inside its episode')
    if source == destination:
        raise ValueError("The generated original and fixed destination must be different paths")
    if not source.is_file():
        raise FileNotFoundError(source)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("The actual complete generation prompt is required")
    if isinstance(generation_version, bool) or not isinstance(generation_version, int) or not 1 <= generation_version <= 4:
        raise ValueError("An image permits initial generation plus three revisions (versions 1~4)")
    if destination.exists() and not replace:
        raise FileExistsError("An asset already exists; an explicit replacement is required: " + str(destination))
    target = image_target(asset_name if episode_cfg else destination.name, episode_cfg)
    records_path = records_root / "generation_records.json"
    records = load_json(records_path) if records_path.exists() else {"schema_version": 1, "assets": []}
    identities = logical_asset_ids(asset_name, episode_cfg)
    prior = [item for item in records['assets']
             if item['file'] == asset_name or set(identities).intersection(item.get('asset_ids', []))]
    if prior and generation_version != max(item["generation_version"] for item in prior) + 1:
        raise ValueError("The next generation_version must advance the stored image history by one")
    references_info = [{"path": str(Path(ref).resolve()), "sha256": sha256_file(ref)} for ref in references]
    source_hash = sha256_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    originals = records_root / "revisions" / "generation_originals"
    originals.mkdir(parents=True, exist_ok=True)
    original = originals / f"{destination.stem}_v{generation_version}_{source_hash[:12]}{source.suffix.lower()}"
    if not original.exists():
        shutil.copy2(source, original)
    old_hash = None
    if destination.exists():
        old_hash = sha256_file(destination)
        old = records_root / "revisions" / f"{destination.stem}_replaced_{old_hash[:12]}.png"
        if not old.exists():
            shutil.copy2(destination, old)
    with Image.open(source) as opened:
        oriented = ImageOps.exif_transpose(opened)
        image = oriented.convert("RGBA") if "A" in oriented.getbands() else oriented.convert("RGB")
        before = image.size
        contained = ImageOps.contain(image, target, Image.Resampling.LANCZOS)
        padding = [(target[0] - contained.width) // 2, (target[1] - contained.height) // 2]
        transparent = image.mode == "RGBA"
        background = (10, 13, 21, 0) if transparent else (10, 13, 21)
        canvas = Image.new(image.mode, target, background)
        canvas.paste(contained, tuple(padding))
        temp = destination.with_name(destination.stem + ".normalizing.png")
        canvas.save(temp, format="PNG")
        temp.replace(destination)
    letterboxed = contained.size != target
    transform = "letterbox" if letterboxed else "none" if before == target else "proportional_resize"
    record = {
        "file": asset_name, "sha256": sha256_file(destination), "generation_version": generation_version,
        "saved_at": now(), "generator": generator, "prompt": prompt, "references": references_info,
        "source_path": str(source), "source_sha256": source_hash,
        "preserved_original": str(original.relative_to(records_root)), "replaced_sha256": old_hash,
        "source_size": list(before), "output_size": list(target), "contained_size": list(contained.size),
        "padding_left_top": padding, "transform": transform,
        "review_status": "UNREVIEWED", "needs_composition_review": True,
        "warning": "Aspect ratio differed: proportional letterboxing was applied; inspect framing before acceptance."
        if letterboxed else "Inspect the saved image and its final video framing; file conversion is not visual approval.",
    }
    record.update(generation_metadata(prompt))
    if identities:
        record['asset_ids'] = identities
    records["assets"].append(record)
    atomic_json(records_path, records)
    return record


def build_review_views(image_path, regions, output_dir=None):
    """Regions are explicit pixel boxes: {'face': [left,top,right,bottom], ...}.

    Regions must be chosen from observed anatomy, not guessed by this program.
    Individual crops retain native pixels; the overview sheet is only navigation.
    """
    image_path = Path(image_path).resolve()
    image_hash = sha256_file(image_path)
    output_dir = Path(output_dir) if output_dir else image_path.parent / "out" / "asset_review" / (image_path.stem + "_" + image_hash[:12])
    output_dir.mkdir(parents=True, exist_ok=True)
    if not isinstance(regions, dict):
        raise ValueError("regions must map observed anatomical labels to pixel bounds")
    items = []
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        full = output_dir / "full.png"
        image.save(full)
        items.append({"label": "full", "file": str(full.resolve()), "sha256": sha256_file(full), "bounds": [0, 0, image.width, image.height]})
        for index, (label, bounds) in enumerate(regions.items(), 1):
            if not isinstance(label, str) or not label.strip() or not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
                raise ValueError("Every crop needs a descriptive label and four pixel coordinates")
            if any(isinstance(value, bool) or not isinstance(value, int) for value in bounds):
                raise ValueError("Crop coordinates must be integer pixels")
            left, top, right, bottom = bounds
            if not 0 <= left < right <= image.width or not 0 <= top < bottom <= image.height:
                raise ValueError("Crop must stay inside the actual image: " + label)
            crop = output_dir / f"detail_{index:02d}.png"
            image.crop(tuple(bounds)).save(crop)
            items.append({"label": label, "file": str(crop.resolve()), "sha256": sha256_file(crop), "bounds": list(bounds)})
    tile_w, tile_h, label_h, cols = 512, 512, 70, 2
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile_w, rows * (tile_h + label_h)), (10, 13, 21))
    draw = ImageDraw.Draw(sheet)
    from make_vs import draw_layout, layout_text
    for i, item in enumerate(items):
        with Image.open(item["file"]) as opened:
            # No upscaling: the separate crop remains available at its native size.
            preview = ImageOps.contain(opened, (min(tile_w - 12, opened.width), min(tile_h - 12, opened.height)), Image.Resampling.LANCZOS)
        x, y = (i % cols) * tile_w, (i // cols) * (tile_h + label_h)
        sheet.paste(preview, (x + (tile_w - preview.width) // 2, y + (tile_h - preview.height) // 2))
        label = item["label"] + " · " + str(item["bounds"])
        draw_layout(draw, layout_text(draw, label, (x + 8, y + tile_h, x + tile_w - 8, y + tile_h + label_h - 8), 24, 16, 1, 4), stroke=1)
    sheet_path = output_dir / "details_overview.png"
    sheet.save(sheet_path)
    result = {"schema_version": 1, "image": str(image_path), "image_sha256": image_hash,
              "created_at": now(), "status": "UNREVIEWED", "views": items,
              "contact_sheet": str(sheet_path.resolve()), "contact_sheet_sha256": sha256_file(sheet_path),
              "detail_regions_supplied": bool(regions),
              "warning": "Open full.png and native detail crops with an image viewer. This tool neither observes anatomy nor grants PASS."}
    atomic_json(output_dir / "review_views.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepared = sub.add_parser("prepare-prompt", help="Append shared action/anatomy guidance before image generation")
    prepared.add_argument("--prompt-file", required=True)
    prepared.add_argument("--output", required=True, help="New complete prompt; no overwrite")
    template = sub.add_parser("review-template", help="Unreviewed current-hash draft for new or reused images")
    template.add_argument("image")
    template.add_argument("--episode", required=True)
    template.add_argument("--views-manifest")
    template.add_argument("--output", required=True, help="New draft; preserve existing image_review.json")
    normal = sub.add_parser("save")
    normal.add_argument("source")
    normal.add_argument("destination")
    normal.add_argument("--prompt-file", required=True)
    normal.add_argument("--reference", action="append", default=[])
    normal.add_argument("--version", type=int, default=1)
    normal.add_argument("--generator", default="unrecorded")
    normal.add_argument("--replace", action="store_true")
    normal.add_argument("--episode", help="Longform episode.json for declared horizontal assets and approval gate")
    review = sub.add_parser("review-views")
    review.add_argument("image")
    review.add_argument("--regions-json", help="JSON object of observed face/hand/weapon boxes")
    review.add_argument("--output-dir")
    args = parser.parse_args()
    if args.command in ("prepare-prompt", "review-template"):
        output = Path(args.output)
        if output.exists():
            raise FileExistsError("Existing prompt/review evidence is preserved: " + str(output))
        if args.command == "prepare-prompt":
            value = prepare_prompt(Path(args.prompt_file).read_text(encoding="utf-8-sig"))
        else:
            draft = review_template(Path(args.episode).resolve().parent, args.image, args.views_manifest)
            value = json.dumps(draft, ensure_ascii=False, indent=2) + "\n"
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
        result = {"file": str(output.resolve()), "sha256": sha256_file(output),
                  "status": "PREPARED_NOT_GENERATED" if args.command == "prepare-prompt" else "UNREVIEWED"}
    elif args.command == "save":
        result = normalize_asset(args.source, args.destination, prompt=Path(args.prompt_file).read_text(encoding="utf-8-sig"),
                                 references=args.reference, generation_version=args.version,
                                 generator=args.generator, replace=args.replace,
                                 episode_cfg=load_json(args.episode) if args.episode else None,
                                 episode_base=Path(args.episode).resolve().parent if args.episode else None)
    else:
        result = build_review_views(args.image, load_json(args.regions_json) if args.regions_json else {}, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
