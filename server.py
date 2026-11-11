import base64
import json
import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from openai import BadRequestError
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
client = OpenAI()  # reads OPENAI_API_KEY from .env
app = FastAPI(title="Vastra stylist agent")


class Item(BaseModel):
    id: str
    name: str
    category: str
    style: str
    colour: str
    price: int
    stock: int


class SuggestRequest(BaseModel):
    request: str = ""
    occasion: str
    budget: int
    whenLabel: str
    items: list[Item]
    profile: dict | None = None


SYSTEM = """You are Vastra's stylist. The shopper describes an occasion in their own words.
Reply with JSON only, in this shape:
{"outfits": [{"label": "short name", "reason": "one sentence why it suits the occasion", "itemIds": ["..."]}]}
Rules:
- Return exactly three outfits when possible, with labels: one that uses most of the budget, one middle, one cheap.
- An outfit is either one Dresses item plus one Footwear item, or one Tops item plus one Bottoms item plus one Footwear item.
- Use only item ids from the list you are given, and only items with stock above 0.
- The total price of each outfit must be at most the budget.
- Choose colours that go together and a style that fits the occasion the shopper described.
- If the shopper profile is given: prefer colours and styles similar to what they bought before, do not suggest exactly what they already own or have in the bag, and keep their usual size in mind.
- If the shopper wrote nothing, judge only from the occasion, budget and profile.
"""

PATTERNS = [sorted(["Dresses", "Footwear"]), sorted(["Tops", "Bottoms", "Footwear"])]


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL}


@app.post("/suggest")
def suggest(req: SuggestRequest):
    stocked = [i for i in req.items if i.stock > 0]
    if not stocked:
        raise HTTPException(404, "No items in stock")
    by_id = {i.id: i for i in stocked}

    payload = {
        "request": req.request,
        "occasion": req.occasion,
        "budget": req.budget,
        "when": req.whenLabel,
        "items": [i.model_dump() for i in stocked],
        "profile": req.profile or {},
    }
    resp = client.chat.completions.create(
        model=MODEL,
        response_format={"type": "json_object"},
        temperature=0.4,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload)},
        ],
    )
    data = json.loads(resp.choices[0].message.content or "{}")

    # Only keep outfits the rules allow: known items, a valid pattern, within budget.
    outfits = []
    for o in data.get("outfits", []):
        ids = [x for x in o.get("itemIds", []) if x in by_id]
        if len(ids) != len(o.get("itemIds", [])) or len(set(ids)) != len(ids):
            continue
        cats = sorted(by_id[x].category for x in ids)
        if cats not in PATTERNS:
            continue
        if sum(by_id[x].price for x in ids) > req.budget:
            continue
        outfits.append({"label": str(o.get("label", "")), "reason": str(o.get("reason", "")), "itemIds": ids})

    if not outfits:
        raise HTTPException(422, "The stylist could not build a valid outfit")
    return {"outfits": outfits[:3], "model": MODEL}


class TryOnRequest(BaseModel):
    photo: str                    # base64 JPEG of the shopper
    garments: list[str] = []      # base64 JPEG catalog photos of the chosen pieces
    outfit: list[str]             # e.g. ["Silk midi dress in camel"]
    angle: str = "front"
    reference: str | None = None  # base64 JPEG of the approved front view, so other angles match it


ANGLE_TEXT = {
    "front": "The person faces the camera.",
    "side": "The person is shown in side profile.",
    "three-quarter": "The person's body is turned about 45 degrees to the left of the frame, with the face toward the camera.",
    "three-quarter-right": "The person's body is turned about 45 degrees to the right of the frame, with the face toward the camera.",
    "back-three-quarter": "The person is turned three-quarter away from the camera.",
    "back": "The person stands facing away from the camera, full body, relaxed neutral pose.",
}

# Softer wording, used when the safety system rejects a view.
SAFE_ANGLE_TEXT = {
    "back": "Full-length view from behind, standing straight, arms relaxed, the outfit fully covered and modest.",
    "back-three-quarter": "Full-length view from behind at a slight angle, standing straight, the outfit fully covered and modest.",
}


def build_prompt(outfit, angle, has_garments, has_reference, soft=False):
    angle_text = (SAFE_ANGLE_TEXT.get(angle) if soft else None) or ANGLE_TEXT[angle]
    parts = ["Edit image 1, a photo of a real person."]
    if has_garments:
        parts.append("The images after it show the exact garments to put on them: copy each garment's design, print, colour and cut exactly.")
    parts.append("Dress them in: " + ", ".join(outfit) + ".")
    if has_reference:
        parts.append("The last image is the approved front view: keep the outfit identical to it.")
    parts.append(angle_text)
    parts.append("Keep the same person exactly: same face, hair, skin tone, body shape and background. "
                 "Make the clothes fit naturally with realistic folds and the lighting of the original photo. "
                 "Do not add text, logos or extra people.")
    return " ".join(parts)


def generate(req, soft=False):
    images = [("photo.jpg", base64.b64decode(req.photo), "image/jpeg")]
    images += [("garment%d.jpg" % i, base64.b64decode(g), "image/jpeg") for i, g in enumerate(req.garments)]
    if req.reference:
        images.append(("reference.jpg", base64.b64decode(req.reference), "image/jpeg"))
    prompt = build_prompt(req.outfit, req.angle, bool(req.garments), bool(req.reference), soft)
    return client.images.edit(model=IMAGE_MODEL, image=images, prompt=prompt, size="1024x1536")


@app.post("/tryon")
def tryon(req: TryOnRequest):
    if req.angle not in ANGLE_TEXT:
        raise HTTPException(400, "angle must be one of: " + ", ".join(ANGLE_TEXT))
    if not req.photo or not req.outfit:
        raise HTTPException(400, "photo and outfit are needed")
    try:
        result = generate(req)
    except BadRequestError:
        try:
            result = generate(req, soft=True)
        except BadRequestError:
            raise HTTPException(422, "This view could not be made. Please try again.")
    image_b64 = result.data[0].b64_json
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, f"tryon-{req.angle}-{int(time.time())}.png"), "wb") as f:
        f.write(base64.b64decode(image_b64))
    return {"image": image_b64, "model": IMAGE_MODEL}
