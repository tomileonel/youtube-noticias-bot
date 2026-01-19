import os
import re
import logging
import time
import random
import requests
import whisper
import google.generativeai as genai
from googleapiclient.discovery import build
from sqlalchemy import create_engine, Column, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime

logging.basicConfig(level=logging.INFO)

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

# --- SOLUCIÓN VÍA COBALT API (INTERMEDIARIO VIP) ---
def descargar_audio_cobalt(video_id):
    print(f"DEBUG: 🚀 Solicitando audio a Cobalt para {video_id}...")
    
    # Lista de servidores públicos de Cobalt (si uno falla, probamos otro)
    cobalt_instances = [
        "https://api.cobalt.tools/api/json",
        "https://co.wuk.sh/api/json",
        "https://cobalt.oup.us/api/json",
        "https://api.server.cobalt.tools/api/json"
    ]
    
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    payload = {
        "url": youtube_url,
        "isAudioOnly": True,
        "aFormat": "mp3"
    }

    direct_url = None

    # 1. Obtener el Link de descarga
    for instance in cobalt_instances:
        try:
            print(f"   📡 Probando servidor: {instance}")
            response = requests.post(instance, json=payload, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                # Cobalt puede devolver la URL en 'url' o 'picker'
                if 'url' in data:
                    direct_url = data['url']
                    break
                elif 'picker' in data: # A veces devuelve una lista
                    for item in data['picker']:
                        if 'url' in item:
                            direct_url = item['url']
                            break
                    if direct_url: break
            
            # Si llegamos aqui es que falló esta instancia
            print(f"   ⚠️ Falló instancia {instance} (Status: {response.status_code})")
            time.sleep(1) # Esperar un poco antes de probar la siguiente
            
        except Exception as e:
            print(f"   ❌ Error conectando a {instance}: {str(e)}")
            continue

    if not direct_url:
        print("❌ ERROR: Ningún servidor de Cobalt pudo procesar el video.")
        return None

    # 2. Descargar el archivo MP3
    print("DEBUG: ⬇️ Descargando archivo de audio final...")
    output_filename = f"audio_{video_id}.mp3"
    
    try:
        with requests.get(direct_url, stream=True) as r:
            r.raise_for_status()
            with open(output_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        # Verificar que se bajó bien (mínimo 10KB)
        if os.path.getsize(output_filename) < 10000:
            print("❌ Error: El archivo descargado es demasiado pequeño (error de Cobalt).")
            os.remove(output_filename)
            return None
            
        return output_filename
        
    except Exception as e:
        print(f"❌ Error descargando el MP3: {e}")
        if os.path.exists(output_filename): os.remove(output_filename)
        return None

def transcribir_con_ia(video_id):
    # Paso 1: Descargar
    audio_file = descargar_audio_cobalt(video_id)
    
    if not audio_file:
        return None

    try:
        # Paso 2: Transcribir
        print("DEBUG: 🎧 Procesando audio con Whisper...")
        model = whisper.load_model("tiny") 
        result = model.transcribe(audio_file)
        texto_generado = result["text"]
        
        # Limpiar
        os.remove(audio_file)
        return texto_generado

    except Exception as e:
        print(f"❌ Error IA: {e}")
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
            
            text = transcribir_con_ia(vid)
            
            if not text:
                print(" -- Saltando (Error en proceso)")
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