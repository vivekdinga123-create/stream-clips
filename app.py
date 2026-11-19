import os, re, json, uuid, subprocess, threading
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request as HTTPRequest
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
import yt_dlp

DATA=Path("/data"); DATA.mkdir(exist_ok=True)
JOBS={}
LOCK=threading.Lock()
COOKIE_FILE=DATA/"youtube_cookies.txt"
app=FastAPI(title="Stream Clips")
app.mount("/static",StaticFiles(directory="static"),name="static")

class Request(BaseModel):
    url: HttpUrl
    topic: str="Gaming"
    clips: int=3

def ydl_auth_options():
    return {"cookiefile": str(COOKIE_FILE)} if COOKIE_FILE.is_file() else {}

@app.post("/api/settings/youtube-cookies")
async def save_youtube_cookies(request: HTTPRequest):
    expected=os.getenv("COOKIE_SETUP_PASSWORD","")
    supplied=request.headers.get("x-admin-password","")
    if not expected or not supplied or not __import__("hmac").compare_digest(expected,supplied):
        raise HTTPException(401,"Invalid setup password")
    raw=await request.body()
    if not raw or len(raw)>2_000_000:
        raise HTTPException(400,"Cookie file is empty or too large (2 MB maximum)")
    try: content=raw.decode("utf-8-sig")
    except UnicodeDecodeError: raise HTTPException(400,"Upload a UTF-8 Netscape cookies.txt file")
    valid=[]
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#"): continue
        fields=line.split("\\t")
        if len(fields)>=7 and ("youtube.com" in fields[0].lower() or "google.com" in fields[0].lower()):
            valid.append(line)
    if not valid:
        raise HTTPException(400,"No YouTube/Google cookies found. Export cookies in Netscape cookies.txt format.")
    temp=DATA/"youtube_cookies.tmp"
    temp.write_text(content,encoding="utf-8")
    os.chmod(temp,0o600)
    temp.replace(COOKIE_FILE)
    os.chmod(COOKIE_FILE,0o600)
    return {"ok":True,"message":"YouTube cookies saved securely on the server."}

@app.get("/api/settings/youtube-cookies")
def youtube_cookie_status():
    return {"configured":COOKIE_FILE.is_file()}

def command(args):
    p=subprocess.run(args,capture_output=True,text=True)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout)[-2500:])
    return p.stdout

def update(j,**kw):
    with LOCK: JOBS[j].update(kw)

def srt_time(sec):
    ms=int(sec*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def worker(j,req):
    folder=DATA/j; folder.mkdir(parents=True,exist_ok=True)
    try:
        url=str(req.url)
        update(j,status="downloading",progress=4,message="Reading video details and downloading audio")
        ydl={"format":"bestaudio/best","outtmpl":str(folder/"audio.%(ext)s"),"quiet":True,"no_warnings":True,**ydl_auth_options()}
        with yt_dlp.YoutubeDL(ydl) as y: info=y.extract_info(url,download=True)
        audio=next(folder.glob("audio.*"))
        update(j,status="transcribing",progress=22,message="Transcribing stream audio")
        from faster_whisper import WhisperModel
        model=WhisperModel(os.getenv("WHISPER_MODEL","base"),device="cpu",compute_type="int8")
        segs,_=model.transcribe(str(audio),vad_filter=True)
        transcript=[{"start":float(s.start),"end":float(s.end),"text":s.text.strip()} for s in segs]
        if not transcript: raise RuntimeError("No speech was detected in this video.")
        (folder/"transcript.json").write_text(json.dumps(transcript,ensure_ascii=False),encoding="utf-8")
        duration=float(info.get("duration") or transcript[-1]["end"])
        update(j,status="selecting",progress=48,message="Finding promising highlight moments")
        terms=re.compile(r"\b(no way|oh my|holy|wow|crazy|insane|bro|let'?s go|what|seriously|amazing|funny|scream|scared|win|fail|clutch|chat)\b",re.I)
        candidates=[]
        for seg in transcript:
            start=max(0,seg["start"]-12); end=min(duration,seg["end"]+18)
            if end-start<12: continue
            words=" ".join(x["text"] for x in transcript if x["start"]<end and x["end"]>start)
            score=len(terms.findall(words))*3+min(len(words)/220,3)
            candidates.append((score,start,end,words))
        candidates.sort(reverse=True)
        chosen=[]
        for c in candidates:
            if all(abs(c[1]-x[1])>50 for x in chosen): chosen.append(c)
            if len(chosen)>=max(1,min(req.clips,5)): break
        if not chosen:
            for n in range(max(1,min(req.clips,5))):
                start=max(0,min(duration-25,n*max(25,duration/max(1,req.clips))))
                chosen.append((0,start,min(duration,start+35),""))
        results=[]
        for n,(_,start,end,words) in enumerate(sorted(chosen,key=lambda x:x[1]),1):
            update(j,status="rendering",progress=50+int(45*n/len(chosen)),message=f"Rendering vertical clip {n} of {len(chosen)}")
            pattern=str(folder/f"segment{n}.%(ext)s")
            opts={"format":"bestvideo[height<=1080]+bestaudio/best[height<=1080]","outtmpl":pattern,"download_sections":[f"*{start:.2f}-{end:.2f}"],"force_keyframes_at_cuts":True,"merge_output_format":"mp4","quiet":True,"no_warnings":True,**ydl_auth_options()}
            with yt_dlp.YoutubeDL(opts) as y: y.download([url])
            sources=list(folder.glob(f"segment{n}.*"))
            if not sources: raise RuntimeError(f"Could not locate downloaded segment {n}")
            src=sources[0]; out=folder/f"clip-{n}.mp4"
            # Generate simple timed subtitle cues from transcript, relative to clip start.
            cues=[x for x in transcript if x["start"]<end and x["end"]>start]
            srt=folder/f"clip-{n}.srt"
            lines=[]
            for idx,x in enumerate(cues,1):
                a=max(0,x["start"]-start); b=max(a+0.2,min(end-start,x["end"]-start))
                lines += [str(idx),f"{srt_time(a)} --> {srt_time(b)}",x["text"],""]
            srt.write_text("\n".join(lines),encoding="utf-8")
            # Portrait crop and burned-in captions.
            vf="scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
            if lines:
                safe=str(srt).replace("\\","/").replace(":","\\:")
                vf+=f",subtitles='{safe}':force_style='FontName=Arial,FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=150'"
            command(["ffmpeg","-y","-i",str(src),"-vf",vf,"-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-b:a","128k","-movflags","+faststart",str(out)])
            results.append({"title":f"{req.topic} highlight #{n}","description":f"{req.topic} moment worth watching! #shorts #{re.sub('[^A-Za-z0-9]','',req.topic)}","start_seconds":round(start),"end_seconds":round(end),"download":f"/clips/{j}/clip-{n}.mp4"})
        (folder/"clips.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
        (folder/"POSTING.md").write_text("\n\n".join(f"## {x['title']}\n{x['description']}" for x in results),encoding="utf-8")
        update(j,status="complete",progress=100,message="Clips are ready",clips=results)
    except Exception as e:
        update(j,status="error",message=str(e)[-1800:])

@app.get("/")
def index(): return FileResponse("static/index.html")
@app.get("/health")
def health(): return {"ok":True}
@app.post("/api/jobs")
def create(req:Request):
    jid=uuid.uuid4().hex[:12]
    with LOCK: JOBS[jid]={"status":"queued","progress":0,"message":"Queued"}
    threading.Thread(target=worker,args=(jid,req),daemon=True).start()
    return {"job_id":jid}
@app.get("/api/jobs/{jid}")
def status(jid):
    if jid not in JOBS: raise HTTPException(404,"Job not found")
    return JOBS[jid]
@app.get("/clips/{jid}/{name}")
def clip(jid,name):
    if not re.fullmatch(r"clip-[1-5]\.mp4",name): raise HTTPException(404,"Not found")
    p=DATA/jid/name
    if not p.is_file(): raise HTTPException(404,"Clip not found")
    return FileResponse(p,media_type="video/mp4",filename=name)
