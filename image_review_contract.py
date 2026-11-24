"""Shared image instructions/evidence checks; no automated visual PASS."""
from pathlib import Path
from copy import deepcopy
from hashlib import sha256
from episode_core import load_json, safe_child, sha256_file

CONTRACT = "action_anatomy_continuity_v1"
CHECKS = {
    "hand_arm_ownership": "Trace each visible hand to its owner: hand, wrist, elbow, shoulder. Record occlusion, extra/fused/disconnected parts.",
    "weapon_continuity": "Trace each weapon end to end; compare axis, thickness and position before/after the gripping hand and across hidden intervals.",
    "grip_contact_support": "Observe fingers/thumb around the grip, weapon contact and shield grip/forearm support; no floating, piercing or detached equipment.",
    "occlusion_camera": "Observe visible connections in a natural rear, side or over-shoulder view. Natural occlusion is allowed; no forced face visibility or finger count.",
    "character_cut_continuity": "Compare clothing, build, equipment, anatomical holding hand, travel/attack direction and spatial layout with references and adjacent cuts.",
    "revision_recheck": "After an edit reopen the whole new original and details; compare prior defect, regressions and affected adjacent cuts. Record unresolved issues."
}
PROMPT_GUIDANCE = """[action_anatomy_continuity_v1]
Plan the action first: actor, anatomical left/right hand, tool, target, direction and opponent response. Choose a natural camera, including rear, side or over-the-shoulder views. Never impose a face-visible percentage or twist a body to show a face.
Connect each visible hand to the correct owner's wrist, elbow and shoulder. Preserve body front/back orientation; do not merge arms or split a body across doors or pillars.
Draw every weapon as one continuous object from end to end. Its centerline, thickness, position and perspective must agree before and after a gripping hand and across occluded sections. Preserve the intended shape; not every weapon is straight.
Make fingers/thumb, palm and handle grip coherent, with credible hand/forearm support for shields and actual contact/distance for the described attack, block or evasion. No floating grips, unintended penetration or disconnected support.
Normal overlap, foreshortening, fists, gloves, hidden fingers and cropping are allowed when ownership and visible structure remain understandable. Do not force all fingers, both feet or both faces into every image.
Preserve reference clothing, build, equipment and continuity of the anatomical holding hand, travel/attack direction, positions and surroundings across cuts. Camera changes must not silently switch ownership or equipment.
For an approved repair retain the intended action and unaffected design. The reviewer must reopen the full new original and native details, inspect the changed area and regressions, and bind observations to current/reference hashes. Unresolved or unseen structure is not a visual PASS.
[/action_anatomy_continuity_v1]"""


def prepare_prompt(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("A scene-specific prompt is required")
    return text.rstrip() + ("\n" if PROMPT_GUIDANCE in text else "\n\n" + PROMPT_GUIDANCE + "\n")


def generation_metadata(prompt):
    return {"review_contract": CONTRACT, "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_guidance_present": PROMPT_GUIDANCE in prompt}


def generation_records(base):
    path = Path(base) / "generation_records.json"
    if not path.exists():
        return []
    document = load_json(path)
    assets = document.get("assets") if isinstance(document, dict) else None
    if not isinstance(assets, list) or any(not isinstance(x, dict) for x in assets):
        raise ValueError("generation_records.assets must be a list of records")
    return assets


def latest_generation(records, name):
    return next((x for x in reversed(records) if x.get("file") == name), {})


def review_template(base, image, views_manifest=None):
    """Unreviewed draft only; existing review and generation history stay intact."""
    base, image = Path(base).resolve(), Path(image).resolve()
    name = image.relative_to(base).as_posix()
    current = sha256_file(image)
    generation = latest_generation(generation_records(base), name)
    if generation.get("sha256") != current:
        generation = {}
    evidence = [{"kind": "full", "file": name, "sha256": current,
                 "image_sha256": current, "viewed": False, "observations": ""}]
    original = generation.get("preserved_original")
    if original:
        source = safe_child(base, original)
        evidence.append({"kind": "original", "file": source.relative_to(base).as_posix(),
                         "sha256": sha256_file(source), "image_sha256": current,
                         "viewed": False, "observations": ""})
    if views_manifest:
        manifest = load_json(views_manifest)
        if manifest.get("image_sha256") != current:
            raise ValueError("Review views belong to a different image hash")
        for view in manifest.get("views", []):
            if view.get("label") == "full":
                continue
            detail = Path(view["file"]).resolve()
            detail.relative_to(base)
            if sha256_file(detail) != view.get("sha256"):
                raise ValueError("Stale detail view: " + view["file"])
            evidence.append({"kind": "detail", "file": detail.relative_to(base).as_posix(),
                             "sha256": view["sha256"], "image_sha256": current,
                             "bounds": view.get("bounds"), "viewed": False, "observations": ""})
    old_review = {}
    old_path = base / "image_review.json"
    if old_path.exists():
        document = load_json(old_path)
        old_review = next((row for row in document.get("images", [])
                           if isinstance(row, dict) and row.get("file") == name and row.get("sha256") == current), {})
    references = dict(old_review.get("reference_hashes", {}))
    for item in generation.get("references", []):
        path = Path(item.get("path", "")).resolve()
        if path.is_relative_to(base) and path.is_file():
            references[path.relative_to(base).as_posix()] = item.get("sha256")
    return {"file": name, "sha256": current, "generation_version": generation.get("generation_version", old_review.get("generation_version")),
            "review_contract": CONTRACT, "status": "UNREVIEWED", "reviewed_at": "",
            "full_observations": "", "detail_observations": "", "issues": deepcopy(old_review.get("issues", [])),
            "character_bible_sha256": sha256_file(base / "character_bible.json") if (base / "character_bible.json").is_file() else "",
            "storyboard_sha256": sha256_file(base / "storyboard.json") if (base / "storyboard.json").is_file() else "",
            "reference_hashes": references, "previous_sha256": generation.get("replaced_sha256"),
            "visual_checks": {key: {"status": "UNREVIEWED", "observations": "", "guidance": value}
                              for key, value in CHECKS.items()}, "viewed_evidence": evidence}


def contract_errors(base, name, review, generation):
    """Check record shape/status/hashes. A human/tool reviewer judges actual images."""
    if not (review.get("review_contract") or generation.get("review_contract")):
        return []  # Historical evidence is not automatically upgraded or erased.
    errors = []
    def fail(message):
        errors.append(name + ": visual contract: " + message)
    if review.get("review_contract") != CONTRACT:
        fail("current action/anatomy review is required")
    if generation.get("review_contract"):
        if generation["review_contract"] != CONTRACT:
            fail("unsupported generation review contract")
        prompt = generation.get("prompt", "")
        if not isinstance(prompt, str) or PROMPT_GUIDANCE not in prompt:
            fail("actual generation prompt lacks the prepared shared guidance")
        elif generation.get("prompt_sha256") != sha256(prompt.encode("utf-8")).hexdigest():
            fail("stored generation prompt hash is stale")
        if generation.get("sha256") != review.get("sha256"):
            fail("current generation and reviewed image hashes differ")
        if generation.get("generation_version") != review.get("generation_version"):
            fail("generation_version differs from current generation history")
    checks = review.get("visual_checks")
    checks = checks if isinstance(checks, dict) else {}
    for key in CHECKS:
        item = checks.get(key, {})
        if (not isinstance(item, dict) or item.get("status") not in ("PASS", "NOT_APPLICABLE")
                or not isinstance(item.get("observations"), str) or not item["observations"].strip()):
            fail(key + ": actual observations or a concrete not-applicable reason required")
    if review.get("issues") != []:
        fail("unresolved issues remain or issues is missing")
    if generation.get("replaced_sha256"):
        if review.get("previous_sha256") != generation["replaced_sha256"]:
            fail("repair review must identify the previous image hash")
        item = checks.get("revision_recheck")
        if not isinstance(item, dict) or item.get("status") != "PASS":
            fail("edited image needs whole-image and detail reinspection")
    references = review.get("reference_hashes", {})
    if not isinstance(references, dict):
        fail("reference_hashes must identify the viewed reference/adjacent images")
    else:
        for filename, digest in references.items():
            try:
                reference = safe_child(base, filename)
                if not reference.is_file() or sha256_file(reference) != digest:
                    raise ValueError("missing or stale reference")
            except (OSError, ValueError, TypeError) as exc:
                fail("reference evidence is invalid: " + str(filename) + ": " + str(exc))
    evidence = review.get("viewed_evidence")
    if not isinstance(evidence, list):
        evidence = []
        fail("viewed_evidence must list the actual opened full image and native details")
    kinds = set()
    for item in evidence:
        if not isinstance(item, dict):
            fail("invalid viewed evidence")
            continue
        kind = item.get("kind")
        if (item.get("viewed") is not True or not isinstance(item.get("observations"), str)
                or not item["observations"].strip()):
            fail("evidence needs actual viewing and specific observations")
        if item.get("image_sha256") != review.get("sha256"):
            fail("view evidence belongs to a different image hash")
        try:
            path = safe_child(base, item.get("file", ""))
            if not path.is_file() or sha256_file(path) != item.get("sha256"):
                raise ValueError("missing or stale file")
            if kind == "full" and item["sha256"] != review.get("sha256"):
                raise ValueError("full evidence is not the current image")
            if kind == "original" and generation.get("source_sha256") and item["sha256"] != generation["source_sha256"]:
                raise ValueError("original evidence is not the current generated source")
            if kind == "detail":
                bounds = item.get("bounds")
                if (not isinstance(bounds, list) or len(bounds) != 4
                        or any(type(x) is not int for x in bounds)
                        or not 0 <= bounds[0] < bounds[2] or not 0 <= bounds[1] < bounds[3]):
                    raise ValueError("native detail bounds are required")
                from PIL import Image
                with Image.open(safe_child(base, name)) as full, Image.open(path) as detail:
                    if bounds[2] > full.width or bounds[3] > full.height:
                        raise ValueError("detail bounds exceed the current image")
                    expected = full.convert("RGB").crop(tuple(bounds))
                    if detail.size != expected.size or detail.convert("RGB").tobytes() != expected.tobytes():
                        raise ValueError("detail is not a native crop of the current image")
            kinds.add(kind)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            fail("view evidence is invalid: " + str(exc))
    for kind in ("full", "detail"):
        if kind not in kinds:
            fail("actual " + kind + " view evidence required")
    if generation.get("preserved_original") and "original" not in kinds:
        fail("full generated original view evidence required")
    return errors
