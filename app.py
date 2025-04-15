import os
import uuid
import shutil
import base64
import tempfile
import threading
import time
import cv2
import subprocess
import traceback
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    send_from_directory,
    jsonify,
    flash,
    after_this_request,
)
import openai
from fpdf import FPDF
from docx import Document

# --- Config ---
UPLOAD_FOLDER = "static/uploads"
FRAMES_FOLDER = "static/frames"
ALLOWED_EXTENSIONS = {"mp4", "avi", "mov", "mkv"}
MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500MB

openai.api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["FRAMES_FOLDER"] = FRAMES_FOLDER
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(FRAMES_FOLDER, exist_ok=True)

# --- Thread-safe Status ---
from threading import Lock

processing_status = {}
status_lock = Lock()

def set_status(video_id, status):
    with status_lock:
        processing_status[video_id] = status

def get_status(video_id):
    with status_lock:
        return processing_status.get(video_id)

# --- Helper Functions ---

def allowed_file(filename):
    """Check if the file has an allowed extension."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_frames(video_path, frames_dir, num_frames=30):
    """Extract representative frames from the video."""
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = total_frames / fps if fps else 0
    if duration == 0:
        cap.release()
        return [], 0
    # 15 frames per minute, max 60
    minutes = max(1, int(duration // 60))
    num_frames = min(15 * minutes, 60)
    frame_indices = [
        int(i * total_frames / num_frames) for i in range(num_frames)
    ]
    frame_files = []
    for idx, frame_idx in enumerate(frame_indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if ret:
            frame_filename = f"frame_{uuid.uuid4().hex[:8]}.jpg"
            frame_path = os.path.join(frames_dir, frame_filename)
            cv2.imwrite(frame_path, frame)
            frame_files.append(frame_filename)
    cap.release()
    return frame_files, duration

def extract_audio(video_path, audio_path):
    """Extract audio from video using ffmpeg."""
    command = [
        "ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1", audio_path
    ]
    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

def transcribe_audio(audio_path):
    """Transcribe audio using OpenAI Whisper."""
    with open(audio_path, "rb") as audio_file:
        transcript = openai.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )
    return transcript.text

def describe_frames(frames_dir, frame_files):
    """Describe each frame using OpenAI Vision model."""
    descriptions = []
    for frame_file in frame_files:
        frame_path = os.path.join(frames_dir, frame_file)
        with open(frame_path, "rb") as img_file:
            frame_b64 = base64.b64encode(img_file.read()).decode("utf-8")
        response = openai.chat.completions.create(
            model="gpt-4.1",  # Correct vision model name
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this video frame."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{frame_b64}"
                            },
                        },
                    ],
                }
            ],
            max_tokens=50,
        )
        descriptions.append(response.choices[0].message.content.strip())
    return descriptions

def summarize(descriptions, transcript):
    """Summarize the video using frame descriptions and transcript."""
    prompt = (
        "You are given a video. Here are the frame descriptions:\n"
        + "\n".join(f"Frame {i+1}: {desc}" for i, desc in enumerate(descriptions))
        + "\n\nAnd here is the transcript of the audio:\n"
        + transcript
        + "\n\nSummarize the video content, combining visual and audio information."
    )
    response = openai.chat.completions.create(
        model="gpt-4.1",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
    )
    return response.choices[0].message.content.strip()

def generate_pdf(transcript, summary, output_path):
    """Generate a PDF file with transcript and summary."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, "Transcript", ln=True)
    pdf.multi_cell(0, 10, transcript)
    pdf.cell(0, 10, "Summary", ln=True)
    pdf.multi_cell(0, 10, summary)
    pdf.output(output_path)

def generate_docx(transcript, summary, output_path):
    """Generate a DOCX file with transcript and summary."""
    doc = Document()
    doc.add_heading("Transcript", level=1)
    doc.add_paragraph(transcript)
    doc.add_heading("Summary", level=1)
    doc.add_paragraph(summary)
    doc.save(output_path)

# --- Background Processing ---

def process_video(video_id, video_path, frames_dir):
    try:
        set_status(video_id, {"status": "Extracting frames...", "progress": 10})
        frame_files, duration = extract_frames(video_path, frames_dir)
        set_status(video_id, {"status": "Extracting audio...", "progress": 30})
        audio_path = os.path.join(frames_dir, f"{video_id}.wav")
        extract_audio(video_path, audio_path)
        set_status(video_id, {"status": "Transcribing audio...", "progress": 50})
        transcript = transcribe_audio(audio_path)
        set_status(video_id, {"status": "Describing frames...", "progress": 70})
        descriptions = describe_frames(frames_dir, frame_files)
        set_status(video_id, {"status": "Summarizing...", "progress": 90})
        summary = summarize(descriptions, transcript)
        set_status(video_id, {
            "status": "Completed",
            "progress": 100,
            "frame_files": frame_files,
            "descriptions": descriptions,
            "transcript": transcript,
            "summary": summary,
            "duration": duration,
            "video_path": video_path,
            "frames_dir": frames_dir,
        })
    except Exception as e:
        traceback.print_exc()
        set_status(video_id, {"status": f"Error: {str(e)}", "progress": -1})

# --- Routes ---

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        if "video" not in request.files:
            flash("No video file part", "danger")
            return redirect(request.url)
        file = request.files["video"]
        if file.filename == "":
            flash("No selected file", "danger")
            return redirect(request.url)
        if file and allowed_file(file.filename):
            video_id = uuid.uuid4().hex
            video_filename = f"{video_id}_{file.filename}"
            video_path = os.path.join(app.config["UPLOAD_FOLDER"], video_filename)
            file.save(video_path)
            frames_dir = os.path.join(app.config["FRAMES_FOLDER"], video_id)
            os.makedirs(frames_dir, exist_ok=True)
            # Start background processing
            threading.Thread(
                target=process_video, args=(video_id, video_path, frames_dir)
            ).start()
            return redirect(url_for("progress", video_id=video_id))
        else:
            flash("Invalid file type", "danger")
            return redirect(request.url)
    # List all processed videos
    videos = []
    for video_file in os.listdir(app.config["UPLOAD_FOLDER"]):
        if any(video_file.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            video_id = video_file.split("_")[0]
            status = get_status(video_id)
            if status and status.get("progress") == 100:
                videos.append({
                    "video_id": video_id,
                    "filename": video_file,
                    "duration": status.get("duration", 0),
                })
    return render_template("index.html", videos=videos)

@app.route("/progress/<video_id>")
def progress(video_id):
    return render_template("progress.html", video_id=video_id)

@app.route("/progress_status/<video_id>")
def progress_status(video_id):
    status = get_status(video_id) or {"status": "Not found", "progress": -1}
    return jsonify(status)

@app.route("/results/<video_id>")
def results(video_id):
    status = get_status(video_id)
    if not status or status.get("progress") != 100:
        flash("Processing not complete or failed.", "danger")
        return redirect(url_for("index"))
    video_file = None
    for f in os.listdir(app.config["UPLOAD_FOLDER"]):
        if f.startswith(video_id):
            video_file = f
            break
    if not video_file:
        flash("Video file not found.", "danger")
        return redirect(url_for("index"))
    return render_template(
        "results.html",
        video_id=video_id,
        video_file=video_file,
        frame_descs=zip(status["frame_files"], status["descriptions"]),
        transcript=status["transcript"],
        summary=status["summary"],
        duration=status["duration"],
    )

@app.route("/download/<video_id>/<filetype>")
def download_file(video_id, filetype):
    status = get_status(video_id)
    if not status or status.get("progress") != 100:
        flash("Processing not complete or failed.", "danger")
        return redirect(url_for("index"))
    transcript = status["transcript"]
    summary = status["summary"]
    temp_dir = tempfile.mkdtemp()
    if filetype == "pdf":
        output_path = os.path.join(temp_dir, f"{video_id}.pdf")
        generate_pdf(transcript, summary, output_path)
        filename = f"{video_id}.pdf"
    elif filetype == "docx":
        output_path = os.path.join(temp_dir, f"{video_id}.docx")
        generate_docx(transcript, summary, output_path)
        filename = f"{video_id}.docx"
    else:
        flash("Invalid file type", "danger")
        shutil.rmtree(temp_dir)
        return redirect(url_for("results", video_id=video_id))

    @after_this_request
    def cleanup(response):
        shutil.rmtree(temp_dir)
        return response

    return send_from_directory(temp_dir, filename, as_attachment=True)

@app.route("/static/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/static/frames/<video_id>/<filename>")
def frame_file(video_id, filename):
    return send_from_directory(os.path.join(app.config["FRAMES_FOLDER"], video_id), filename)

# --- Templates ---
# Place index.html, progress.html, results.html in /templates

if __name__ == "__main__":
    app.run(debug=True)
