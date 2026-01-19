import os
import logging
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

# Configuración
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

# --- BASE DE DATOS ---
db_string = os.getenv('DATABASE_URL')

# Arreglo automático por si el link dice "postgres://" en vez de "postgresql://"
if db_string and db_string.startswith("postgres://"):
    db_string = db_string.replace("postgres://", "postgresql://", 1)

if not db_string:
    raise ValueError("Falta DATABASE_URL")

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

# --- APIS ---
youtube = build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_API_KEY'))
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

def get_latest_video(channel_id):
    try:
        request = youtube.search().list(part="snippet", channelId=channel_id, maxResults=1, order="date", type="video")
        response = request.execute()
        if not response['items']: return None, None
        item = response['items'][0]
        return item['id']['videoId'], item['snippet']['title']
    except Exception as e:
        logger.error(f"Error YouTube: {e}")
        return None, None

def get_transcript(video_id):
    try:
        t = YouTubeTranscriptApi.get_transcript(video_id, languages=['es', 'es-419', 'en'])
        return " ".join([i['text'] for i in t])
    except:
        return None

def generate_news(text, title):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    Actúa como periodista. Crea una noticia HTML para WordPress basada en este video: '{title}'.
    TRANSCRIPCION: {text[:30000]}
    REGLAS: Usa etiquetas <h2>, <p>, <ul>. Tono profesional. Mínimo 300 palabras.
    """
    try:
        return model.generate_content(prompt).text
    except:
        return "Error generando texto."

def main():
    session = Session()
    try:
        cid = os.getenv('CHANNEL_ID')
        vid, vtitle = get_latest_video(cid)

        if not vid: 
            print("No hay videos.")
            return

        if session.query(VideoNoticia).filter_by(id=vid).first():
            print(f"Video ya procesado: {vtitle}")
            return

        print(f"Procesando nuevo video: {vtitle}")
        text = get_transcript(vid)
        if not text:
            print("Sin subtítulos.")
            return

        html = generate_news(text, vtitle)

        post = VideoNoticia(id=vid, titulo=vtitle, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
        session.add(post)
        session.commit()
        print("¡Noticia guardada con éxito!")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()