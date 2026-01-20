import os
import re
import logging
import time
import whisper
import google.generativeai as genai
from pytubefix import YouTube
from pytubefix.cli import on_progress
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

logging.basicConfig(level=logging.INFO)

# --- BASE DE DATOS ---
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

# --- NUEVO MÉTODO DE DESCARGA (Anti-Bloqueo) ---
def descargar_y_transcribir(video_id):
    print(f"DEBUG: 🚀 Intentando descargar {video_id} con Pytubefix...")
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Nombre temporal
    output_audio = f"audio_{video_id}" 

    try:
        # Usamos el cliente 'ANDROID' que suele saltarse el "Sign in"
        yt = YouTube(url, client='ANDROID')
        
        print(f"   Título: {yt.title}")
        
        # Obtener solo el audio (m4a/webm) con menor peso
        audio_stream = yt.streams.get_audio_only()
        
        if not audio_stream:
            print("❌ No se encontró audio disponible.")
            return None

        print("   ⬇️ Descargando...")
        # Pytubefix descarga el archivo con su extensión correcta
        archivo_descargado = audio_stream.download(filename=output_audio)
        
        # --- TRANSCRIPCIÓN ---
        print("   🎧 Cargando Whisper (IA)...")
        # Usamos 'tiny' para que no explote la memoria de GitHub
        model = whisper.load_model("tiny")
        
        print("   🗣️ Transcribiendo...")
        # Whisper acepta m4a/mp3/webm directamente
        result = model.transcribe(archivo_descargado)
        texto = result["text"]

        # Limpiar archivo
        if os.path.exists(archivo_descargado):
            os.remove(archivo_descargado)
            
        return texto

    except Exception as e:
        print(f"❌ Error en Proceso: {e}")
        # Limpieza de emergencia (pytube suele bajar .m4a)
        if os.path.exists(f"{output_audio}.m4a"): os.remove(f"{output_audio}.m4a")
        if os.path.exists(f"{output_audio}.mp4"): os.remove(f"{output_audio}.mp4")
        return None

def generate_news(text, title):
    if not text or len(text) < 50: return None
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
            
            text = descargar_y_transcribir(vid)
            
            if not text:
                print(" -- Saltando (Error)")
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
        session.close()

if __name__ == "__main__":
    main()