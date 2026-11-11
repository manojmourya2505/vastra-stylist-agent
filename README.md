# Vastra stylist agent

The small server behind the Vastra Android app. It runs on your computer and does two jobs:

1. **Outfit suggestions.** Given the shopper's budget, occasion and catalogue items, it asks OpenAI's chat model (`gpt-4o-mini` by default) for outfits that fit the budget, and returns only items that exist in the catalogue.
2. **Virtual try-on.** Given the shopper's photo and the chosen pieces, it asks OpenAI's image model (`gpt-image-2`) to dress the shopper in those pieces from one angle, and returns the picture. The app asks for each angle separately.

## How it fits together

```
Android app  --HTTP-->  this server (port 8001)  --HTTPS-->  OpenAI
```

The app talks to this server over your local network. This server is the only part that holds the OpenAI key.

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/health` | Returns `{"ok": true, "model": ...}` when the server is running. |
| POST | `/suggest` | Takes the request, profile and items. Returns up to three outfits within budget. |
| POST | `/tryon` | Takes a base64 photo, a list of garment descriptions, an angle, optional garment photos and an optional reference image. Returns a base64 PNG. |

Try-on angles: `front`, `three-quarter`, `three-quarter-right`, `side`, `back-three-quarter`, `back`. If a view is blocked by the safety system, the server retries once with softer wording. If it's still blocked, it returns a clear error.

## Setup

1. Install Python 3.11 or newer.
2. Create a virtual environment and install the requirements:
   ```
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and paste your OpenAI key into `OPENAI_API_KEY`.
4. Run the server on all interfaces so the phone can reach it:
   ```
   .venv/bin/uvicorn server:app --host 0.0.0.0 --port 8001
   ```
5. Check it's up: `curl http://127.0.0.1:8001/health`

Keep the server running while you test the app. To run it in the background on a Mac, use `nohup ... &`.

## Cost

Each try-on view is one image request. The Android app asks for several views per outfit, so each outfit costs several image requests. Check your usage on the OpenAI dashboard.

## Security notes

- Keep `.env` out of version control. This repo's `.gitignore` already excludes it.
- The server accepts requests from anyone on the same network. Run it only on a network you trust, and don't expose port 8001 to the internet.
- Shopper photos are sent to OpenAI for each try-on. Don't use real people's photos without their consent.

## Files

- `server.py`: the FastAPI app with the `/suggest` and `/tryon` endpoints.
- `requirements.txt`: Python dependencies (FastAPI, uvicorn, OpenAI SDK, python-dotenv, Pydantic).
- `.env.example`: the settings you need to fill in.
