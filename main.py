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

# --- DESCARGA VÍA RED COBALT (PROXY) ---
def descargar_con_cobalt(video_id):
    print(f"DEBUG: 🚀 Iniciando protocolo Cobalt para {video_id}...")
    
    # LISTA DE SERVIDORES ESPEJO (Si uno falla, probamos el siguiente)
    cobalt_instances = [
        "https://api.cobalt.tools/api/json",      # Oficial
        "https://co.wuk.sh/api/json",             # Muy estable
        "https://cobalt.oup.us/api/json",         # Alternativa US
        "https://api.server.cobalt.tools/api/json",
        "https://cobalt.xy24.eu/api/json",        # Europa
        "https://cobalt.angelofall.net/api/json", 
        "https://dl.khub.tel/api/json",
        "https://cobalt.q14.rocks/api/json"
    ]
    
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Configuración: Pedir solo audio MP3
    payload = {
        "url": youtube_url,
        "isAudioOnly": True,
        "aFormat": "mp3"
    }
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }

    direct_url = None

    # 1. Bucle para encontrar un servidor que funcione
    for instance in cobalt_instances:
        try:
            print(f"   📡 Probando servidor: {instance}")
            response = requests.post(instance, json=payload, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                # Cobalt puede devolver la URL en distintos formatos
                if 'url' in data:
                    direct_url = data['url']
                elif 'picker' in data:
                    for item in data['picker']:
                        if 'url' in item:
                            direct_url = item['url']
                            break
                
                if direct_url:
                    print("   ✅ ¡Enlace conseguido!")
                    break # Salimos del bucle
            
            # Si el status no es 200, probamos el siguiente
        except Exception:
            continue # Si da timeout, probamos el siguiente
            
    if not direct_url:
        print("❌ ERROR CRÍTICO: Ningún servidor de Cobalt pudo descargar el video.")
        return None

    # 2. Descargar el archivo MP3 desde el enlace conseguido
    output_filename = f"audio_{video_id}.mp3"
    print("DEBUG: ⬇️ Descargando archivo de audio final...")
    
    try:
        with requests.get(direct_url, stream=True) as r:
            r.raise_for_status()
            with open(output_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return output_filename
    except Exception as e:
        print(f"❌ Error descargando el MP3: {e}")
        return None

def transcribir_y_generar(video_id):
    # Paso 1: Descargar (Externo)
    audio_file = descargar_con_cobalt(video_id)
    
    if not audio_file:
        return None

    try:
        # Paso 2: Transcribir (Local en GitHub)
        print("DEBUG: 🎧 Procesando audio con Whisper (IA)...")
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
                print(" -- Saltando (No se pudo procesar)")
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