import os
import re
import logging
import subprocess
import whisper
import google.generativeai as genai
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

logging.basicConfig(level=logging.INFO)

# ================== BD ==================
db_string = os.getenv('DATABASE_URL')
if db_string and db_string.startswith("postgres://"):
    db_string = db_string.replace("postgres://", "postgresql://", 1)
if not db_string:
    raise RuntimeError("DATABASE_URL no definido")

engine = create_engine(db_string)
Base = declarative_base()

class VideoNoticia(Base):
    __tablename__ = 'noticias_youtube'
    id = Column(String, primary_key=True)
    titulo = Column(String)
    contenido_noticia = Column(Text)
    url_video = Column(String)
    fecha_proceso = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# ================== APIs ==================
youtube = build(
    'youtube',
    'v3',
    developerKey=os.getenv('YOUTUBE_API_KEY')
)

genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

# ================== HELPERS ==================
def limpiar_titulo(texto):
    texto = re.sub(r'[^\w\s\u00C0-\u00FF.,!¡?¿\-:;"\']', '', texto)
    return re.sub(r'\s+', ' ', texto).strip()

def get_latest_videos(channel_id, max_results=5):
    req = youtube.search().list(
        part="snippet",
        channelId=channel_id,
        maxResults=max_results,
        order="date",
        type="video"
    )
    res = req.execute()
    return [{'id': i['id']['videoId'], 'title': i['snippet']['title']}
            for i in res.get('items', [])]

# ================== AUDIO ==================
def descargar_audio(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    output = f"audio_{video_id}.mp3"

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "-o", output,
        url
    ]

    try:
        subprocess.run(cmd, check=True)
        return output if os.path.exists(output) else None
    except Exception as e:
        logging.error(f"yt-dlp error {video_id}: {e}")
        return None

# ================== TRANSCRIPCION ==================
def transcribir(video_id):
    audio = descargar_audio(video_id)
    if not audio:
        return None

    try:
        model = whisper.load_model("tiny")
        result = model.transcribe(audio, language="es")
        return result["text"]
    except Exception as e:
        logging.error(f"Whisper error: {e}")
        return None
    finally:
        if os.path.exists(audio):
            os.remove(audio)

# ================== IA NOTICIA ==================
def generate_news(text, title):
    if not text or len(text) < 100:
        return None

    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = f"""
Eres periodista profesional.
Escribe una noticia HTML para WordPress basada en el video titulado:
"{title}"

TRANSCRIPCIÓN:
{text[:25000]}

REGLAS:
- HTML limpio (<h2>, <p>, <ul>)
- Más de 300 palabras
- Español neutro
- Sin emojis
"""

    return model.generate_content(prompt).text

# ================== MAIN ==================
def main():
    cid = os.getenv('CHANNEL_ID')
    if not cid:
        raise RuntimeError("CHANNEL_ID no definido")

    session = Session()

    videos = get_latest_videos(cid)
    logging.info(f"Videos analizados: {len(videos)}")

    for v in videos:
        vid = v['id']
        title = limpiar_titulo(v['title'])

        if session.query(VideoNoticia).filter_by(id=vid).first():
            logging.info(f"YA EXISTE: {title}")
            continue

        logging.info(f"PROCESANDO: {title}")

        text = transcribir(vid)
        if not text:
            logging.warning("Transcripción fallida")
            continue

        html = generate_news(text, title)
        if not html:
            logging.warning("Generación IA fallida")
            continue

        post = VideoNoticia(
            id=vid,
            titulo=title,
            contenido_noticia=html,
            url_video=f"https://youtu.be/{vid}"
        )

        session.add(post)
        session.commit()
        logging.info("GUARDADO EN BD")

    session.close()

if __name__ == "__main__":
    main()
