#!/usr/bin/env python3
"""
V7 Stage B — Vision Stack
Runs 4 vision models in parallel on the input image.
Outputs measured dimensions and part detections for Stage C to reconcile against A.

Models:
  1. Grounding DINO → bounding boxes + part labels + confidence
  2. SAM3           → pixel masks per part
  3. DepthPro       → metric depth map in meters (absolute scale)
  4. DepthAnything3 → relative depth (cross-validation)

Output: stage_b.json with per-component measurements
"""

import os
import sys
import time
import threading
import json
import numpy as np
from PIL import Image

_DIR = os.path.dirname(os.path.abspath(__file__))
_SIMREADY_DIR = os.path.dirname(_DIR)                          # simready_assets/
_TOOLS_DIR = os.path.dirname(_SIMREADY_DIR)                    # scripts/tools/
_V3_MODELS_DIR = os.path.join(_TOOLS_DIR, "v3", "models")
_MODELS_DIR = os.path.join(_TOOLS_DIR, "models")

# Model paths
DINO_CONFIG  = os.path.join(_MODELS_DIR, "GroundingDINO/groundingdino/config/GroundingDINO_SwinT_OGC.py")
DINO_WEIGHTS = os.path.join(_MODELS_DIR, "GroundingDINO/weights/groundingdino_swint_ogc.pth")
DEPTH_PRO_DIR  = os.path.join(_V3_MODELS_DIR, "ml-depth-pro")
DEPTH_PRO_CKPT = os.path.join(DEPTH_PRO_DIR, "checkpoints", "depth_pro.pt")
SAM3_DIR = os.path.join(_V3_MODELS_DIR, "sam3")
SAM3_BPE = os.path.join(SAM3_DIR, "sam3", "assets", "bpe_simple_vocab_16e6.txt.gz")
DA3_DIR  = os.path.join(_V3_MODELS_DIR, "depth-anything-3")


# ═══════════════════════════════════════════════════════════════════
# MODEL RUNNERS — each in its own thread
# ═══════════════════════════════════════════════════════════════════

def run_dino(image_path, part_names, results):
    """Grounding DINO: open-vocabulary detection using part names from Stage A."""
    try:
        import torch
        from groundingdino.util.inference import load_model, load_image, predict

        model = load_model(DINO_CONFIG, DINO_WEIGHTS)
        _, image = load_image(image_path)

        # Build prompt from Stage A part names
        prompt = " . ".join(part_names[:10])  # DINO supports up to ~10 tokens

        with torch.no_grad():
            boxes, logits, phrases = predict(
                model=model, image=image, caption=prompt,
                box_threshold=0.25, text_threshold=0.20,
            )

        detections = []
        for box, logit, phrase in zip(boxes, logits, phrases):
            area = float(box[2] * box[3])
            if area < 0.5:
                detections.append({
                    "label": phrase,
                    "confidence": round(float(logit), 4),
                    "box_cxcywh": [round(float(x), 4) for x in box.tolist()],
                })

        del model
        torch.cuda.empty_cache()
        results["dino"] = {"status": "success", "detections": detections, "count": len(detections)}
        print(f"  [B1] DINO ✓ — {len(detections)} detections")
    except Exception as e:
        results["dino"] = {"status": "error", "error": str(e)}
        print(f"  [B1] DINO ✗ — {e}")


def run_sam3(image_path, part_names, results):
    """SAM3: segmentation masks per part using text prompts."""
    try:
        import torch

        if SAM3_DIR not in sys.path:
            sys.path.insert(0, SAM3_DIR)

        from sam3 import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor

        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True

        with torch.autocast("cuda", dtype=torch.bfloat16):
            model = build_sam3_image_model(bpe_path=SAM3_BPE)
            processor = Sam3Processor(model, confidence_threshold=0.25)

            image = Image.open(image_path).convert("RGB")
            width, height = image.size
            state = processor.set_image(image)

            masks_out = []
            # Use generic prompts + part names
            prompts = list(set(["drawer", "cabinet door", "handle", "knob", "cabinet body", "leg"] + part_names[:6]))

            for prompt_text in prompts:
                try:
                    processor.reset_all_prompts(state)
                    state = processor.set_text_prompt(state=state, prompt=prompt_text)

                    scores = state.get("scores", torch.tensor([]))
                    boxes  = state.get("boxes", torch.tensor([]))
                    masks  = state.get("masks", torch.tensor([]))

                    for i in range(len(scores)):
                        score = float(scores[i])
                        if score < 0.25:
                            continue
                        box  = boxes[i].cpu().tolist() if len(boxes) > i else None
                        mask = masks[i].cpu().numpy() if len(masks) > i else None
                        masks_out.append({
                            "label": prompt_text,
                            "score": round(score, 4),
                            "bbox": box,
                            "mask_area": int(mask.sum()) if mask is not None else 0,
                            "mask": mask,
                        })
                except Exception:
                    continue

        del model, processor
        torch.cuda.empty_cache()
        results["sam3"] = {"status": "success", "masks": masks_out, "image_size": (width, height)}
        print(f"  [B2] SAM3 ✓ — {len(masks_out)} masks")
    except Exception as e:
        results["sam3"] = {"status": "error", "error": str(e)}
        print(f"  [B2] SAM3 ✗ — {e}")


def run_depth_pro(image_path, results):
    """DepthPro: metric depth in meters."""
    try:
        import torch

        if DEPTH_PRO_DIR not in sys.path:
            sys.path.insert(0, DEPTH_PRO_DIR)

        import depth_pro
        from depth_pro.depth_pro import DEFAULT_MONODEPTH_CONFIG_DICT
        from dataclasses import replace

        config = replace(DEFAULT_MONODEPTH_CONFIG_DICT, checkpoint_uri=DEPTH_PRO_CKPT)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model, transform = depth_pro.create_model_and_transforms(config=config, device=device)
        model.eval()

        image = Image.open(image_path).convert("RGB")
        img_w, img_h = image.size
        input_tensor = transform(image).to(device)

        with torch.no_grad():
            prediction = model.infer(input_tensor)

        depth_map = prediction["depth"].cpu().numpy()
        focal_px  = float(prediction["focallength_px"]) if "focallength_px" in prediction else None

        del model
        torch.cuda.empty_cache()

        results["depth_pro"] = {
            "status": "success",
            "depth_map": depth_map,
            "focal_px": focal_px,
            "image_size": (img_w, img_h),
            "depth_range": (round(float(depth_map.min()), 3), round(float(depth_map.max()), 3)),
        }
        print(f"  [B3] DepthPro ✓ — focal={focal_px:.1f}px depth={depth_map.min():.2f}-{depth_map.max():.2f}m")
    except Exception as e:
        results["depth_pro"] = {"status": "error", "error": str(e)}
        print(f"  [B3] DepthPro ✗ — {e}")


def run_depth_anything(image_path, results):
    """DepthAnything3: relative depth for cross-validation."""
    try:
        import torch

        if DA3_DIR not in sys.path:
            sys.path.insert(0, DA3_DIR)

        from depth_anything_3.api import DepthAnything3

        model = DepthAnything3.from_pretrained("depth-anything/DA3-LARGE")
        model = model.eval()
        if torch.cuda.is_available():
            model = model.cuda()

        image = Image.open(image_path)
        prediction = model.inference([image])

        da_depth = None
        for attr in ["depth", "disp"]:
            val = getattr(prediction, attr, None)
            if val is not None:
                da_depth = val[0]
                if hasattr(da_depth, "cpu"):
                    da_depth = da_depth.cpu().numpy()
                else:
                    da_depth = np.array(da_depth)
                break

        del model
        torch.cuda.empty_cache()

        if da_depth is not None:
            results["depth_anything"] = {
                "status": "success",
                "depth_map": da_depth,
                "depth_range": (round(float(da_depth.min()), 3), round(float(da_depth.max()), 3)),
            }
            print(f"  [B4] DA3 ✓ — shape={da_depth.shape}")
        else:
            results["depth_anything"] = {"status": "error", "error": "No depth output"}
            print(f"  [B4] DA3 ✗ — no depth output")
    except Exception as e:
        results["depth_anything"] = {"status": "error", "error": str(e)}
        print(f"  [B4] DA3 ✗ — {e}")


# ═══════════════════════════════════════════════════════════════════
# MATH RECONCILIATION
# ═══════════════════════════════════════════════════════════════════

def _iou(box_cxcywh, mask, img_w, img_h):
    cx, cy, w, h = box_cxcywh
    x1 = int((cx - w/2) * img_w); y1 = int((cy - h/2) * img_h)
    x2 = int((cx + w/2) * img_w); y2 = int((cy + h/2) * img_h)

    m = mask
    while m.ndim > 2: m = m[0]
    mh, mw = m.shape
    sx, sy = mw/img_w, mh/img_h
    mx1, my1 = max(0,int(x1*sx)), max(0,int(y1*sy))
    mx2, my2 = min(mw,int(x2*sx)), min(mh,int(y2*sy))

    box_mask = np.zeros((mh, mw), dtype=bool)
    box_mask[my1:my2, mx1:mx2] = True
    m_bool = m > 0.5
    inter = np.logical_and(box_mask, m_bool).sum()
    union = np.logical_or(box_mask, m_bool).sum()
    return float(inter/union) if union > 0 else 0.0


def reconcile(raw_results, image_path, stage_a_parts):
    """Combine DINO + SAM3 + DepthPro + DA3 into per-component measurements."""
    image   = Image.open(image_path).convert("RGB")
    img_w, img_h = image.size
    img_arr = np.array(image)

    dino = raw_results.get("dino", {})
    sam3 = raw_results.get("sam3", {})
    dp   = raw_results.get("depth_pro", {})
    da   = raw_results.get("depth_anything", {})

    dino_dets  = dino.get("detections", [])
    sam3_masks = sam3.get("masks", [])
    dp_depth   = dp.get("depth_map")
    dp_focal   = dp.get("focal_px")
    da_depth   = da.get("depth_map")

    components = []

    for det in dino_dets:
        cx, cy, w, h = det["box_cxcywh"]
        x1 = int((cx-w/2)*img_w); y1 = int((cy-h/2)*img_h)
        x2 = int((cx+w/2)*img_w); y2 = int((cy+h/2)*img_h)
        px_w = x2-x1; px_h = y2-y1

        comp = {
            "label": det["label"],
            "dino_confidence": det["confidence"],
            "pixel_bbox": [x1, y1, x2, y2],
            "pixel_width": px_w,
            "pixel_height": px_h,
        }

        # SAM3 overlap
        best_iou, best_mask = 0.0, None
        for sm in sam3_masks:
            if sm.get("mask") is None: continue
            iou = _iou(det["box_cxcywh"], sm["mask"], img_w, img_h)
            if iou > best_iou:
                best_iou = iou; best_mask = sm
        if best_iou > 0.15:
            comp["sam3_label"] = best_mask["label"]
            comp["sam3_iou"]   = round(best_iou, 3)
            comp["confirmed"]  = True
        else:
            comp["confirmed"] = det["confidence"] > 0.4

        # DepthPro metric dimensions
        if dp_depth is not None and dp_focal and dp_focal > 0:
            dh, dw = dp_depth.shape
            sx, sy = dw/img_w, dh/img_h
            dx1,dy1 = max(0,int(x1*sx)), max(0,int(y1*sy))
            dx2,dy2 = min(dw,int(x2*sx)), min(dh,int(y2*sy))
            roi = dp_depth[dy1:dy2, dx1:dx2]
            if roi.size > 0:
                avg_d = float(np.median(roi))
                comp["depth_m"]           = round(avg_d, 4)
                comp["measured_width_mm"]  = round(px_w * avg_d / dp_focal * 1000, 1)
                comp["measured_height_mm"] = round(px_h * avg_d / dp_focal * 1000, 1)
                comp["b_confidence"]       = round(min(det["confidence"] + 0.1, 1.0), 2)

        # DA3 relative depth at center
        if da_depth is not None:
            dah, daw = da_depth.shape
            ccx = min(daw-1, max(0, int((x1+x2)/2 * daw/img_w)))
            ccy = min(dah-1, max(0, int((y1+y2)/2 * dah/img_h)))
            comp["da_relative_depth"] = round(float(da_depth[ccy, ccx]), 4)

        # Pixel color sampling
        rx1,ry1 = max(0,x1), max(0,y1)
        rx2,ry2 = min(img_w,x2), min(img_h,y2)
        if rx2>rx1 and ry2>ry1:
            roi_px = img_arr[ry1:ry2, rx1:rx2]
            avg_rgb = roi_px.mean(axis=(0,1)) / 255.0
            comp["sampled_rgb"] = [round(float(avg_rgb[i]),3) for i in range(3)]

        components.append(comp)

    # Overall dimensions
    confirmed = [c for c in components if c.get("confirmed")]
    overall   = {}
    if confirmed and dp_focal:
        all_x1 = [c["pixel_bbox"][0] for c in confirmed]
        all_y1 = [c["pixel_bbox"][1] for c in confirmed]
        all_x2 = [c["pixel_bbox"][2] for c in confirmed]
        all_y2 = [c["pixel_bbox"][3] for c in confirmed]
        largest = max(confirmed, key=lambda c: c.get("measured_width_mm",0))
        if "depth_m" in largest:
            d = largest["depth_m"]
            overall["total_width_mm"]  = round((max(all_x2)-min(all_x1)) * d / dp_focal * 1000, 1)
            overall["total_height_mm"] = round((max(all_y2)-min(all_y1)) * d / dp_focal * 1000, 1)

    # Part counts
    counts = {}
    for c in confirmed:
        counts[c["label"]] = counts.get(c["label"], 0) + 1

    # Row ratios
    row_ratios = {}
    doors   = [c for c in confirmed if "door" in c["label"]]
    drawers = [c for c in confirmed if "drawer" in c["label"]]
    if doors and drawers:
        avg_dh  = np.mean([c["pixel_height"] for c in doors])
        avg_drh = np.mean([c["pixel_height"] for c in drawers])
        total   = avg_dh + avg_drh
        if total > 0:
            row_ratios["door_ratio"]   = round(float(avg_dh/total), 3)
            row_ratios["drawer_ratio"] = round(float(avg_drh/total), 3)

    return {
        "components": components,
        "confirmed_count": len(confirmed),
        "part_counts": counts,
        "overall_dims": overall,
        "row_ratios": row_ratios,
        "model_status": {
            "dino": dino.get("status","missing"),
            "sam3": sam3.get("status","missing"),
            "depth_pro": dp.get("status","missing"),
            "depth_anything": da.get("status","missing"),
        }
    }


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def run_stage_b(image_path: str, stage_a: dict, output_path: str = None) -> dict:
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"  STAGE B — Vision Stack")
    print(f"  Image: {os.path.basename(image_path)}")
    print(f"{'='*60}")

    # Get part names from Stage A for targeted DINO/SAM3 prompts
    part_names = [p["part"].replace("_", " ") for p in stage_a.get("parts", [])]
    print(f"  Parts from A: {part_names}")

    raw = {}

    # Run DINO and SAM3 sequentially (both need GPU, avoid OOM)
    print(f"\n  Running DINO...")
    run_dino(image_path, part_names, raw)

    print(f"  Running SAM3...")
    run_sam3(image_path, part_names, raw)

    print(f"  Running DepthPro...")
    run_depth_pro(image_path, raw)

    print(f"  Running DepthAnything3...")
    run_depth_anything(image_path, raw)

    # Reconcile
    print(f"\n  Reconciling...")
    result = reconcile(raw, image_path, stage_a.get("parts", []))

    elapsed = time.time() - t0
    print(f"\n  ✓ Stage B complete — {result['confirmed_count']} confirmed parts, {elapsed:.1f}s")
    print(f"  Part counts: {result['part_counts']}")
    print(f"  Overall dims: {result['overall_dims']}")
    if result['row_ratios']:
        print(f"  Row ratios: {result['row_ratios']}")
    print(f"{'='*60}")

    # Strip numpy arrays before saving
    def clean(obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items() if k != "depth_map" and k != "mask"}
        if isinstance(obj, list):
            return [clean(x) for x in obj]
        return obj

    saveable = clean(result)

    if output_path:
        with open(output_path, "w") as f:
            json.dump(saveable, f, indent=2)
        print(f"  Saved: {output_path}")

    # Return with numpy arrays intact for Stage C
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--stage_a", required=True, help="Path to stage_a.json")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    with open(args.stage_a) as f:
        stage_a = json.load(f)

    name    = os.path.splitext(os.path.basename(args.image))[0]
    out_dir = os.path.join(os.path.dirname(args.image), f"{name}_v7")
    os.makedirs(out_dir, exist_ok=True)
    output  = args.output or os.path.join(out_dir, "stage_b.json")

    run_stage_b(args.image, stage_a, output)
