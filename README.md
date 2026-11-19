# Stream Clips

Browser-based YouTube livestream clipping MVP. Paste a public YouTube URL, choose a topic and clip count, and generate vertical 9:16 MP4 highlights with burned-in captions and posting copy. No manual video upload is required.

## Deploy on Railway

1. Connect this repository (`vivekdinga123-create/stream-clips`, branch `main`) to the Railway service.
2. Use the repository-root `Dockerfile`.
3. Mount a persistent Railway volume at `/data`.
4. Expose the service's HTTP port and use `/health` as the healthcheck.
5. Allow sufficient memory, CPU, disk and processing time for yt-dlp, FFmpeg and faster-whisper.

The app listens on `0.0.0.0:$PORT` (default 8080). Generated clips are stored under `/data`.

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080`.

## MVP limitations

- Highlight selection currently uses transcript heuristics, not an LLM or live chat/reaction analysis.
- Jobs are held in process memory; a service restart clears job status. Use one worker/replica for this MVP.
- Processing a full livestream can take a long time and requires enough disk, RAM and CPU. YouTube availability, age/region restrictions, livestream archive state and yt-dlp compatibility can affect results.
- Use only videos you are authorized to download and repurpose. Respect YouTube terms and creators' rights.

This repository is an MVP scaffold, not a guarantee that every livestream can be processed.