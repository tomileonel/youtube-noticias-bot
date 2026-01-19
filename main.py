import os
import sys
import re
import logging
import glob
import yt_dlp  # LIBRERÍA NUEVA
from googleapiclient.discovery import build
import google.generativeai as genai
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

logging.basicConfig(level=logging.INFO)

# --- 1. CONFIGURACIÓN DE COOKIES ---
COOKIES_FILE = "cookies.txt"
cookies_env = os.getenv('YOUTUBE_COOKIES')

if cookies_env:
    with open(COOKIES_FILE, "w") as f:
        f.write(cookies_env)
    print("✅ Cookies cargadas desde el Secreto.")
else:
    print("⚠️ ADVERTENCIA: No hay cookies. Es probable que falle con videos sensibles.")

# --- 2. BASE DE DATOS ---
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

# --- 3. APIS ---
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

# --- 4. FUNCIÓN MAESTRA CON YT-DLP ---
def get_transcript_ytdlp(video_id):
    print(f"DEBUG: Intentando descargar subtítulos para {video_id} con yt-dlp...")
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Configuración para descargar SOLO texto (sin video)
    ydl_opts = {
        'skip_download': True,      # No bajar video
        'writesubtitles': True,     # Bajar subs manuales
        'writeautomaticsub': True,  # Bajar subs auto
        'subtitleslangs': ['es', 'es-419', 'en'], # Idiomas
        'outtmpl': f'/tmp/{video_id}', # Guardar en carpeta temporal
        'quiet': True,
        # Simulamos ser Android para evitar bloqueos
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }

    # Si tenemos cookies, se las pasamos
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Buscar el archivo generado (puede ser .vtt o .srv3)
        files = glob.glob(f"/tmp/{video_id}*")
        if not files:
            print("❌ ERROR: yt-dlp terminó pero no generó archivo de subtítulos.")
            return None
            
        filename = files[0] # Agarramos el primero que haya
        print(f"DEBUG: Archivo descargado: {filename}")
        
        # Leemos y limpiamos el archivo (quitamos tiempos y basura)
        clean_text = []
        with open(filename, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            for line in lines:
                # Filtrar líneas de tiempo, cabeceras y vacías
                line = line.strip()
                if not line: continue
                if '-->' in line: continue
                if line.startswith('WEBVTT') or line.startswith('Kind:') or line.startswith('Language:'): continue
                if line.isdigit(): continue
                
                # Quitar etiquetas raras <c> y duplicados
                line = re.sub(r'<[^>]+>', '', line)
                if clean_text and clean_text[-1] == line:
                    continue
                clean_text.append(line)
        
        # Borramos el archivo temporal
        for f in files:
            if os.path.exists(f): os.remove(f)
        
        return " ".join(clean_text)

    except Exception as e:
        print(f"❌ ERROR CRÍTICO YT-DLP: {e}")
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
            
            # USAMOS LA NUEVA FUNCIÓN
            text = get_transcript_ytdlp(vid)
            
            if not text:
                print(" -- Saltando (Imposible obtener texto)")
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
        # Borrar cookies al cerrar
        if os.path.exists(COOKIES_FILE):
            os.remove(COOKIES_FILE)
        session.close()

if __name__ == "__main__":
    main()