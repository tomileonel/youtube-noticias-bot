import os
import re
import logging
import time
import random
import yt_dlp
import whisper
import google.generativeai as genai
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

logging.basicConfig(level=logging.INFO)

# --- CONFIGURACIÓN DE COOKIES ---
COOKIES_FILE = "cookies.txt"
cookies_env = os.getenv('YOUTUBE_COOKIES')

print("--- DIAGNÓSTICO DE COOKIES ---")
if cookies_env:
    print(f"✅ Secreto YOUTUBE_COOKIES encontrado (Longitud: {len(cookies_env)} caracteres)")
    with open(COOKIES_FILE, "w") as f:
        f.write(cookies_env)
else:
    print("⚠️ NO se encontró YOUTUBE_COOKIES. El bot intentará entrar sin pase (posible bloqueo).")
print("------------------------------")

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

# --- SOLUCIÓN IA CON DISFRAZ DE IPHONE ---
def transcribir_con_ia(video_id):
    print(f"DEBUG: 🤖 La IA va a escuchar el video {video_id}...")
    
    output_filename = f"audio_{video_id}"
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    # CONFIGURACIÓN CLAVE: DISFRAZ DE IPHONE
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_filename,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'quiet': True,
        'no_warnings': True,
        # TRUCO: Usar cliente 'ios' (iPhone) para evitar el bloqueo "Sign in"
        'extractor_args': {'youtube': {'player_client': ['ios']}},
    }
    
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        # Pausa para no parecer robot ansioso
        time.sleep(random.uniform(3, 7))
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        final_audio = f"{output_filename}.mp3"
        
        if not os.path.exists(final_audio):
            return None

        # Transcribir usando Whisper
        print("DEBUG: 🎧 Procesando audio con Whisper...")
        model = whisper.load_model("tiny") 
        result = model.transcribe(final_audio)
        texto_generado = result["text"]
        
        # Limpiar archivo de audio
        if os.path.exists(final_audio):
            os.remove(final_audio)
            
        return texto_generado

    except Exception as e:
        print(f"❌ Error descarga/IA: {e}")
        if os.path.exists(f"{output_filename}.mp3"):
            os.remove(f"{output_filename}.mp3")
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
            
            text = transcribir_con_ia(vid)
            
            if not text:
                print(" -- Saltando (Bloqueo persistente)")
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
        if os.path.exists(COOKIES_FILE):
            os.remove(COOKIES_FILE)
        session.close()

if __name__ == "__main__":
    main()