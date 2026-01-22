import os
import re
import logging
import requests
import google.generativeai as genai
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
# Obtenemos la cadena de tokens y la convertimos en lista
SUPADATA_KEYS_RAW = os.getenv('SUPADATA_API_KEY', '')
SUPADATA_KEYS = [k.strip() for k in SUPADATA_KEYS_RAW.split(',') if k.strip()]

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

# --- LÓGICA SUPADATA CON ROTACIÓN ---
def get_transcript_supadata(video_id):
    print(f"   🔹 Solicitando a Supadata ({len(SUPADATA_KEYS)} tokens disponibles)...")
    
    url = f"https://api.supadata.ai/v1/youtube/transcript?videoId={video_id}&text=true"

    for index, api_key in enumerate(SUPADATA_KEYS):
        try:
            print(f"      🔑 Probando token #{index + 1}...")
            
            headers = {'x-api-key': api_key}
            response = requests.get(url, headers=headers, timeout=20)
            
            if response.status_code == 200:
                data = response.json()
                # Supadata suele devolver un campo 'content' con el texto
                text = data.get('content')
                
                if text:
                    print(f"      ✅ Éxito con token #{index + 1}")
                    return text
                else:
                    print("      ⚠️ Respuesta vacía de Supadata.")
            elif response.status_code in [402, 403, 429]:
                print(f"      ⚠️ Token #{index + 1} agotado o inválido (Code {response.status_code}). Rotando...")
                continue # Pasa al siguiente token del bucle
            else:
                print(f"      ❌ Error desconocido Supadata: {response.status_code}")
                
        except Exception as e:
            print(f"      ❌ Error de conexión: {e}")
            continue

    print("   ❌ FALLO TOTAL: Se probaron todos los tokens y ninguno funcionó.")
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
        print("--- INICIANDO CON SUPADATA ---")
        videos = get_latest_videos(CHANNEL_ID)
        
        for v in videos:
            vid = v['id']
            vtitle = limpiar_titulo(v['title'])
            
            if session.query(VideoNoticia).filter_by(id=vid).first():
                print(f"⏭️  {vtitle}")
                continue

            print(f"✨ Procesando: {vtitle}")
            
            # Llamada a Supadata
            transcript = get_transcript_supadata(vid)
            
            if transcript:
                print(f"   📜 Transcripción OK.")
                html = generate_news(transcript, vtitle)
                if html:
                    post = VideoNoticia(id=vid, titulo=vtitle, contenido_noticia=html, url_video=f"https://youtu.be/{vid}")
                    session.add(post)
                    session.commit()
                    print("   ✅ GUARDADO")
                else:
                    print("   ❌ Error Gemini")
            else:
                print("   ❌ OMITIDO (Sin transcripción)")

    except Exception as e:
        print(f"Error General: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    main()