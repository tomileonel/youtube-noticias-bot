import os
import re
import logging
import time
import requests
import whisper
import google.generativeai as genai
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

# --- MÉTODO "CIRUJANO": API DE INVIDIOUS ---
def descargar_desde_invidious(video_id):
    print(f"DEBUG: 💉 Operando video {video_id} vía Invidious API...")
    
    # Lista de instancias activas de Invidious
    # Estas URLs nos dan el JSON con los datos del video
    instances = [
        "https://inv.tux.pizza",
        "https://invidious.jing.rocks",
        "https://vid.uff.ink",
        "https://yt.artemislena.eu",
        "https://invidious.projectsegfau.lt",
        "https://invidious.nerdvpn.de",
        "https://inv.zzls.xyz"
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    direct_audio_url = None

    # 1. Buscar una instancia que responda
    for base_url in instances:
        api_url = f"{base_url}/api/v1/videos/{video_id}"
        print(f"   📡 Consultando: {base_url}...")
        
        try:
            response = requests.get(api_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                # 2. Buscar el stream de audio dentro del JSON
                # Invidious nos da varios formatos, buscamos 'audio'
                if 'adaptiveFormats' in data:
                    for fmt in data['adaptiveFormats']:
                        # Buscamos audio/webm o audio/mp4
                        if 'type' in fmt and 'audio' in fmt['type']:
                            direct_audio_url = fmt['url']
                            print("   ✅ ¡Enlace de audio encontrado!")
                            break
                
                if direct_audio_url: break # Si encontramos link, salimos
            
        except Exception:
            continue

    if not direct_audio_url:
        print("❌ ERROR CRÍTICO: No se pudo extraer el audio de ninguna instancia.")
        return None

    # 3. Descargar el archivo desde el enlace directo
    output_filename = f"audio_{video_id}.webm" # Invidious suele dar webm, Whisper lo lee igual
    print("DEBUG: ⬇️ Descargando stream de audio...")
    
    try:
        with requests.get(direct_audio_url, stream=True, headers=headers) as r:
            r.raise_for_status()
            with open(output_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        # Verificar tamaño mínimo (10KB)
        if os.path.getsize(output_filename) < 10000:
            print("❌ Archivo vacío.")
            return None
            
        return output_filename

    except Exception as e:
        print(f"❌ Error descargando: {e}")
        return None

def transcribir_y_generar(video_id):
    # Paso 1: Descargar (Vía Invidious)
    audio_file = descargar_desde_invidious(video_id)
    
    if not audio_file:
        return None

    try:
        # Paso 2: Transcribir (Whisper Local)
        print("DEBUG: 🎧 Procesando audio con Whisper...")
        model = whisper.load_model("tiny") 
        result = model.transcribe(audio_file)
        texto_generado = result["text"]
        
        # Limpiar
        if os.path.exists(audio_file):
            os.remove(audio_file)
            
        return texto_generado

    except Exception as e:
        print(f"❌ Error Transcripción: {e}")
        if os.path.exists(audio_file):
            os.remove(audio_file)
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
            
            text = transcribir_y_generar(vid)
            
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