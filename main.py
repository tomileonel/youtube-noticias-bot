import os
import re
import logging
import time
import random
import requests
import yt_dlp
import whisper
import google.generativeai as genai
from youtube_transcript_api import YouTubeTranscriptApi
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

logging.basicConfig(level=logging.INFO)

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
        request = youtube.search().list(part="snippet", channelId=channel_id, maxResults=5, order="date", type="video")
        response = request.execute()
        return [{'id': i['id']['videoId'], 'title': i['snippet']['title']} for i in response.get('items', [])]
    except Exception as e:
        print(f"Error API Youtube: {e}")
        return []

# --- PLAN A: OBTENER TRANSCRIPCIÓN DIRECTA (Sin Descarga) ---
def obtener_texto_directo(video_id):
    print(f"DEBUG: 📄 [PLAN A] Intentando obtener subtítulos existentes de {video_id}...")
    try:
        # Intenta obtener subtítulos en español o inglés (manuales o automáticos)
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['es', 'es-419', 'en'])
        
        # Unir todo el texto
        full_text = " ".join([entry['text'] for entry in transcript])
        print("   ✅ ¡Subtítulos encontrados! (Ahorramos descarga y Whisper)")
        return full_text
    except Exception:
        print("   ❌ No hay subtítulos disponibles o bloqueado. Pasando al Plan B...")
        return None

# --- PLAN B: DESCARGA CON PROXIES ROTATIVOS (Audio + Whisper) ---
def obtener_proxies_gratis():
    # Obtiene una lista fresca de proxies gratuitos
    try:
        url = "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"
        r = requests.get(url)
        proxies = r.text.strip().split('\n')
        return [p.strip() for p in proxies if p.strip()]
    except:
        return []

def descargar_con_proxy(video_id):
    print(f"DEBUG: 🎧 [PLAN B] Intentando descargar audio con Proxies...")
    
    proxies = obtener_proxies_gratis()
    # Tomamos 5 proxies al azar para probar
    proxies_to_try = random.sample(proxies, min(len(proxies), 10))
    
    url = f"https://www.youtube.com/watch?v={video_id}"
    output = f"audio_{video_id}"

    for proxy_url in proxies_to_try:
        print(f"   🛡️ Probando Proxy: {proxy_url}...")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output,
            'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}],
            'quiet': True,
            'proxy': f"http://{proxy_url}", # Inyectamos el proxy
            'socket_timeout': 10
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            final_file = f"{output}.mp3"
            if os.path.exists(final_file):
                print("   ✅ ¡Descarga exitosa con Proxy!")
                return final_file
        except Exception:
            continue # Si falla, probamos el siguiente

    print("❌ ERROR: Ningún proxy funcionó.")
    return None

def procesar_video(video_id):
    # 1. PLAN A: Texto directo
    texto = obtener_texto_directo(video_id)
    if texto: return texto

    # 2. PLAN B: Descarga Audio con Proxy + Whisper
    audio_file = descargar_con_proxy(video_id)
    
    if not audio_file: return None

    try:
        print("   🗣️ Transcribiendo audio con Whisper...")
        model = whisper.load_model("tiny")
        result = model.transcribe(audio_file)
        
        if os.path.exists(audio_file): os.remove(audio_file)
        return result["text"]
    except Exception as e:
        print(f"❌ Error Whisper: {e}")
        if os.path.exists(audio_file): os.remove(audio_file)
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
            
            text = procesar_video(vid)
            
            if not text:
                print(" -- Saltando (Imposible obtener info)")
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