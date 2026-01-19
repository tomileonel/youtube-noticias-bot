import os
import sys
import re
import logging

# Truco de instalación local
sys.path.insert(0, os.getcwd())

from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

logging.basicConfig(level=logging.INFO)

# --- CONFIGURACIÓN DE COOKIES (LA CLAVE DEL ÉXITO) ---
COOKIES_FILE = "cookies.txt"
cookies_content = os.getenv('YOUTUBE_COOKIES')

if cookies_content:
    # Creamos el archivo físico de cookies para que la librería lo use
    with open(COOKIES_FILE, "w") as f:
        f.write(cookies_content)
    print("✅ Cookies cargadas correctamente.")
else:
    print("⚠️ ADVERTENCIA: No se encontró el secreto YOUTUBE_COOKIES. Algunos videos fallarán.")

# --- BD ---
db_string = os.getenv('DATABASE_URL')
if db_string and db_string.startswith("postgres://"):
    db_string = db_string.replace("postgres://", "postgresql://", 1)
if not db_string: exit()

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

def limpiar_titulo(texto):
    texto_limpio = re.sub(r'[^\w\s\u00C0-\u00FF.,!¡?¿\-:;"\']', '', texto)
    return re.sub(r'\s+', ' ', texto_limpio).strip()

def get_latest_videos(channel_id):
    try:
        request = youtube.search().list(part="snippet", channelId=channel_id, maxResults=5, order="date", type="video")
        response = request.execute()
        return [{'id': i['id']['videoId'], 'title': i['snippet']['title']} for i in response.get('items', [])]
    except Exception as e:
        print(f"Error API Youtube: {e}")
        return []

def get_transcript(video_id):
    print(f"DEBUG: Buscando subtítulos para {video_id}...")
    try:
        # AQUI USAMOS LAS COOKIES PARA SALTAR EL BLOQUEO
        # Si existe el archivo cookies.txt, lo usa. Si no, intenta sin él.
        cookies_path = COOKIES_FILE if os.path.exists(COOKIES_FILE) else None
        
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, cookies=cookies_path)
        
        try:
            transcript = transcript_list.find_transcript(['es', 'es-419', 'en'])
        except:
            print("DEBUG: Usando autogenerados...")
            transcript = next(iter(transcript_list))
        
        fetched = transcript.fetch()
        return " ".join([i['text'] for i in fetched])
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return None

def generate_news(text, title):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    Eres periodista. Crea noticia HTML para WordPress sobre: '{title}'.
    TRANSCRIPCION: {text[:25000]}
    REGLAS: HTML (<h2>,<p>,<ul>). Tono profesional. +300 palabras.
    """
    try:
        return model.generate_content(prompt).text
    except Exception as e:
        print(f"Error Gemini: {e}")
        return None

def main():
    session = Session()
    try:
        cid = os.getenv('CHANNEL_ID')
        if not cid: return

        videos = get_latest_videos(cid)
        print(f"Analizando {len(videos)} videos recientes...")

        for v in videos:
            vid, vtitle_raw = v['id'], v['title']
            vtitle_clean = limpiar_titulo(vtitle_raw)

            if session.query(VideoNoticia).filter_by(id=vid).first():
                print(f"[YA EXISTE] {vtitle_clean}")
                continue

            print(f"[NUEVO] Procesando: {vtitle_clean}")
            text = get_transcript(vid)
            
            if not text:
                print(" -- Saltando (Bloqueado o sin texto)")
                continue

            html = generate_news(text, vtitle_clean)
            if html:
                post = VideoNoticia(id=vid, titulo=vtitle_clean, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
                session.add(post)
                session.commit()
                print(" -- ¡GUARDADO EN BD!")
            
    except Exception as e:
        print(f"Error General: {e}")
    finally:
        # Borrar cookies al terminar por seguridad
        if os.path.exists(COOKIES_FILE):
            os.remove(COOKIES_FILE)
        session.close()

if __name__ == "__main__":
    main()