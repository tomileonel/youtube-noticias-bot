import os
import re
import logging
import glob
import webvtt
import google.generativeai as genai
from yt_dlp import YoutubeDL
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- CONFIGURACIÓN ---
CHANNEL_ID = os.getenv('CHANNEL_ID')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
DATABASE_URL = os.getenv('DATABASE_URL')

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL: exit()

engine = create_engine(DATABASE_URL)
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

youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
genai.configure(api_key=GEMINI_API_KEY)

def limpiar_titulo(texto):
    texto_limpio = re.sub(r'[^\w\s\u00C0-\u00FF.,!¡?¿\-:;"\']', '', texto)
    return re.sub(r'\s+', ' ', texto_limpio).strip()

def get_latest_videos(channel_id):
    try:
        req = youtube.search().list(part="snippet", channelId=channel_id, maxResults=5, order="date", type="video")
        res = req.execute()
        return [{'id': i['id']['videoId'], 'title': i['snippet']['title']} for i in res.get('items', [])]
    except Exception as e:
        print(f"Error API Youtube: {e}")
        return []

# --- EL NUEVO MÉTODO CON YT-DLP ---
def obtener_subtitulos_ytdlp(video_id):
    print(f"   🔹 Ejecutando yt-dlp para {video_id}...")
    
    url = f"https://www.youtube.com/watch?v={video_id}"
    output_base = f"subs_{video_id}"
    
    # Configuración "Anti-Bloqueo"
    ydl_opts = {
        'skip_download': True,      # ¡NO bajar video! Solo info
        'writeautomaticsub': True,  # Bajar subs auto-generados
        'writesubtitles': True,     # Bajar subs manuales si hay
        'sublangs': ['es', 'en'],   # Preferir español, luego inglés
        'outtmpl': output_base,     # Nombre del archivo
        'quiet': True,
        'no_warnings': True,
        
        # EL TRUCO MAESTRO: Simular ser una App de Android
        # Esto salta la mayoría de bloqueos de "Sign in required"
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios'] 
            }
        }
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        # yt-dlp guarda como 'subs_ID.es.vtt' o 'subs_ID.en.vtt'
        # Buscamos cualquier .vtt que haya generado
        archivos_vtt = glob.glob(f"{output_base}*.vtt")
        
        if not archivos_vtt:
            print("      ❌ yt-dlp no encontró subtítulos.")
            return None
            
        # Tomamos el primero que encuentre (idealmente será español por la prioridad)
        archivo_elegido = archivos_vtt[0]
        print(f"      ✅ Subtítulo descargado: {archivo_elegido}")
        
        # Extraer texto limpio del VTT
        texto_limpio = []
        for caption in webvtt.read(archivo_elegido):
            texto_limpio.append(caption.text)
            
        full_text = " ".join(texto_limpio).replace('\n', ' ')
        
        # Limpieza de archivos temporales
        for f in archivos_vtt:
            if os.path.exists(f): os.remove(f)
            
        return full_text

    except Exception as e:
        print(f"      ❌ Error crítico yt-dlp: {e}")
        return None

def generate_news(text, title):
    modelos = ['gemini-pro', 'gemini-1.5-flash']
    for m in modelos:
        try:
            model = genai.GenerativeModel(m)
            prompt = f"Eres periodista. Escribe noticia HTML (+300 palabras) sobre '{title}'. TRANSCRIPCION: {text[:25000]}"
            return model.generate_content(prompt).text
        except: continue
    return None

def main():
    session = Session()
    try:
        if not CHANNEL_ID: return
        print("--- INICIANDO ---")
        videos = get_latest_videos(CHANNEL_ID)
        
        for v in videos:
            vid = v['id']
            vtitle = limpiar_titulo(v['title'])
            
            if session.query(VideoNoticia).filter_by(id=vid).first():
                print(f"⏭️  {vtitle}")
                continue

            print(f"✨ Procesando: {vtitle}")
            
            # Usamos yt-dlp directamente
            transcript = obtener_subtitulos_ytdlp(vid)
            
            if transcript:
                print(f"   📜 Texto extraído ({len(transcript)} caracteres).")
                html = generate_news(transcript, vtitle)
                if html:
                    post = VideoNoticia(id=vid, titulo=vtitle, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
                    session.add(post)
                    session.commit()
                    print("   ✅ GUARDADO EN BD")
                else:
                    print("   ❌ Error generando noticia (Gemini)")
            else:
                print("   ❌ FALLO TOTAL: No se pudo obtener texto.")

    except Exception as e:
        print(f"Error General: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()